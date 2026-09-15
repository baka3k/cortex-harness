//! Message-scan detector registry — port của `tools/common/message_detectors/`.
//!
//! Mỗi detector hiểu cú pháp của một ngôn ngữ để tách hai thứ:
//! 1. **sender** — tên hàm/method bao quanh dòng chứa call-site (stateful qua
//!    file: mỗi dòng mới có thể "mở" một sender mới).
//! 2. **message fields** — `(message_name, receiver, payload, explanation)`
//!    từ callee name + args của một call.
//!
//! Contract byte-exact với Python implementation: regex khớp `^` tại stripped
//! line, keyword set được lower-case so với callee name (last segment sau
//! `.:>`), short-circuit theo thứ tự `keyword → sendmessage/postmessage →
//! registerreceiver/startactivity/startservice → broadcast-class → looks_endpoint`.
//!
//! Đây là phần determinism-critical nhất của message-scan. Khi parity với
//! Python fail, đây là nơi đầu tiên cần kiểm tra.

use once_cell::sync::Lazy;
use regex::Regex;
use std::collections::BTreeSet;

/// Stripped call args với depth/quote-aware split.
pub type ArgList<'a> = &'a [String];

/// Output của `extract_fields` — match Python `MessageFields`.
pub type MessageFields = (String, String, String, String);

/// Detectors mà Python `_DETECTORS` map quản lý (17 ngôn ngữ). `swift` được
/// Python bind về `GenericMessageDetector`.
pub trait MessageDetector: Send + Sync {
    fn parser_name(&self) -> &'static str;
    fn keywords(&self) -> &BTreeSet<&'static str>;

    /// Trả về tên sender cho dòng đã strip — `None` nếu dòng không mở một
    /// function/method scope mới.
    fn extract_sender(&self, line_text: &str) -> Option<String>;

    /// Tách (name, receiver, payload, explanation) từ callee + args.
    fn extract_fields(&self, callee_name: &str, args: ArgList<'_>) -> MessageFields;
}

// ── helpers shared across detectors ──────────────────────────────────────

/// `_STRING_PATTERN` Python: khớp string `"..."` hoặc `'...'` không crossing
/// quote khác. Trả về unquoted value (đã `.strip()` như Python) nếu input
/// chứa ít nhất một string literal — `None` nếu không.
pub fn unquote(text: &str) -> Option<String> {
    static DOUBLE: Lazy<Regex> =
        Lazy::new(|| Regex::new(r#""([^"\\]*(?:\\.[^"\\]*)*)""#).expect("static regex"));
    static SINGLE: Lazy<Regex> =
        Lazy::new(|| Regex::new(r"'([^'\\]*(?:\\.[^'\\]*)*)'").expect("static regex"));
    let trimmed = text.trim();
    if let Some(caps) = DOUBLE.captures(trimmed) {
        return caps.get(1).map(|m| m.as_str().trim().to_string());
    }
    if let Some(caps) = SINGLE.captures(trimmed) {
        return caps.get(1).map(|m| m.as_str().trim().to_string());
    }
    None
}

/// Python slice semantics — truncate a `String` to at most `max_chars`
/// Unicode scalar values (NOT bytes). Mirrors `s[:n]` cho string Python.
fn truncate_chars(mut value: String, max_chars: usize) -> String {
    if value.chars().count() > max_chars {
        value = value.chars().take(max_chars).collect();
    }
    value
}

/// Python `re.sub(r"\s+", " ", message_name)` — collapse internal whitespace
/// runs thành 1 space (Generic detector only).
fn collapse_whitespace(value: &str) -> String {
    value.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// `_IDENTIFIER_PATTERN.fullmatch` — một identifier đơn, không whitespace.
fn is_identifier(token: &str) -> bool {
    let mut chars = token.chars();
    let Some(first) = chars.next() else {
        return false;
    };
    if !(first.is_ascii_alphabetic() || first == '_') {
        return false;
    }
    for ch in chars {
        if !(ch.is_ascii_alphanumeric() || ch == '_' || ch == ':' || ch == '.') {
            return false;
        }
    }
    true
}

/// `looks_endpoint` Python — true nếu token là unquoted identifier không
/// thuộc `null/true/false/self/this` và length ≤ 120.
pub fn looks_endpoint(text: &str) -> bool {
    let token = text.trim();
    if token.is_empty() {
        return false;
    }
    if unquote(token).is_some() {
        return true;
    }
    if token.len() > 120 {
        return false;
    }
    let lower = token.to_ascii_lowercase();
    if matches!(lower.as_str(), "null" | "true" | "false" | "this" | "self") {
        return false;
    }
    is_identifier(token)
}

// ── GenericMessageDetector (base fallback + swift) ────────────────────────

pub struct GenericMessageDetector;

impl MessageDetector for GenericMessageDetector {
    fn parser_name(&self) -> &'static str {
        "generic"
    }

    fn keywords(&self) -> &BTreeSet<&'static str> {
        static KW: Lazy<BTreeSet<&'static str>> = Lazy::new(|| {
            BTreeSet::from([
                "emit",
                "publish",
                "send",
                "post",
                "notify",
                "dispatch",
                "broadcast",
                "sendmessage",
                "postmessage",
            ])
        });
        &KW
    }

    fn extract_sender(&self, _line_text: &str) -> Option<String> {
        None
    }

    fn extract_fields(&self, callee_name: &str, args: ArgList<'_>) -> MessageFields {
        let lower = callee_name.to_ascii_lowercase();
        let first = args.first().map(String::as_str).unwrap_or("");
        let second = args.get(1).map(String::as_str).unwrap_or("");
        let third = args.get(2).map(String::as_str).unwrap_or("");
        let mut message_name = unquote(first)
            .unwrap_or_else(|| first.trim().to_string())
            .trim()
            .to_string();
        if message_name.is_empty() {
            message_name = callee_name.to_string();
        }
        message_name = collapse_whitespace(&message_name);
        message_name = truncate_chars(message_name, 220);
        let mut receiver = String::new();
        let mut payload: String = if !second.trim().is_empty() {
            second.trim().to_string()
        } else {
            first.trim().to_string()
        };
        let explanation = format!("{callee_name}() inferred by generic detector");
        if matches!(lower.as_str(), "sendmessage" | "postmessage") {
            receiver = first.trim().to_string();
            payload = if !second.trim().is_empty() {
                second.trim().to_string()
            } else if !third.trim().is_empty() {
                third.trim().to_string()
            } else {
                String::new()
            };
            if unquote(first).is_none() && !second.is_empty() {
                let override_name = unquote(second).unwrap_or_else(|| message_name.clone());
                message_name = collapse_whitespace(&override_name);
                message_name = truncate_chars(message_name, 220);
            }
        } else if looks_endpoint(second) {
            receiver = second.trim().chars().take(220).collect();
        } else if looks_endpoint(third) {
            receiver = third.trim().chars().take(220).collect();
        }
        payload = truncate_chars(payload, 400);
        (message_name, receiver, payload, explanation)
    }
}

// ── cplus ────────────────────────────────────────────────────────────────

pub struct CPlusMessageDetector;

static C_STYLE_FN: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"^\s*(?:template\s*<[^>]+>\s*)?(?:[\w:\[\]<>*&~]+\s+){0,8}([A-Za-z_~][\w:]*)\s*\([^;{}]*\)\s*(?:const\b)?\s*(?:\{|$)").expect("static regex")
});

impl MessageDetector for CPlusMessageDetector {
    fn parser_name(&self) -> &'static str {
        "cplus"
    }
    fn keywords(&self) -> &BTreeSet<&'static str> {
        static KW: Lazy<BTreeSet<&'static str>> = Lazy::new(|| {
            BTreeSet::from([
                "emit",
                "publish",
                "send",
                "postmessage",
                "sendmessage",
                "dispatch",
                "notify",
                "broadcast",
            ])
        });
        &KW
    }
    fn extract_sender(&self, line_text: &str) -> Option<String> {
        C_STYLE_FN
            .captures(line_text.trim())
            .and_then(|c| c.get(1).map(|m| m.as_str().to_string()))
    }
    fn extract_fields(&self, callee_name: &str, args: ArgList<'_>) -> MessageFields {
        let lower = callee_name.to_ascii_lowercase();
        let first = args.first().map(String::as_str).unwrap_or("");
        let second = args.get(1).map(String::as_str).unwrap_or("");
        let third = args.get(2).map(String::as_str).unwrap_or("");
        let mut message_name = unquote(first)
            .unwrap_or_else(|| first.trim().to_string())
            .trim()
            .chars()
            .take(220)
            .collect::<String>();
        if message_name.is_empty() {
            message_name = callee_name.chars().take(220).collect();
        }
        let mut payload: String = if !second.trim().is_empty() {
            second.trim().to_string()
        } else {
            first.trim().to_string()
        };
        let mut receiver = String::new();
        if matches!(lower.as_str(), "sendmessage" | "postmessage") {
            receiver = first.trim().to_string();
            payload = if !second.trim().is_empty() {
                second.trim().to_string()
            } else if !third.trim().is_empty() {
                third.trim().to_string()
            } else {
                String::new()
            };
            if unquote(first).is_none() && !second.is_empty() {
                let override_name = unquote(second).unwrap_or_else(|| message_name.clone());
                message_name = override_name.chars().take(220).collect();
            }
        } else if looks_endpoint(second) {
            receiver = second.trim().chars().take(220).collect();
        } else if looks_endpoint(third) {
            receiver = third.trim().chars().take(220).collect();
        }
        payload = truncate_chars(payload, 400);
        (
            message_name,
            receiver,
            payload,
            format!("{callee_name}() inferred by cplus detector"),
        )
    }
}

// ── delphi ───────────────────────────────────────────────────────────────

pub struct DelphiMessageDetector;

static DELPHI_FN: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"^\s*(?:class\s+)?(?:procedure|function|constructor|destructor)\s+([A-Za-z_][A-Za-z0-9_.]*)")
        .expect("static regex")
});

impl MessageDetector for DelphiMessageDetector {
    fn parser_name(&self) -> &'static str {
        "delphi"
    }
    fn keywords(&self) -> &BTreeSet<&'static str> {
        static KW: Lazy<BTreeSet<&'static str>> = Lazy::new(|| {
            BTreeSet::from([
                "sendmessage",
                "postmessage",
                "publish",
                "dispatch",
                "notify",
                "broadcast",
            ])
        });
        &KW
    }
    fn extract_sender(&self, line_text: &str) -> Option<String> {
        DELPHI_FN
            .captures(line_text.trim())
            .and_then(|c| c.get(1).map(|m| m.as_str().to_string()))
    }
    fn extract_fields(&self, callee_name: &str, args: ArgList<'_>) -> MessageFields {
        let lower = callee_name.to_ascii_lowercase();
        let first = args.first().map(String::as_str).unwrap_or("");
        let second = args.get(1).map(String::as_str).unwrap_or("");
        let third = args.get(2).map(String::as_str).unwrap_or("");
        let mut message_name = unquote(first)
            .unwrap_or_else(|| first.trim().to_string())
            .trim()
            .chars()
            .take(220)
            .collect::<String>();
        if message_name.is_empty() {
            message_name = callee_name.chars().take(220).collect();
        }
        let mut payload: String = if !second.trim().is_empty() {
            second.trim().to_string()
        } else {
            first.trim().to_string()
        };
        let mut receiver = String::new();
        if matches!(lower.as_str(), "sendmessage" | "postmessage") {
            receiver = first.trim().to_string();
            payload = if !second.trim().is_empty() {
                second.trim().to_string()
            } else if !third.trim().is_empty() {
                third.trim().to_string()
            } else {
                String::new()
            };
            if unquote(first).is_none() && !second.is_empty() {
                let override_name = unquote(second).unwrap_or_else(|| message_name.clone());
                message_name = override_name.chars().take(220).collect();
            }
        } else if looks_endpoint(second) {
            receiver = second.trim().chars().take(220).collect();
        }
        payload = truncate_chars(payload, 400);
        (
            message_name,
            receiver,
            payload,
            format!("{callee_name}() inferred by delphi detector"),
        )
    }
}

// ── java ─────────────────────────────────────────────────────────────────

pub struct JavaMessageDetector;

static JAVA_FN: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"^\s*(?:(?:public|private|protected|static|final|abstract|synchronized|native|default|override|internal|open|sealed)\s+)*(?:[\w<>\[\],?.]+\s+)+([A-Za-z_][\w]*)\s*\(").expect("static regex")
});

impl MessageDetector for JavaMessageDetector {
    fn parser_name(&self) -> &'static str {
        "java"
    }
    fn keywords(&self) -> &BTreeSet<&'static str> {
        static KW: Lazy<BTreeSet<&'static str>> = Lazy::new(|| {
            BTreeSet::from([
                "publish",
                "post",
                "send",
                "emit",
                "sendbroadcast",
                "registerreceiver",
                "sendmessage",
                "notify",
            ])
        });
        &KW
    }
    fn extract_sender(&self, line_text: &str) -> Option<String> {
        JAVA_FN
            .captures(line_text.trim())
            .and_then(|c| c.get(1).map(|m| m.as_str().to_string()))
    }
    fn extract_fields(&self, callee_name: &str, args: ArgList<'_>) -> MessageFields {
        let lower = callee_name.to_ascii_lowercase();
        let first = args.first().map(String::as_str).unwrap_or("");
        let second = args.get(1).map(String::as_str).unwrap_or("");
        let third = args.get(2).map(String::as_str).unwrap_or("");
        let message_name = unquote(first)
            .unwrap_or_else(|| first.trim().to_string())
            .trim()
            .chars()
            .take(220)
            .collect::<String>();
        let message_name = if message_name.is_empty() {
            callee_name.chars().take(220).collect()
        } else {
            message_name
        };
        let payload_seed = if !second.trim().is_empty() {
            second.trim().to_string()
        } else {
            first.trim().to_string()
        };
        let mut receiver = String::new();
        if lower == "registerreceiver" {
            receiver = if !first.trim().is_empty() {
                first.trim().to_string()
            } else {
                second.trim().to_string()
            };
        } else if matches!(
            lower.as_str(),
            "sendbroadcast" | "publish" | "emit" | "post" | "notify"
        ) {
            if looks_endpoint(second) {
                receiver = second.trim().chars().take(220).collect();
            } else if looks_endpoint(third) {
                receiver = third.trim().chars().take(220).collect();
            }
        } else if looks_endpoint(second) {
            receiver = second.trim().chars().take(220).collect();
        }
        let payload = truncate_chars(payload_seed, 400);
        (
            message_name,
            receiver,
            payload,
            format!("{callee_name}() inferred by java detector"),
        )
    }
}

// ── csharp ───────────────────────────────────────────────────────────────

pub struct CSharpMessageDetector;

static CSHARP_FN: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"^\s*(?:(?:public|private|protected|internal|static|sealed|virtual|override|async|partial)\s+)*(?:[\w<>\[\],?.]+\s+)+([A-Za-z_][\w]*)\s*\(").expect("static regex")
});

impl MessageDetector for CSharpMessageDetector {
    fn parser_name(&self) -> &'static str {
        "csharp"
    }
    fn keywords(&self) -> &BTreeSet<&'static str> {
        static KW: Lazy<BTreeSet<&'static str>> = Lazy::new(|| {
            BTreeSet::from([
                "publish",
                "send",
                "sendasync",
                "emit",
                "post",
                "notify",
                "dispatch",
                "broadcast",
            ])
        });
        &KW
    }
    fn extract_sender(&self, line_text: &str) -> Option<String> {
        CSHARP_FN
            .captures(line_text.trim())
            .and_then(|c| c.get(1).map(|m| m.as_str().to_string()))
    }
    fn extract_fields(&self, callee_name: &str, args: ArgList<'_>) -> MessageFields {
        let first = args.first().map(String::as_str).unwrap_or("");
        let second = args.get(1).map(String::as_str).unwrap_or("");
        let third = args.get(2).map(String::as_str).unwrap_or("");
        let message_name = unquote(first)
            .unwrap_or_else(|| first.trim().to_string())
            .trim()
            .chars()
            .take(220)
            .collect::<String>();
        let message_name = if message_name.is_empty() {
            callee_name.chars().take(220).collect()
        } else {
            message_name
        };
        let payload_seed = if !second.trim().is_empty() {
            second.trim().to_string()
        } else {
            first.trim().to_string()
        };
        let mut receiver = String::new();
        if looks_endpoint(second) {
            receiver = second.trim().chars().take(220).collect();
        } else if looks_endpoint(third) {
            receiver = third.trim().chars().take(220).collect();
        }
        let payload = truncate_chars(payload_seed, 400);
        (
            message_name,
            receiver,
            payload,
            format!("{callee_name}() inferred by csharp detector"),
        )
    }
}

// ── kotlin ───────────────────────────────────────────────────────────────

pub struct KotlinMessageDetector;

static KOTLIN_FN: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"^\s*(?:suspend\s+)?fun\s+([A-Za-z_][\w.]*)\s*\(").expect("static regex"));
static JAVA_LIKE_FN: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"^\s*(?:(?:public|private|protected|internal|open|override|abstract|final|suspend|inline)\s+)*(?:[\w<>\[\],?.]+\s+)+([A-Za-z_][\w]*)\s*\(").expect("static regex")
});

impl MessageDetector for KotlinMessageDetector {
    fn parser_name(&self) -> &'static str {
        "kotlin"
    }
    fn keywords(&self) -> &BTreeSet<&'static str> {
        static KW: Lazy<BTreeSet<&'static str>> = Lazy::new(|| {
            BTreeSet::from([
                "emit",
                "publish",
                "post",
                "send",
                "sendbroadcast",
                "registerreceiver",
                "sendmessage",
                "notify",
            ])
        });
        &KW
    }
    fn extract_sender(&self, line_text: &str) -> Option<String> {
        let stripped = line_text.trim();
        if let Some(c) = KOTLIN_FN.captures(stripped) {
            return c.get(1).map(|m| m.as_str().to_string());
        }
        JAVA_LIKE_FN
            .captures(stripped)
            .and_then(|c| c.get(1).map(|m| m.as_str().to_string()))
    }
    fn extract_fields(&self, callee_name: &str, args: ArgList<'_>) -> MessageFields {
        let lower = callee_name.to_ascii_lowercase();
        let first = args.first().map(String::as_str).unwrap_or("");
        let second = args.get(1).map(String::as_str).unwrap_or("");
        let third = args.get(2).map(String::as_str).unwrap_or("");
        let message_name = unquote(first)
            .unwrap_or_else(|| first.trim().to_string())
            .trim()
            .chars()
            .take(220)
            .collect::<String>();
        let message_name = if message_name.is_empty() {
            callee_name.chars().take(220).collect()
        } else {
            message_name
        };
        let payload_seed = if !second.trim().is_empty() {
            second.trim().to_string()
        } else {
            first.trim().to_string()
        };
        let mut receiver = String::new();
        if lower == "registerreceiver" {
            receiver = if !first.trim().is_empty() {
                first.trim().to_string()
            } else {
                second.trim().to_string()
            };
        } else if looks_endpoint(second) {
            receiver = second.trim().chars().take(220).collect();
        } else if looks_endpoint(third) {
            receiver = third.trim().chars().take(220).collect();
        }
        let payload = truncate_chars(payload_seed, 400);
        (
            message_name,
            receiver,
            payload,
            format!("{callee_name}() inferred by kotlin detector"),
        )
    }
}

// ── android (extends kotlin) ─────────────────────────────────────────────

pub struct AndroidMessageDetector;

impl MessageDetector for AndroidMessageDetector {
    fn parser_name(&self) -> &'static str {
        "android"
    }
    fn keywords(&self) -> &BTreeSet<&'static str> {
        static KW: Lazy<BTreeSet<&'static str>> = Lazy::new(|| {
            BTreeSet::from([
                "emit",
                "publish",
                "post",
                "send",
                "sendbroadcast",
                "registerreceiver",
                "startactivity",
                "startservice",
                "sendmessage",
                "notify",
            ])
        });
        &KW
    }
    fn extract_sender(&self, line_text: &str) -> Option<String> {
        // Reuse kotlin extraction (Android = kotlin + java).
        KotlinMessageDetector.extract_sender(line_text)
    }
    fn extract_fields(&self, callee_name: &str, args: ArgList<'_>) -> MessageFields {
        let lower = callee_name.to_ascii_lowercase();
        let first = args.first().map(String::as_str).unwrap_or("");
        let second = args.get(1).map(String::as_str).unwrap_or("");
        let third = args.get(2).map(String::as_str).unwrap_or("");
        let message_name = unquote(first)
            .unwrap_or_else(|| first.trim().to_string())
            .trim()
            .chars()
            .take(220)
            .collect::<String>();
        let message_name = if message_name.is_empty() {
            callee_name.chars().take(220).collect()
        } else {
            message_name
        };
        let mut payload_seed = if !second.trim().is_empty() {
            second.trim().to_string()
        } else {
            first.trim().to_string()
        };
        let mut receiver = String::new();
        if matches!(lower.as_str(), "startactivity" | "startservice") {
            receiver = if !first.trim().is_empty() {
                first.trim().to_string()
            } else {
                second.trim().to_string()
            };
            payload_seed = if !second.trim().is_empty() {
                second.trim().to_string()
            } else {
                third.trim().to_string()
            };
        } else if lower == "registerreceiver" {
            receiver = if !first.trim().is_empty() {
                first.trim().to_string()
            } else {
                second.trim().to_string()
            };
        } else if matches!(
            lower.as_str(),
            "sendbroadcast" | "publish" | "emit" | "post" | "notify"
        ) {
            if looks_endpoint(second) {
                receiver = second.trim().chars().take(220).collect();
            } else if looks_endpoint(third) {
                receiver = third.trim().chars().take(220).collect();
            }
        } else if looks_endpoint(second) {
            receiver = second.trim().chars().take(220).collect();
        }
        let payload = truncate_chars(payload_seed, 400);
        (
            message_name,
            receiver,
            payload,
            format!("{callee_name}() inferred by android detector"),
        )
    }
}

// ── python ───────────────────────────────────────────────────────────────

pub struct PythonMessageDetector;

static PY_FN: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"^\s*(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(").expect("static regex"));

impl MessageDetector for PythonMessageDetector {
    fn parser_name(&self) -> &'static str {
        "python"
    }
    fn keywords(&self) -> &BTreeSet<&'static str> {
        static KW: Lazy<BTreeSet<&'static str>> = Lazy::new(|| {
            BTreeSet::from([
                "publish",
                "send",
                "emit",
                "post",
                "notify",
                "dispatch",
                "enqueue",
                "produce",
                "push",
            ])
        });
        &KW
    }
    fn extract_sender(&self, line_text: &str) -> Option<String> {
        PY_FN
            .captures(line_text.trim())
            .and_then(|c| c.get(1).map(|m| m.as_str().to_string()))
    }
    fn extract_fields(&self, callee_name: &str, args: ArgList<'_>) -> MessageFields {
        let first = args.first().map(String::as_str).unwrap_or("");
        let second = args.get(1).map(String::as_str).unwrap_or("");
        let third = args.get(2).map(String::as_str).unwrap_or("");
        let message_name = unquote(first)
            .unwrap_or_else(|| first.trim().to_string())
            .trim()
            .chars()
            .take(220)
            .collect::<String>();
        let message_name = if message_name.is_empty() {
            callee_name.chars().take(220).collect()
        } else {
            message_name
        };
        let payload_seed = if !second.trim().is_empty() {
            second.trim().to_string()
        } else {
            first.trim().to_string()
        };
        let mut receiver = String::new();
        if looks_endpoint(second) {
            receiver = second.trim().chars().take(220).collect();
        } else if looks_endpoint(third) {
            receiver = third.trim().chars().take(220).collect();
        }
        let payload = truncate_chars(payload_seed, 400);
        (
            message_name,
            receiver,
            payload,
            format!("{callee_name}() inferred by python detector"),
        )
    }
}

// ── js ───────────────────────────────────────────────────────────────────

pub struct JsMessageDetector;

static JS_FUNCTION_DECL: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"^\s*function\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\(").expect("static regex"));
static JS_ARROW_ASSIGN: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"^\s*(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(?:async\s+)?\([^)]*\)\s*=>")
        .expect("static regex")
});
static JS_METHOD_DECL: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"^\s*(?:async\s+)?([A-Za-z_$][A-Za-z0-9_$]*)\s*\([^)]*\)\s*\{").expect("static regex")
});

impl MessageDetector for JsMessageDetector {
    fn parser_name(&self) -> &'static str {
        "js"
    }
    fn keywords(&self) -> &BTreeSet<&'static str> {
        static KW: Lazy<BTreeSet<&'static str>> = Lazy::new(|| {
            BTreeSet::from([
                "publish",
                "send",
                "emit",
                "post",
                "notify",
                "dispatch",
                "enqueue",
                "produce",
                "broadcast",
            ])
        });
        &KW
    }
    fn extract_sender(&self, line_text: &str) -> Option<String> {
        let stripped = line_text.trim();
        for pattern in [&*JS_FUNCTION_DECL, &*JS_ARROW_ASSIGN, &*JS_METHOD_DECL] {
            if let Some(c) = pattern.captures(stripped)
                && let Some(m) = c.get(1) {
                    return Some(m.as_str().to_string());
                }
        }
        None
    }
    fn extract_fields(&self, callee_name: &str, args: ArgList<'_>) -> MessageFields {
        let first = args.first().map(String::as_str).unwrap_or("");
        let second = args.get(1).map(String::as_str).unwrap_or("");
        let third = args.get(2).map(String::as_str).unwrap_or("");
        let message_name = unquote(first)
            .unwrap_or_else(|| first.trim().to_string())
            .trim()
            .chars()
            .take(220)
            .collect::<String>();
        let message_name = if message_name.is_empty() {
            callee_name.chars().take(220).collect()
        } else {
            message_name
        };
        let payload_seed = if !second.trim().is_empty() {
            second.trim().to_string()
        } else {
            first.trim().to_string()
        };
        let mut receiver = String::new();
        if looks_endpoint(second) {
            receiver = second.trim().chars().take(220).collect();
        } else if looks_endpoint(third) {
            receiver = third.trim().chars().take(220).collect();
        }
        let payload = truncate_chars(payload_seed, 400);
        (
            message_name,
            receiver,
            payload,
            format!("{callee_name}() inferred by js detector"),
        )
    }
}

// ── ts ───────────────────────────────────────────────────────────────────

pub struct TsMessageDetector;

static TS_FUNCTION_DECL: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"^\s*function\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\(").expect("static regex"));
static TS_ARROW_ASSIGN: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"^\s*(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(?:async\s+)?\([^)]*\)\s*(?::[^=]+)?=>")
        .expect("static regex")
});
static TS_METHOD_DECL: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"^\s*(?:(?:public|private|protected|static|readonly|async)\s+)*([A-Za-z_$][A-Za-z0-9_$]*)\s*\([^)]*\)\s*(?::\s*[^ {]+)?\s*\{").expect("static regex")
});

impl MessageDetector for TsMessageDetector {
    fn parser_name(&self) -> &'static str {
        "ts"
    }
    fn keywords(&self) -> &BTreeSet<&'static str> {
        static KW: Lazy<BTreeSet<&'static str>> = Lazy::new(|| {
            BTreeSet::from([
                "publish",
                "send",
                "emit",
                "post",
                "notify",
                "dispatch",
                "enqueue",
                "produce",
                "broadcast",
            ])
        });
        &KW
    }
    fn extract_sender(&self, line_text: &str) -> Option<String> {
        let stripped = line_text.trim();
        for pattern in [&*TS_FUNCTION_DECL, &*TS_ARROW_ASSIGN, &*TS_METHOD_DECL] {
            if let Some(c) = pattern.captures(stripped)
                && let Some(m) = c.get(1) {
                    return Some(m.as_str().to_string());
                }
        }
        None
    }
    fn extract_fields(&self, callee_name: &str, args: ArgList<'_>) -> MessageFields {
        let first = args.first().map(String::as_str).unwrap_or("");
        let second = args.get(1).map(String::as_str).unwrap_or("");
        let third = args.get(2).map(String::as_str).unwrap_or("");
        let message_name = unquote(first)
            .unwrap_or_else(|| first.trim().to_string())
            .trim()
            .chars()
            .take(220)
            .collect::<String>();
        let message_name = if message_name.is_empty() {
            callee_name.chars().take(220).collect()
        } else {
            message_name
        };
        let payload_seed = if !second.trim().is_empty() {
            second.trim().to_string()
        } else {
            first.trim().to_string()
        };
        let mut receiver = String::new();
        if looks_endpoint(second) {
            receiver = second.trim().chars().take(220).collect();
        } else if looks_endpoint(third) {
            receiver = third.trim().chars().take(220).collect();
        }
        let payload = truncate_chars(payload_seed, 400);
        (
            message_name,
            receiver,
            payload,
            format!("{callee_name}() inferred by ts detector"),
        )
    }
}

// ── php ──────────────────────────────────────────────────────────────────

pub struct PhpMessageDetector;

static PHP_FN: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"^\s*(?:(?:public|private|protected|static|final|abstract)\s+)*function\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(").expect("static regex")
});

impl MessageDetector for PhpMessageDetector {
    fn parser_name(&self) -> &'static str {
        "php"
    }
    fn keywords(&self) -> &BTreeSet<&'static str> {
        static KW: Lazy<BTreeSet<&'static str>> = Lazy::new(|| {
            BTreeSet::from([
                "publish",
                "send",
                "emit",
                "post",
                "notify",
                "dispatch",
                "enqueue",
                "produce",
                "broadcast",
            ])
        });
        &KW
    }
    fn extract_sender(&self, line_text: &str) -> Option<String> {
        PHP_FN
            .captures(line_text.trim())
            .and_then(|c| c.get(1).map(|m| m.as_str().to_string()))
    }
    fn extract_fields(&self, callee_name: &str, args: ArgList<'_>) -> MessageFields {
        let first = args.first().map(String::as_str).unwrap_or("");
        let second = args.get(1).map(String::as_str).unwrap_or("");
        let third = args.get(2).map(String::as_str).unwrap_or("");
        let message_name = unquote(first)
            .unwrap_or_else(|| first.trim().to_string())
            .trim()
            .chars()
            .take(220)
            .collect::<String>();
        let message_name = if message_name.is_empty() {
            callee_name.chars().take(220).collect()
        } else {
            message_name
        };
        let payload_seed = if !second.trim().is_empty() {
            second.trim().to_string()
        } else {
            first.trim().to_string()
        };
        let mut receiver = String::new();
        if looks_endpoint(second) {
            receiver = second.trim().chars().take(220).collect();
        } else if looks_endpoint(third) {
            receiver = third.trim().chars().take(220).collect();
        }
        let payload = truncate_chars(payload_seed, 400);
        (
            message_name,
            receiver,
            payload,
            format!("{callee_name}() inferred by php detector"),
        )
    }
}

// ── sql ──────────────────────────────────────────────────────────────────

pub struct SqlMessageDetector;

fn build_sql_regex() -> Regex {
    Regex::new(
        r"(?i)^\s*create\s+(?:or\s+replace\s+)?(?:procedure|function)\s+([A-Za-z_][A-Za-z0-9_.$]*)",
    )
    .expect("static regex")
}

impl MessageDetector for SqlMessageDetector {
    fn parser_name(&self) -> &'static str {
        "sql"
    }
    fn keywords(&self) -> &BTreeSet<&'static str> {
        static KW: Lazy<BTreeSet<&'static str>> = Lazy::new(|| {
            BTreeSet::from([
                "publish",
                "send",
                "notify",
                "post",
                "emit",
                "enqueue",
                "produce",
            ])
        });
        &KW
    }
    fn extract_sender(&self, line_text: &str) -> Option<String> {
        build_sql_regex()
            .captures(line_text.trim())
            .and_then(|c| c.get(1).map(|m| m.as_str().to_string()))
    }
    fn extract_fields(&self, callee_name: &str, args: ArgList<'_>) -> MessageFields {
        let first = args.first().map(String::as_str).unwrap_or("");
        let second = args.get(1).map(String::as_str).unwrap_or("");
        let third = args.get(2).map(String::as_str).unwrap_or("");
        let message_name = unquote(first)
            .unwrap_or_else(|| first.trim().to_string())
            .trim()
            .chars()
            .take(220)
            .collect::<String>();
        let message_name = if message_name.is_empty() {
            callee_name.chars().take(220).collect()
        } else {
            message_name
        };
        let payload_seed = if !second.trim().is_empty() {
            second.trim().to_string()
        } else {
            first.trim().to_string()
        };
        let mut receiver = String::new();
        if looks_endpoint(second) {
            receiver = second.trim().chars().take(220).collect();
        } else if looks_endpoint(third) {
            receiver = third.trim().chars().take(220).collect();
        }
        let payload = truncate_chars(payload_seed, 400);
        (
            message_name,
            receiver,
            payload,
            format!("{callee_name}() inferred by sql detector"),
        )
    }
}

// ── plsql ────────────────────────────────────────────────────────────────

pub struct PlSqlMessageDetector;

fn build_plsql_regex() -> Regex {
    Regex::new(r"^\s*(?:create\s+(?:or\s+replace\s+)?(?:procedure|function)\s+|(?:procedure|function)\s+)([A-Za-z_][A-Za-z0-9_.$]*)").expect("static regex")
}

impl MessageDetector for PlSqlMessageDetector {
    fn parser_name(&self) -> &'static str {
        "plsql"
    }
    fn keywords(&self) -> &BTreeSet<&'static str> {
        static KW: Lazy<BTreeSet<&'static str>> = Lazy::new(|| {
            BTreeSet::from([
                "publish",
                "send",
                "notify",
                "post",
                "emit",
                "enqueue",
                "produce",
            ])
        });
        &KW
    }
    fn extract_sender(&self, line_text: &str) -> Option<String> {
        build_plsql_regex()
            .captures(line_text.trim())
            .and_then(|c| c.get(1).map(|m| m.as_str().to_string()))
    }
    fn extract_fields(&self, callee_name: &str, args: ArgList<'_>) -> MessageFields {
        let first = args.first().map(String::as_str).unwrap_or("");
        let second = args.get(1).map(String::as_str).unwrap_or("");
        let third = args.get(2).map(String::as_str).unwrap_or("");
        let message_name = unquote(first)
            .unwrap_or_else(|| first.trim().to_string())
            .trim()
            .chars()
            .take(220)
            .collect::<String>();
        let message_name = if message_name.is_empty() {
            callee_name.chars().take(220).collect()
        } else {
            message_name
        };
        let payload_seed = if !second.trim().is_empty() {
            second.trim().to_string()
        } else {
            first.trim().to_string()
        };
        let mut receiver = String::new();
        if looks_endpoint(second) {
            receiver = second.trim().chars().take(220).collect();
        } else if looks_endpoint(third) {
            receiver = third.trim().chars().take(220).collect();
        }
        let payload = truncate_chars(payload_seed, 400);
        (
            message_name,
            receiver,
            payload,
            format!("{callee_name}() inferred by plsql detector"),
        )
    }
}

// ── vbnet ────────────────────────────────────────────────────────────────

pub struct VbNetMessageDetector;

fn build_vb_fn_regex() -> Regex {
    Regex::new(r"(?i)^\s*(?:Public\s+|Private\s+|Friend\s+|Protected\s+|Static\s+|Shared\s+|Overloads\s+|Overrides\s+|Partial\s+|Async\s+)*(?:Sub|Function|Property\s+Get|Property\s+Set|Property\s+Let)\s+([A-Za-z_][A-Za-z0-9_]*)").expect("static regex")
}

impl MessageDetector for VbNetMessageDetector {
    fn parser_name(&self) -> &'static str {
        "vbnet"
    }
    fn keywords(&self) -> &BTreeSet<&'static str> {
        static KW: Lazy<BTreeSet<&'static str>> = Lazy::new(|| {
            BTreeSet::from([
                "publish",
                "send",
                "emit",
                "notify",
                "dispatch",
                "post",
                "raiseevent",
            ])
        });
        &KW
    }
    fn extract_sender(&self, line_text: &str) -> Option<String> {
        build_vb_fn_regex()
            .captures((line_text).trim())
            .and_then(|c| c.get(1).map(|m| m.as_str().to_string()))
    }
    fn extract_fields(&self, callee_name: &str, args: ArgList<'_>) -> MessageFields {
        let first = args.first().map(String::as_str).unwrap_or("");
        let second = args.get(1).map(String::as_str).unwrap_or("");
        let third = args.get(2).map(String::as_str).unwrap_or("");
        let message_name = unquote(first)
            .unwrap_or_else(|| first.trim().to_string())
            .trim()
            .chars()
            .take(220)
            .collect::<String>();
        let message_name = if message_name.is_empty() {
            callee_name.chars().take(220).collect()
        } else {
            message_name
        };
        let payload_seed = if !second.trim().is_empty() {
            second.trim().to_string()
        } else {
            first.trim().to_string()
        };
        let mut receiver = String::new();
        if looks_endpoint(second) {
            receiver = second.trim().chars().take(220).collect();
        } else if looks_endpoint(third) {
            receiver = third.trim().chars().take(220).collect();
        }
        let payload = truncate_chars(payload_seed, 400);
        (
            message_name,
            receiver,
            payload,
            format!("{callee_name}() inferred by vbnet detector"),
        )
    }
}

// ── vb6 ──────────────────────────────────────────────────────────────────

pub struct Vb6MessageDetector;

fn build_vb6_fn_regex() -> Regex {
    Regex::new(r"(?i)^\s*(?:Public\s+|Private\s+|Friend\s+|Static\s+|Global\s+)?(?:Sub|Function|Property\s+Get|Property\s+Set|Property\s+Let)\s+([A-Za-z_][A-Za-z0-9_]*)").expect("static regex")
}

impl MessageDetector for Vb6MessageDetector {
    fn parser_name(&self) -> &'static str {
        "vb6"
    }
    fn keywords(&self) -> &BTreeSet<&'static str> {
        static KW: Lazy<BTreeSet<&'static str>> = Lazy::new(|| {
            BTreeSet::from([
                "sendmessage",
                "postmessage",
                "publish",
                "send",
                "notify",
                "dispatch",
            ])
        });
        &KW
    }
    fn extract_sender(&self, line_text: &str) -> Option<String> {
        build_vb6_fn_regex()
            .captures((line_text).trim())
            .and_then(|c| c.get(1).map(|m| m.as_str().to_string()))
    }
    fn extract_fields(&self, callee_name: &str, args: ArgList<'_>) -> MessageFields {
        let lower = callee_name.to_ascii_lowercase();
        let first = args.first().map(String::as_str).unwrap_or("");
        let second = args.get(1).map(String::as_str).unwrap_or("");
        let third = args.get(2).map(String::as_str).unwrap_or("");
        let mut message_name = unquote(first)
            .unwrap_or_else(|| first.trim().to_string())
            .trim()
            .chars()
            .take(220)
            .collect::<String>();
        if message_name.is_empty() {
            message_name = callee_name.chars().take(220).collect();
        }
        let mut payload: String = if !second.trim().is_empty() {
            second.trim().to_string()
        } else {
            first.trim().to_string()
        };
        let mut receiver = String::new();
        if matches!(lower.as_str(), "sendmessage" | "postmessage") {
            receiver = first.trim().chars().take(220).collect();
            payload = if !second.trim().is_empty() {
                second.trim().to_string()
            } else if !third.trim().is_empty() {
                third.trim().to_string()
            } else {
                String::new()
            };
            if unquote(first).is_none() && !second.is_empty() {
                let override_name = unquote(second).unwrap_or_else(|| message_name.clone());
                message_name = override_name.chars().take(220).collect();
            }
        } else if looks_endpoint(second) {
            receiver = second.trim().chars().take(220).collect();
        }
        payload = truncate_chars(payload, 400);
        (
            message_name,
            receiver,
            payload,
            format!("{callee_name}() inferred by vb6 detector"),
        )
    }
}

// ── vba ──────────────────────────────────────────────────────────────────

pub struct VbaMessageDetector;

impl MessageDetector for VbaMessageDetector {
    fn parser_name(&self) -> &'static str {
        "vba"
    }
    fn keywords(&self) -> &BTreeSet<&'static str> {
        static KW: Lazy<BTreeSet<&'static str>> = Lazy::new(|| {
            BTreeSet::from([
                "publish",
                "send",
                "notify",
                "dispatch",
                "raiseevent",
            ])
        });
        &KW
    }
    fn extract_sender(&self, line_text: &str) -> Option<String> {
        // vba reuses vb6 sender regex (same surface).
        build_vb6_fn_regex()
            .captures((line_text).trim())
            .and_then(|c| c.get(1).map(|m| m.as_str().to_string()))
    }
    fn extract_fields(&self, callee_name: &str, args: ArgList<'_>) -> MessageFields {
        let first = args.first().map(String::as_str).unwrap_or("");
        let second = args.get(1).map(String::as_str).unwrap_or("");
        let third = args.get(2).map(String::as_str).unwrap_or("");
        let message_name = unquote(first)
            .unwrap_or_else(|| first.trim().to_string())
            .trim()
            .chars()
            .take(220)
            .collect::<String>();
        let message_name = if message_name.is_empty() {
            callee_name.chars().take(220).collect()
        } else {
            message_name
        };
        let payload_seed = if !second.trim().is_empty() {
            second.trim().to_string()
        } else {
            first.trim().to_string()
        };
        let mut receiver = String::new();
        if looks_endpoint(second) {
            receiver = second.trim().chars().take(220).collect();
        } else if looks_endpoint(third) {
            receiver = third.trim().chars().take(220).collect();
        }
        let payload = truncate_chars(payload_seed, 400);
        (
            message_name,
            receiver,
            payload,
            format!("{callee_name}() inferred by vba detector"),
        )
    }
}

// ── vbscript ─────────────────────────────────────────────────────────────

pub struct VbScriptMessageDetector;

fn build_vbscript_fn_regex() -> Regex {
    Regex::new(r"(?i)^\s*(?:Sub|Function)\s+([A-Za-z_][A-Za-z0-9_]*)").expect("static regex")
}

impl MessageDetector for VbScriptMessageDetector {
    fn parser_name(&self) -> &'static str {
        "vbscript"
    }
    fn keywords(&self) -> &BTreeSet<&'static str> {
        static KW: Lazy<BTreeSet<&'static str>> = Lazy::new(|| {
            BTreeSet::from([
                "publish",
                "send",
                "notify",
                "dispatch",
                "raiseevent",
            ])
        });
        &KW
    }
    fn extract_sender(&self, line_text: &str) -> Option<String> {
        build_vbscript_fn_regex()
            .captures((line_text).trim())
            .and_then(|c| c.get(1).map(|m| m.as_str().to_string()))
    }
    fn extract_fields(&self, callee_name: &str, args: ArgList<'_>) -> MessageFields {
        let first = args.first().map(String::as_str).unwrap_or("");
        let second = args.get(1).map(String::as_str).unwrap_or("");
        let third = args.get(2).map(String::as_str).unwrap_or("");
        let message_name = unquote(first)
            .unwrap_or_else(|| first.trim().to_string())
            .trim()
            .chars()
            .take(220)
            .collect::<String>();
        let message_name = if message_name.is_empty() {
            callee_name.chars().take(220).collect()
        } else {
            message_name
        };
        let payload_seed = if !second.trim().is_empty() {
            second.trim().to_string()
        } else {
            first.trim().to_string()
        };
        let mut receiver = String::new();
        if looks_endpoint(second) {
            receiver = second.trim().chars().take(220).collect();
        } else if looks_endpoint(third) {
            receiver = third.trim().chars().take(220).collect();
        }
        let payload = truncate_chars(payload_seed, 400);
        (
            message_name,
            receiver,
            payload,
            format!("{callee_name}() inferred by vbscript detector"),
        )
    }
}

// ── detector registry ────────────────────────────────────────────────────

/// Static lookup mirroring Python `_DETECTORS` map. `swift` is bound to the
/// generic detector (`GenericMessageDetector`).
pub fn get_detector(parser: &str) -> &'static dyn MessageDetector {
    let key = parser.trim().to_ascii_lowercase();
    match key.as_str() {
        "cplus" => &CPlusMessageDetector,
        "delphi" => &DelphiMessageDetector,
        "java" => &JavaMessageDetector,
        "csharp" => &CSharpMessageDetector,
        "kotlin" => &KotlinMessageDetector,
        "android" => &AndroidMessageDetector,
        "python" => &PythonMessageDetector,
        "swift" => &GenericMessageDetector,
        "js" => &JsMessageDetector,
        "ts" => &TsMessageDetector,
        "php" => &PhpMessageDetector,
        "sql" => &SqlMessageDetector,
        "plsql" => &PlSqlMessageDetector,
        "vbnet" => &VbNetMessageDetector,
        "vb6" => &Vb6MessageDetector,
        "vba" => &VbaMessageDetector,
        "vbscript" => &VbScriptMessageDetector,
        _ => &GenericMessageDetector,
    }
}

pub fn has_specific_detector(parser: &str) -> bool {
    let key = parser.trim().to_ascii_lowercase();
    matches!(
        key.as_str(),
        "cplus"
            | "delphi"
            | "java"
            | "csharp"
            | "kotlin"
            | "android"
            | "python"
            | "swift"
            | "js"
            | "ts"
            | "php"
            | "sql"
            | "plsql"
            | "vbnet"
            | "vb6"
            | "vba"
            | "vbscript"
    )
}

/// Set of parsers that have a registered detector (matches
/// `_DETECTORS.keys()` in Python).
pub fn supported_parsers() -> BTreeSet<&'static str> {
    BTreeSet::from([
        "cplus",
        "delphi",
        "java",
        "csharp",
        "kotlin",
        "android",
        "python",
        "swift",
        "js",
        "ts",
        "php",
        "sql",
        "plsql",
        "vbnet",
        "vb6",
        "vba",
        "vbscript",
    ])
}

/// Compile-time keyword lookup (BTreeMap of detector_name → keyword set) — used
/// by `collect.rs` to short-circuit per-call.
pub fn detector_keywords(parser: &str) -> Option<BTreeSet<String>> {
    let detector = get_detector(parser);
    Some(detector.keywords().iter().map(|s| s.to_string()).collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unquote_doubles_and_singles() {
        assert_eq!(unquote(r#""hello""#).as_deref(), Some("hello"));
        assert_eq!(unquote(r"'hello'").as_deref(), Some("hello"));
        assert_eq!(unquote("hello"), None);
        // `r#""a\\\"b""#` is the literal 8-char input `"a\\\"b"`. The Python
        // regex extracts `a\\\"b` (5 chars) as the captured group.
        assert_eq!(unquote(r#""a\\\"b""#).as_deref(), Some(r#"a\\\"b"#));
    }

    #[test]
    fn looks_endpoint_drops_keywords_and_long_tokens() {
        assert!(looks_endpoint("dispatcher"));
        assert!(!looks_endpoint("null"));
        assert!(!looks_endpoint("true"));
        assert!(!looks_endpoint("self"));
        assert!(!looks_endpoint("this"));
        // Python: `unquote(token)` returns Some for quoted strings → endpoint
        // counts as endpoint-shaped (used by `receiver` extraction).
        assert!(looks_endpoint("\"quoted\""));
        assert!(!looks_endpoint(&"a".repeat(121)));
        assert!(looks_endpoint(&"a".repeat(120)));
    }

    #[test]
    fn java_emitter_extracts_receiver_from_second_arg() {
        let detector = JavaMessageDetector;
        let args = vec!["\"evt\"".to_string(), "bus".to_string(), "payload".to_string()];
        let (m, r, p, _) = detector.extract_fields("publish", &args);
        assert_eq!(m, "evt");
        assert_eq!(r, "bus");
        assert_eq!(p, "bus");
    }

    #[test]
    fn kotlin_registerreceiver_overrides_receiver_first_arg() {
        let detector = KotlinMessageDetector;
        let args = vec!["subscriber".to_string(), "filter".to_string()];
        let (_m, r, _p, _) = detector.extract_fields("registerReceiver", &args);
        assert_eq!(r, "subscriber");
    }

    #[test]
    fn generic_sendmessage_takes_first_as_receiver() {
        let detector = GenericMessageDetector;
        let args = vec!["\"msg\"".to_string(), "peer".to_string(), "data".to_string()];
        let (_m, r, _p, _) = detector.extract_fields("sendMessage", &args);
        assert_eq!(r, "\"msg\"");
    }

    #[test]
    fn registry_covers_known_parsers() {
        for parser in [
            "cplus",
            "delphi",
            "java",
            "csharp",
            "kotlin",
            "android",
            "python",
            "swift",
            "js",
            "ts",
            "php",
            "sql",
            "plsql",
            "vbnet",
            "vb6",
            "vba",
            "vbscript",
        ] {
            assert!(has_specific_detector(parser), "{parser} missing detector");
        }
        assert!(!has_specific_detector("cobol"));
    }
}