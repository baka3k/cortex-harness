//! Port `tools/common/semantic_inference.py` + `call_graph_builder.py` +
//! `confidence_scorer.py` — multi-signal semantic inference (naming/type/
//! body/usage) sinh summary/note dạng "Performs X operation (takes N
//! parameters)". Format chuỗi này phải byte-identical với Python vì nó được
//! embed vào vector sau này.

use std::collections::HashMap;
use std::sync::OnceLock;

use regex::Regex;

use crate::ts::normalize_ws;

pub const INTENT_RETRIEVAL: &str = "retrieval";
pub const INTENT_MUTATION: &str = "mutation";
pub const INTENT_PREDICATE: &str = "predicate";
pub const INTENT_VALIDATION: &str = "validation";
pub const INTENT_COMPUTATION: &str = "computation";
pub const INTENT_IO_READ: &str = "io_read";
pub const INTENT_IO_WRITE: &str = "io_write";
pub const INTENT_TRANSFORMATION: &str = "transformation";
pub const INTENT_SIDE_EFFECT: &str = "side_effect";
pub const INTENT_DELETION: &str = "deletion";
pub const INTENT_FACTORY: &str = "factory";
pub const INTENT_UNKNOWN: &str = "unknown";

/// Python `round(x, 3)` — correct decimal rounding (ties-to-even) trên giá
/// trị nhị phân; Rust `{:.3}` format cũng correct-round nên parse ngược lại.
pub fn py_round3(x: f64) -> f64 {
    let text = format!("{:.3}", x);
    text.parse::<f64>().unwrap_or(x)
}

// ── Naming analyzer ─────────────────────────────────────────────────────────

/// (pattern, intent, base_confidence) — thứ tự khai báo quyết định tie-break.
const VERB_PATTERNS: &[(&str, &str, f64)] = &[
    (r"^get", INTENT_RETRIEVAL, 0.95),
    (r"^fetch", INTENT_RETRIEVAL, 0.95),
    (r"^retrieve", INTENT_RETRIEVAL, 0.90),
    (r"^find", INTENT_RETRIEVAL, 0.90),
    (r"^search", INTENT_RETRIEVAL, 0.88),
    (r"^query", INTENT_RETRIEVAL, 0.88),
    (r"^list", INTENT_RETRIEVAL, 0.85),
    (r"^select", INTENT_RETRIEVAL, 0.82),
    (r"^load", INTENT_IO_READ, 0.90),
    (r"^read", INTENT_IO_READ, 0.90),
    (r"^import", INTENT_IO_READ, 0.82),
    (r"^download", INTENT_IO_READ, 0.88),
    (r"^set", INTENT_MUTATION, 0.95),
    (r"^update", INTENT_MUTATION, 0.95),
    (r"^modify", INTENT_MUTATION, 0.90),
    (r"^change", INTENT_MUTATION, 0.88),
    (r"^apply", INTENT_MUTATION, 0.78),
    (r"^assign", INTENT_MUTATION, 0.85),
    (r"^patch", INTENT_MUTATION, 0.88),
    (r"^replace", INTENT_MUTATION, 0.85),
    (r"^reset", INTENT_MUTATION, 0.82),
    (r"^save", INTENT_IO_WRITE, 0.90),
    (r"^write", INTENT_IO_WRITE, 0.90),
    (r"^store", INTENT_IO_WRITE, 0.90),
    (r"^persist", INTENT_IO_WRITE, 0.88),
    (r"^upload", INTENT_IO_WRITE, 0.88),
    (r"^export", INTENT_IO_WRITE, 0.82),
    (r"^publish", INTENT_IO_WRITE, 0.82),
    (r"^send", INTENT_IO_WRITE, 0.85),
    (r"^push", INTENT_IO_WRITE, 0.75),
    (r"^is", INTENT_PREDICATE, 0.95),
    (r"^has", INTENT_PREDICATE, 0.95),
    (r"^can", INTENT_PREDICATE, 0.93),
    (r"^should", INTENT_PREDICATE, 0.93),
    (r"^will", INTENT_PREDICATE, 0.88),
    (r"^contains", INTENT_PREDICATE, 0.85),
    (r"^exists", INTENT_PREDICATE, 0.85),
    (r"^allows", INTENT_PREDICATE, 0.82),
    (r"^supports", INTENT_PREDICATE, 0.82),
    (r"^check", INTENT_VALIDATION, 0.88),
    (r"^validate", INTENT_VALIDATION, 0.95),
    (r"^verify", INTENT_VALIDATION, 0.90),
    (r"^ensure", INTENT_VALIDATION, 0.85),
    (r"^assert", INTENT_VALIDATION, 0.88),
    (r"^require", INTENT_VALIDATION, 0.75),
    (r"^calculate", INTENT_COMPUTATION, 0.95),
    (r"^compute", INTENT_COMPUTATION, 0.95),
    (r"^determine", INTENT_COMPUTATION, 0.85),
    (r"^resolve", INTENT_COMPUTATION, 0.80),
    (r"^derive", INTENT_COMPUTATION, 0.82),
    (r"^estimate", INTENT_COMPUTATION, 0.82),
    (r"^count", INTENT_COMPUTATION, 0.85),
    (r"^measure", INTENT_COMPUTATION, 0.82),
    (r"^sum", INTENT_COMPUTATION, 0.88),
    (r"^average", INTENT_COMPUTATION, 0.85),
    (r"^create", INTENT_FACTORY, 0.95),
    (r"^make", INTENT_FACTORY, 0.90),
    (r"^build", INTENT_FACTORY, 0.92),
    (r"^construct", INTENT_FACTORY, 0.90),
    (r"^generate", INTENT_FACTORY, 0.85),
    (r"^spawn", INTENT_FACTORY, 0.82),
    (r"^produce", INTENT_FACTORY, 0.80),
    (r"^instantiate", INTENT_FACTORY, 0.88),
    (r"^new", INTENT_FACTORY, 0.75),
    (r"^init(?:ialize)?", INTENT_FACTORY, 0.85),
    (r"^delete", INTENT_DELETION, 0.95),
    (r"^remove", INTENT_DELETION, 0.93),
    (r"^destroy", INTENT_DELETION, 0.90),
    (r"^clear", INTENT_DELETION, 0.88),
    (r"^purge", INTENT_DELETION, 0.88),
    (r"^drop", INTENT_DELETION, 0.82),
    (r"^discard", INTENT_DELETION, 0.82),
    (r"^unset", INTENT_DELETION, 0.80),
    (r"^revoke", INTENT_DELETION, 0.80),
    (r"^parse", INTENT_TRANSFORMATION, 0.88),
    (r"^transform", INTENT_TRANSFORMATION, 0.88),
    (r"^convert", INTENT_TRANSFORMATION, 0.88),
    (r"^format", INTENT_TRANSFORMATION, 0.85),
    (r"^map", INTENT_TRANSFORMATION, 0.80),
    (r"^serialize", INTENT_TRANSFORMATION, 0.90),
    (r"^deserialize", INTENT_TRANSFORMATION, 0.90),
    (r"^encode", INTENT_TRANSFORMATION, 0.88),
    (r"^decode", INTENT_TRANSFORMATION, 0.88),
    (r"^normalize", INTENT_TRANSFORMATION, 0.85),
    (r"^sanitize", INTENT_TRANSFORMATION, 0.85),
    (r"^render", INTENT_TRANSFORMATION, 0.78),
    (r"^handle", INTENT_SIDE_EFFECT, 0.75),
    (r"^process", INTENT_SIDE_EFFECT, 0.72),
    (r"^execute", INTENT_SIDE_EFFECT, 0.78),
    (r"^run", INTENT_SIDE_EFFECT, 0.72),
    (r"^perform", INTENT_SIDE_EFFECT, 0.72),
    (r"^do", INTENT_SIDE_EFFECT, 0.65),
    (r"^invoke", INTENT_SIDE_EFFECT, 0.78),
    (r"^dispatch", INTENT_SIDE_EFFECT, 0.78),
    (r"^notify", INTENT_SIDE_EFFECT, 0.78),
    (r"^trigger", INTENT_SIDE_EFFECT, 0.78),
    (r"^emit", INTENT_SIDE_EFFECT, 0.78),
    (r"^fire", INTENT_SIDE_EFFECT, 0.75),
    (r"^broadcast", INTENT_SIDE_EFFECT, 0.78),
    (r"^log", INTENT_SIDE_EFFECT, 0.80),
    (r"^track", INTENT_SIDE_EFFECT, 0.75),
    (r"^register", INTENT_SIDE_EFFECT, 0.75),
    (r"^subscribe", INTENT_SIDE_EFFECT, 0.78),
    (r"^unsubscribe", INTENT_SIDE_EFFECT, 0.78),
    (r"^add", INTENT_MUTATION, 0.85),
    (r"^append", INTENT_MUTATION, 0.85),
    (r"^insert", INTENT_MUTATION, 0.85),
    (r"^prepend", INTENT_MUTATION, 0.83),
    (r"^attach", INTENT_MUTATION, 0.80),
    (r"^merge", INTENT_MUTATION, 0.80),
    (r"^inject", INTENT_MUTATION, 0.75),
    (r"^receive", INTENT_IO_READ, 0.80),
];

const SUMMARY_TEMPLATES: &[(&str, &str)] = &[
    (INTENT_RETRIEVAL, "Retrieves {subject}"),
    (INTENT_IO_READ, "Reads {subject} from external source"),
    (INTENT_MUTATION, "Updates or modifies {subject}"),
    (INTENT_IO_WRITE, "Writes {subject} to persistent storage"),
    (INTENT_PREDICATE, "Checks whether {subject}"),
    (INTENT_VALIDATION, "Validates {subject}"),
    (INTENT_COMPUTATION, "Calculates {subject}"),
    (INTENT_FACTORY, "Creates a new {subject}"),
    (INTENT_DELETION, "Deletes {subject}"),
    (INTENT_SIDE_EFFECT, "Performs {subject} operation"),
    (INTENT_TRANSFORMATION, "Transforms {subject}"),
    (INTENT_UNKNOWN, "Performs unknown operation on {subject}"),
];

// ── Type annotation analyzer ────────────────────────────────────────────────

const RETURN_TYPE_SIGNALS: &[(&str, &str, f64)] = &[
    (r"(?i)^void$", INTENT_SIDE_EFFECT, 0.70),
    (r"(?i)^undefined$", INTENT_SIDE_EFFECT, 0.60),
    (r"(?i)^never$", INTENT_VALIDATION, 0.65),
    (r"(?i)^bool(?:ean)?$", INTENT_PREDICATE, 0.85),
    (r"(?i)Promise\s*<\s*(?:void|undefined)", INTENT_SIDE_EFFECT, 0.65),
    (r"(?i)Promise\s*<\s*bool(?:ean)?", INTENT_PREDICATE, 0.80),
    (r"(?i)Observable\s*<", INTENT_IO_READ, 0.60),
    (r"(?i)Promise\s*<", INTENT_IO_READ, 0.55),
];

fn analyze_return_type(return_type: &str, compiled: &Compiled) -> (Option<&'static str>, f64) {
    if return_type.is_empty() {
        return (None, 0.0);
    }
    let rt = return_type.trim();
    for (re, intent, conf) in &compiled.return_signals {
        if re.is_match(rt) {
            return (Some(intent), *conf);
        }
    }
    if !rt.is_empty() && !matches!(rt, "any" | "unknown" | "T" | "U" | "R") {
        return (Some(INTENT_RETRIEVAL), 0.45);
    }
    (None, 0.0)
}

fn analyze_param_types(param_types: &[String]) -> (Option<&'static str>, f64) {
    if param_types.is_empty() {
        return (None, 0.0);
    }
    for pt in param_types {
        if pt.is_empty() {
            continue;
        }
        let pt_lower = pt.to_lowercase();
        if pt_lower.contains("event") {
            return (Some(INTENT_SIDE_EFFECT), 0.40);
        }
        if pt_lower.contains("request") || pt_lower == "req" {
            return (Some(INTENT_IO_READ), 0.35);
        }
        if pt_lower.contains("response") || pt_lower == "res" {
            return (Some(INTENT_IO_WRITE), 0.35);
        }
    }
    (None, 0.0)
}

// ── Body analyzer ───────────────────────────────────────────────────────────

/// (intent, patterns) — thứ tự khai báo là tie-break của `max(scores, key=…)`.
const BODY_PATTERNS: &[(&str, &[&str])] = &[
    (
        INTENT_IO_READ,
        &[
            r"(?i)\bfetch\s*\(",
            r"(?i)\baxios\b",
            r"(?i)\bHttpClient\b",
            r"(?i)\.get\s*\(",
            r"(?i)await\s+\w+\.find",
            r"(?i)\bprisma\.\w+\.find",
            r"(?i)\bknex\(",
            r"(?i)localStorage\.getItem",
            r"(?i)sessionStorage\.getItem",
            r"(?i)\bfs\.read",
            r"(?i)\bsupabase\.",
        ],
    ),
    (
        INTENT_IO_WRITE,
        &[
            r"(?i)\.post\s*\(",
            r"(?i)\.put\s*\(",
            r"(?i)\.patch\s*\(",
            r"(?i)\.save\s*\(",
            r"(?i)\.create\s*\(",
            r"(?i)\.insert\s*\(",
            r"(?i)\.update\s*\(",
            r"(?i)\.upsert\s*\(",
            r"(?i)localStorage\.setItem",
            r"(?i)sessionStorage\.setItem",
            r"(?i)\bfs\.write",
        ],
    ),
    (
        INTENT_VALIDATION,
        &[
            r"(?i)\bthrow\s+new\b",
            r"(?i)\bthrow\b",
            r"(?i)throw.*Error",
            r"(?i)if\s*\(.*\)\s*throw",
        ],
    ),
    (
        INTENT_COMPUTATION,
        &[
            r"(?i)\bfor\s*\(",
            r"(?i)\bwhile\s*\(",
            r"(?i)\b\.reduce\s*\(",
            r"(?i)\bMath\.",
            r"[+\-*/]\s*\w",
            r"(?i)\bparseInt\b",
            r"(?i)\bparseFloat\b",
        ],
    ),
    (
        INTENT_SIDE_EFFECT,
        &[
            r"(?i)\bemit\s*\(",
            r"(?i)\.dispatch\s*\(",
            r"(?i)\bconsole\.",
            r"(?i)setState\s*\(",
            r"(?i)this\.state\s*=",
            r"(?i)\.addEventListener\s*\(",
            r"(?i)\bsetTimeout\s*\(",
            r"(?i)\bsetInterval\s*\(",
        ],
    ),
    (
        INTENT_TRANSFORMATION,
        &[
            r"(?i)\b\.map\s*\(",
            r"(?i)\b\.filter\s*\(",
            r"(?i)\bJSON\.parse\b",
            r"(?i)\bJSON\.stringify\b",
            r"(?i)\bObject\.assign\b",
            r"\bSpread\b|\.\.\.",
        ],
    ),
];

const BODY_MATCH_WEIGHT: f64 = 0.25;

fn analyze_body(code: &str, compiled: &Compiled) -> (Option<&'static str>, f64) {
    if code.is_empty() {
        return (None, 0.0);
    }
    // scores theo thứ tự khai báo; `max` của Python trả key đầu tiên đạt max.
    let mut best: Option<(&'static str, f64)> = None;
    for (intent, patterns) in compiled.body_patterns.iter() {
        let count = patterns.iter().filter(|re| re.is_match(code)).count();
        if count == 0 {
            continue;
        }
        let score = (count as f64 * BODY_MATCH_WEIGHT).min(1.0);
        match best {
            Some((_, best_score)) if score <= best_score => {}
            _ => best = Some((intent, score)),
        }
    }
    match best {
        Some((intent, score)) => (Some(intent), py_round3(score)),
        None => (None, 0.0),
    }
}

// ── Subject extraction ──────────────────────────────────────────────────────

fn camel_to_spaced(name: &str, compiled: &Compiled) -> String {
    let spaced = compiled
        .camel_split
        .replace_all(name, "${1}${3} ${2}${4}")
        .to_string();
    let spaced = spaced.replace('_', " ");
    normalize_ws(&spaced).to_lowercase()
}

fn extract_subject(name: &str, pattern: &Regex, compiled: &Compiled) -> String {
    let remainder = pattern.replace(name, "").trim_matches('_').to_string();
    let remainder = compiled.articles.replace(&remainder, "").to_string();
    let spaced = camel_to_spaced(&remainder, compiled);
    if spaced.is_empty() {
        "data".to_string()
    } else {
        spaced
    }
}

fn camel_to_words(name: &str, compiled: &Compiled) -> String {
    let spaced = camel_to_spaced(name, compiled);
    if spaced.is_empty() {
        "data".to_string()
    } else {
        spaced
    }
}

// ── Summary generator ───────────────────────────────────────────────────────

fn generate_summary(intent: &str, subject: &str, arity: i64, is_async: bool) -> String {
    let template = SUMMARY_TEMPLATES
        .iter()
        .find(|(key, _)| *key == intent)
        .map(|(_, template)| *template)
        .unwrap_or("Performs {subject} operation");
    let mut summary = template.replace("{subject}", subject);
    let mut enrichments: Vec<String> = Vec::new();
    if arity > 0 {
        enrichments.push(format!(
            "takes {arity} parameter{}",
            if arity > 1 { "s" } else { "" }
        ));
    }
    if is_async {
        enrichments.push("async".to_string());
    }
    if !enrichments.is_empty() {
        summary += &format!(" ({})", enrichments.join(", "));
    }
    summary
}

// ── Confidence scorer (confidence_scorer.py) ────────────────────────────────

const GENERIC_TOKENS: &[&str] = &[
    "data",
    "stuff",
    "thing",
    "value",
    "object",
    "obj",
    "item",
    "result",
    "response",
    "res",
    "info",
    "detail",
    "content",
    "payload",
    "input",
    "output",
    "temp",
    "tmp",
    "foo",
    "bar",
    "baz",
    "test",
    "util",
    "utils",
    "helper",
    "handler",
    "run",
    "do",
    "exec",
    "func",
    "fn",
    // Common generic verb-stems that carry no domain meaning
    "handle",
    "process",
    "manage",
    "perform",
    "execute",
];

const VERB_PREFIXES: &[&str] = &[
    "get",
    "fetch",
    "retrieve",
    "find",
    "search",
    "query",
    "list",
    "select",
    "load",
    "read",
    "download",
    "import",
    "set",
    "update",
    "modify",
    "change",
    "apply",
    "assign",
    "patch",
    "replace",
    "reset",
    "save",
    "write",
    "store",
    "persist",
    "upload",
    "export",
    "publish",
    "send",
    "push",
    "is",
    "has",
    "can",
    "should",
    "will",
    "check",
    "validate",
    "verify",
    "ensure",
    "assert",
    "calculate",
    "compute",
    "determine",
    "resolve",
    "derive",
    "estimate",
    "count",
    "measure",
    "sum",
    "create",
    "make",
    "build",
    "construct",
    "generate",
    "spawn",
    "produce",
    "init",
    "initialize",
    "delete",
    "remove",
    "destroy",
    "clear",
    "purge",
    "drop",
    "parse",
    "transform",
    "convert",
    "format",
    "map",
    "serialize",
    "deserialize",
    "encode",
    "decode",
    "normalize",
    "sanitize",
    "render",
    "handle",
    "emit",
    "dispatch",
    "notify",
    "trigger",
    "log",
    "add",
    "append",
    "insert",
    "merge",
    "on",
];

fn is_generic_name(name: &str) -> bool {
    let tokens: Vec<String> = split_name_tokens(name);
    if tokens.is_empty() {
        return true;
    }
    let mut subject_tokens: Vec<&str> = tokens
        .iter()
        .map(String::as_str)
        .filter(|t| !VERB_PREFIXES.contains(t))
        .collect();
    if subject_tokens.is_empty() {
        subject_tokens = tokens.iter().map(String::as_str).collect();
    }
    let meaningful: Vec<&str> = subject_tokens
        .into_iter()
        .filter(|t| !matches!(*t, "the" | "a" | "an" | "my" | "our" | "new"))
        .collect();
    !meaningful.is_empty() && meaningful.iter().all(|t| GENERIC_TOKENS.contains(t))
}

/// `_SPLIT_PATTERN = r"[_\s]+|(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])"`.
fn split_name_tokens(name: &str) -> Vec<String> {
    let mut tokens: Vec<String> = Vec::new();
    for chunk in name.split(|c: char| c == '_' || c.is_whitespace()) {
        let mut start = 0;
        let bytes = chunk.as_bytes();
        let mut i = 1;
        while i < bytes.len() {
            let prev = bytes[i - 1];
            let cur = bytes[i];
            let lower_upper = prev.is_ascii_lowercase() && cur.is_ascii_uppercase();
            let upper_run_lower = prev.is_ascii_uppercase()
                && cur.is_ascii_uppercase()
                && bytes.get(i + 1).map(|b| b.is_ascii_lowercase()).unwrap_or(false);
            if lower_upper || upper_run_lower {
                tokens.push(chunk[start..i].to_string());
                start = i;
            }
            i += 1;
        }
        tokens.push(chunk[start..].to_string());
    }
    // Python `_name_tokens`: frozenset(t.lower() ...) — lowercase trước khi so.
    tokens
        .into_iter()
        .map(|t| t.to_lowercase())
        .filter(|t| !t.is_empty())
        .collect()
}

pub fn confidence_score(
    naming: f64,
    type_: f64,
    usage: f64,
    body: f64,
    func_name: &str,
    is_exported: bool,
) -> f64 {
    // Thứ tự cộng giữ nguyên như Python (plain mul/add, không FMA) để bit-parity.
    let mut raw = 0.40 * naming + 0.20 * type_;
    raw += 0.30 * usage;
    raw += 0.10 * body;
    if is_exported {
        raw = (raw + 0.03).min(1.0);
    }
    if !func_name.is_empty() {
        if func_name.chars().count() <= 3 {
            raw *= 0.75;
        } else if is_generic_name(func_name) {
            raw *= 0.85;
        }
    }
    py_round3(raw.clamp(0.0, 1.0))
}

// ── Usage index (call_graph_builder.py) ─────────────────────────────────────

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub enum CallPattern {
    Assignment,
    Condition,
    Await,
    Standalone,
}

impl CallPattern {
    fn intent_score(&self) -> (Option<&'static str>, f64) {
        match self {
            CallPattern::Assignment => (Some(INTENT_RETRIEVAL), 0.90),
            CallPattern::Condition => (Some(INTENT_PREDICATE), 0.90),
            CallPattern::Await => (Some(INTENT_IO_READ), 0.75),
            CallPattern::Standalone => (Some(INTENT_SIDE_EFFECT), 0.70),
        }
    }
}

#[derive(Debug, Clone)]
pub struct CallSiteContext {
    pattern: CallPattern,
    #[allow(dead_code)]
    caller_id: String,
}

#[derive(Default)]
pub struct FunctionUsageIndex {
    pub by_id: HashMap<String, Vec<CallSiteContext>>,
    pub by_name: HashMap<String, Vec<CallSiteContext>>,
}

fn usage_regex(template: &'static str, name: &str) -> Regex {
    use std::collections::HashMap;
    use std::sync::{Mutex, OnceLock};
    static CACHE: OnceLock<Mutex<HashMap<(&'static str, String), Regex>>> = OnceLock::new();
    let cache = CACHE.get_or_init(|| Mutex::new(HashMap::new()));
    let mut guard = cache.lock().expect("usage regex cache");
    // re.compile phía Python cũng cache theo pattern string — key (template, name).
    guard
        .entry((template, name.to_string()))
        .or_insert_with(|| {
            let escaped = regex::escape(name);
            Regex::new(&template.replace("{name}", &escaped)).expect("usage regex")
        })
        .clone()
}

const RE_ASSIGN: &str =
    r"(?:(?:const|let|var)\s+\w+\s*(?::\s*\S+)?\s*=\s*|return\s+)(?:await\s+)?(?:\w+\.)*{name}\s*[(<]";
const RE_COND: &str = r"(?:(?:if\s*\(\s*(?:!?\s*)?)|(?:\|\|\s*(?:!?\s*)?)|(?:&&\s*(?:!?\s*)?)|(?:\?\s*\S+\s*:\s*)|(?:while\s*\(\s*(?:!?\s*)?))(?:\w+\.)*{name}\s*[(<]";
const RE_AWAIT: &str = r"await\s+(?:\w+\.)*{name}\s*[(<]";

fn extract_call_context(callee_name: &str, caller_code: &str, caller_id: &str) -> CallSiteContext {
    let cond = usage_regex(RE_COND, callee_name);
    let assign = usage_regex(RE_ASSIGN, callee_name);
    let await_re = usage_regex(RE_AWAIT, callee_name);
    let pattern = if cond.is_match(caller_code) {
        CallPattern::Condition
    } else if assign.is_match(caller_code) {
        CallPattern::Assignment
    } else if await_re.is_match(caller_code) {
        CallPattern::Await
    } else {
        CallPattern::Standalone
    };
    CallSiteContext {
        pattern,
        caller_id: caller_id.to_string(),
    }
}

/// Borrow bundle cho `build_usage_index`.
pub struct FunctionRef<'a> {
    pub symbol_id: &'a str,
    pub code: &'a str,
}

pub struct UsageCall<'a> {
    pub caller_id: &'a str,
    pub callee_id: &'a str,
    pub callee_name: &'a str,
}

/// `build_usage_index` — index ngược call-site từ functions + call edges.
pub fn build_usage_index(
    functions: &[FunctionRef<'_>],
    calls: &[UsageCall<'_>],
) -> FunctionUsageIndex {
    let mut code_by_id: HashMap<&str, &str> = HashMap::new();
    for func in functions {
        if !func.symbol_id.is_empty() {
            code_by_id.insert(func.symbol_id, func.code);
        }
    }
    let mut index = FunctionUsageIndex::default();
    for call in calls {
        if call.caller_id.is_empty() || call.callee_name.is_empty() {
            continue;
        }
        let Some(caller_code) = code_by_id.get(call.caller_id) else {
            continue;
        };
        if caller_code.is_empty() {
            continue;
        }
        let ctx = extract_call_context(call.callee_name, caller_code, call.caller_id);
        if !call.callee_id.is_empty() {
            index
                .by_id
                .entry(call.callee_id.to_string())
                .or_default()
                .push(ctx.clone());
        }
        index
            .by_name
            .entry(call.callee_name.to_string())
            .or_default()
            .push(ctx);
    }
    index
}

/// `get_usage_signal` — vote theo pattern, dominant wins.
pub fn get_usage_signal(
    func_id: &str,
    func_name: &str,
    index: &FunctionUsageIndex,
) -> (Option<&'static str>, f64) {
    let empty = Vec::new();
    let contexts = index
        .by_id
        .get(func_id)
        .or_else(|| index.by_name.get(func_name))
        .unwrap_or(&empty);
    if contexts.is_empty() {
        return (None, 0.0);
    }
    // votes giữ thứ tự first-seen (Python dict insertion order cho max()).
    let mut votes: Vec<(CallPattern, usize)> = Vec::new();
    for ctx in contexts {
        if let Some(entry) = votes.iter_mut().find(|(p, _)| *p == ctx.pattern) {
            entry.1 += 1;
        } else {
            votes.push((ctx.pattern.clone(), 1));
        }
    }
    let total = contexts.len();
    let Some((dominant, dominant_votes)) = votes.iter().fold(
        None::<(&CallPattern, usize)>,
        |acc, (p, count)| match acc {
            Some((_, best)) if *count <= best => acc,
            _ => Some((p, *count)),
        },
    ) else {
        return (None, 0.0);
    };
    let vote_ratio = dominant_votes as f64 / total as f64;
    let (intent_hint, base_score) = dominant.intent_score();
    let mut confidence = py_round3(base_score * vote_ratio);
    if total == 1 {
        confidence = py_round3(confidence * 0.70);
    }
    (intent_hint, confidence)
}

// ── Engine ──────────────────────────────────────────────────────────────────

struct Compiled {
    verb_patterns: Vec<(Regex, &'static str, f64)>,
    return_signals: Vec<(Regex, &'static str, f64)>,
    body_patterns: Vec<(&'static str, Vec<Regex>)>,
    on_prefix_match: Regex,
    on_pattern: Regex,
    camel_split: Regex,
    articles: Regex,
    async_re: Regex,
}

fn compiled() -> &'static Compiled {
    static COMPILED: OnceLock<Compiled> = OnceLock::new();
    COMPILED.get_or_init(|| Compiled {
        verb_patterns: VERB_PATTERNS
            .iter()
            .map(|(pat, intent, conf)| {
                (
                    Regex::new(&format!("(?i){pat}")).expect("verb pattern"),
                    *intent,
                    *conf,
                )
            })
            .collect(),
        return_signals: RETURN_TYPE_SIGNALS
            .iter()
            .map(|(pat, intent, conf)| (Regex::new(pat).expect("return pattern"), *intent, *conf))
            .collect(),
        body_patterns: BODY_PATTERNS
            .iter()
            .map(|(intent, patterns)| {
                (
                    *intent,
                    patterns
                        .iter()
                        .map(|pat| Regex::new(pat).expect("body pattern"))
                        .collect(),
                )
            })
            .collect(),
        // Python: re.match(r"^on([A-Z_])", name) — case-SENSITIVE.
        on_prefix_match: Regex::new(r"^on([A-Z_])").expect("on prefix"),
        on_pattern: Regex::new("(?i)^on").expect("on pattern"),
        camel_split: Regex::new(r"([a-z])([A-Z])|([A-Z]+)([A-Z][a-z])").expect("camel split"),
        articles: Regex::new("(?i)^(the|a|an)[\\s_]?").expect("articles"),
        async_re: Regex::new(r"\basync\b").expect("async"),
    })
}

#[derive(Debug, Clone)]
pub struct SemanticResult {
    pub intent: String,
    pub summary: String,
    pub confidence: f64,
    pub inferred: bool,
    pub side_effect: bool,
}

pub struct SemanticInferenceEngine;

impl Default for SemanticInferenceEngine {
    fn default() -> Self {
        Self::new()
    }
}

impl SemanticInferenceEngine {
    pub fn new() -> Self {
        Self
    }

    fn naming_signal(name: &str, compiled: &Compiled) -> (String, f64, Option<Regex>) {
        if name.is_empty() {
            return (INTENT_UNKNOWN.to_string(), 0.0, None);
        }
        if matches!(name, "constructor" | "__init__" | "initialize") {
            return (INTENT_FACTORY.to_string(), 0.90, None);
        }
        if compiled.on_prefix_match.is_match(name) {
            return (
                INTENT_SIDE_EFFECT.to_string(),
                0.72,
                Some(compiled.on_pattern.clone()),
            );
        }
        for (re, intent, conf) in &compiled.verb_patterns {
            if re.is_match(name) {
                return ((*intent).to_string(), *conf, Some(re.clone()));
            }
        }
        if name.chars().count() <= 3 {
            return (INTENT_UNKNOWN.to_string(), 0.15, None);
        }
        (INTENT_UNKNOWN.to_string(), 0.0, None)
    }

    #[allow(clippy::too_many_arguments)]
    fn resolve_intent(
        naming_intent: &str,
        naming_conf: f64,
        type_intent: Option<&str>,
        type_conf: f64,
        body_intent: Option<&str>,
        body_conf: f64,
        usage_intent: Option<&str>,
        usage_conf: f64,
    ) -> String {
        if let Some(usage) = usage_intent
            && usage_conf >= 0.50
        {
            return usage.to_string();
        }
        if let Some(type_i) = type_intent
            && type_conf >= 0.65
            && type_i != naming_intent
        {
            return type_i.to_string();
        }
        if naming_intent != INTENT_UNKNOWN && naming_conf >= 0.60 {
            return naming_intent.to_string();
        }
        if let Some(body) = body_intent
            && body_conf >= 0.50
        {
            return body.to_string();
        }
        if naming_intent != INTENT_UNKNOWN {
            naming_intent.to_string()
        } else {
            INTENT_UNKNOWN.to_string()
        }
    }

    /// `_analyze_safe` — các input là field đã parse của function.
    #[allow(clippy::too_many_arguments)]
    fn analyze(
        &self,
        name: &str,
        code: &str,
        comment: &str,
        arity: i64,
        symbol_id: &str,
        is_exported: bool,
        return_type: &str,
        param_types: &[String],
        index: Option<&FunctionUsageIndex>,
        compiled: &Compiled,
    ) -> SemanticResult {
        let head: String = code.chars().take(120).collect();
        let is_async = compiled.async_re.is_match(&head);

        let (naming_intent, naming_conf, matched_pat) = Self::naming_signal(name, compiled);
        let (type_intent_rt, type_conf_rt) = analyze_return_type(return_type, compiled);
        let (type_intent_pt, type_conf_pt) = analyze_param_types(param_types);
        let type_conf = type_conf_rt.max(type_conf_pt);
        let type_intent = type_intent_rt.or(type_intent_pt);
        let (body_intent, body_conf) = analyze_body(code, compiled);
        let (usage_intent, usage_conf) = match index {
            Some(index) => get_usage_signal(symbol_id, name, index),
            None => (None, 0.0),
        };

        let intent = Self::resolve_intent(
            &naming_intent,
            naming_conf,
            type_intent,
            type_conf,
            body_intent,
            body_conf,
            usage_intent,
            usage_conf,
        );

        let subject = match &matched_pat {
            Some(pat) => extract_subject(name, pat, compiled),
            None => camel_to_words(name, compiled),
        };

        // Python: A or (B and C) với B = return_type rỗng/void/undefined.
        let rt_lower = return_type.trim().to_lowercase();
        let side_effect_base = rt_lower == "void"
            || rt_lower == "undefined"
            || rt_lower.is_empty();
        let is_side_effect = intent == INTENT_SIDE_EFFECT
            || intent == INTENT_IO_WRITE
            || (side_effect_base && intent != INTENT_PREDICATE && intent != INTENT_RETRIEVAL);

        let (summary, inferred) = if !comment.is_empty() {
            let first = comment
                .trim_start_matches(['/', '*', ' ', '\n'])
                .lines()
                .next()
                .unwrap_or("")
                .trim();
            (first.to_string(), false)
        } else {
            (generate_summary(&intent, &subject, arity, is_async), true)
        };

        let confidence = confidence_score(
            naming_conf,
            type_conf,
            usage_conf,
            body_conf,
            name,
            is_exported,
        );

        SemanticResult {
            intent,
            summary,
            confidence,
            inferred,
            side_effect: is_side_effect,
        }
    }

    /// `_enrich_one` — trả field ghi vào graph cho 1 function.
    ///
    /// * `effective_comment` — docstring hoặc leading comment (parse stage).
    /// * `parsed_summary` — summary parse stage (= effective_comment).
    /// * `parsed_note` — note parse stage (unused khi engine active; giữ cho
    ///   đối chiếu).
    #[allow(clippy::too_many_arguments)]
    #[allow(clippy::too_many_arguments)]
    pub fn enrich_one(
        &self,
        name: &str,
        code: &str,
        effective_comment: &str,
        parsed_summary: &str,
        arity: i64,
        symbol_id: &str,
        is_exported: bool,
        return_type: &str,
        param_types: &[String],
        index: &FunctionUsageIndex,
    ) -> EnrichedFunction {
        let compiled = compiled();
        let result = self.analyze(
            name,
            code,
            effective_comment,
            arity,
            symbol_id,
            is_exported,
            return_type,
            param_types,
            Some(index),
            compiled,
        );
        let summary = if effective_comment.is_empty() {
            result.summary.clone()
        } else {
            parsed_summary.to_string()
        };
        let note = build_semantic_note(
            code,
            effective_comment,
            &summary,
            &result.intent,
            result.confidence,
            result.inferred,
        );
        EnrichedFunction {
            intent: result.intent,
            inferred_doc: result.inferred,
            doc_confidence: result.confidence,
            side_effect: result.side_effect,
            summary,
            note,
        }
    }
}

/// Kết quả enrichment cho 1 function — các field analyzer ghi vào graph.
#[derive(Debug, Clone)]
pub struct EnrichedFunction {
    pub intent: String,
    pub inferred_doc: bool,
    pub doc_confidence: f64,
    pub side_effect: bool,
    /// Summary cuối cùng ghi vào graph.
    pub summary: String,
    /// Note mới (semantic note) — luôn thay note parse-stage.
    pub note: String,
}

/// `_build_semantic_note` — note enriched cho embedding.
pub fn build_semantic_note(
    code: &str,
    comment: &str,
    summary: &str,
    intent: &str,
    confidence: f64,
    inferred: bool,
) -> String {
    let mut parts: Vec<String> = Vec::new();
    if !summary.is_empty() {
        let label = if inferred { "Summary (inferred)" } else { "Summary" };
        parts.push(format!("{label}:\n{summary}"));
    }
    if !intent.is_empty() && intent != INTENT_UNKNOWN {
        parts.push(format!("Intent: {intent} (confidence: {confidence:.2})"));
    }
    if !comment.is_empty() {
        parts.push(format!("Comment:\n{comment}"));
    }
    if !code.is_empty() {
        parts.push(format!("Code:\n{code}"));
    }
    parts.join("\n\n")
}
