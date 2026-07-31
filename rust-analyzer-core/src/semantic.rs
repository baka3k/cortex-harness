//! Phase 4 — Semantic enrichment port of `SemanticInferenceEngine`.
//!
//! Re-implements the four-signal scoring engine (naming / type / body / usage)
//! from `tools.common.semantic_inference` using pre-compiled regexes for fast
//! classification. Pre-compilation happens once at engine construction; per-
//! function scoring is O(body length) with no allocation for the common case.

use std::collections::HashMap;

use once_cell::sync::Lazy;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use regex::Regex;

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

/// Single (pattern, intent, confidence) tuple for naming classification.
struct VerbPattern {
    pattern: Regex,
    intent: &'static str,
    confidence: f32,
}

/// Pre-compiled verb patterns — exact 1:1 port of `_VERB_PATTERNS`.
static VERB_PATTERNS: Lazy<Vec<VerbPattern>> = Lazy::new(|| {
    let raw: &[(&str, &'static str, f32)] = &[
        // Retrieval
        (r"^get", INTENT_RETRIEVAL, 0.95),
        (r"^fetch", INTENT_RETRIEVAL, 0.95),
        (r"^retrieve", INTENT_RETRIEVAL, 0.90),
        (r"^find", INTENT_RETRIEVAL, 0.90),
        (r"^search", INTENT_RETRIEVAL, 0.88),
        (r"^query", INTENT_RETRIEVAL, 0.88),
        (r"^list", INTENT_RETRIEVAL, 0.85),
        (r"^select", INTENT_RETRIEVAL, 0.82),
        // IO read
        (r"^load", INTENT_IO_READ, 0.90),
        (r"^read", INTENT_IO_READ, 0.90),
        (r"^import", INTENT_IO_READ, 0.82),
        (r"^download", INTENT_IO_READ, 0.88),
        // Mutation
        (r"^set", INTENT_MUTATION, 0.95),
        (r"^update", INTENT_MUTATION, 0.95),
        (r"^modify", INTENT_MUTATION, 0.90),
        (r"^change", INTENT_MUTATION, 0.88),
        (r"^apply", INTENT_MUTATION, 0.78),
        (r"^assign", INTENT_MUTATION, 0.85),
        (r"^patch", INTENT_MUTATION, 0.88),
        (r"^replace", INTENT_MUTATION, 0.85),
        (r"^reset", INTENT_MUTATION, 0.82),
        // IO write
        (r"^save", INTENT_IO_WRITE, 0.90),
        (r"^write", INTENT_IO_WRITE, 0.90),
        (r"^store", INTENT_IO_WRITE, 0.90),
        (r"^persist", INTENT_IO_WRITE, 0.88),
        (r"^upload", INTENT_IO_WRITE, 0.88),
        (r"^export", INTENT_IO_WRITE, 0.82),
        (r"^publish", INTENT_IO_WRITE, 0.82),
        (r"^send", INTENT_IO_WRITE, 0.85),
        (r"^push", INTENT_IO_WRITE, 0.75),
        // Predicate
        (r"^is", INTENT_PREDICATE, 0.95),
        (r"^has", INTENT_PREDICATE, 0.95),
        (r"^can", INTENT_PREDICATE, 0.93),
        (r"^should", INTENT_PREDICATE, 0.93),
        (r"^will", INTENT_PREDICATE, 0.88),
        (r"^contains", INTENT_PREDICATE, 0.85),
        (r"^exists", INTENT_PREDICATE, 0.85),
        (r"^allows", INTENT_PREDICATE, 0.82),
        (r"^supports", INTENT_PREDICATE, 0.82),
        // Validation
        (r"^check", INTENT_VALIDATION, 0.88),
        (r"^validate", INTENT_VALIDATION, 0.95),
        (r"^verify", INTENT_VALIDATION, 0.90),
        (r"^ensure", INTENT_VALIDATION, 0.85),
        (r"^assert", INTENT_VALIDATION, 0.88),
        (r"^require", INTENT_VALIDATION, 0.75),
        // Computation
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
        // Factory
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
        // Deletion
        (r"^delete", INTENT_DELETION, 0.95),
        (r"^remove", INTENT_DELETION, 0.93),
        (r"^destroy", INTENT_DELETION, 0.90),
        (r"^clear", INTENT_DELETION, 0.88),
        (r"^purge", INTENT_DELETION, 0.88),
        (r"^drop", INTENT_DELETION, 0.82),
        (r"^discard", INTENT_DELETION, 0.82),
        (r"^unset", INTENT_DELETION, 0.80),
        (r"^revoke", INTENT_DELETION, 0.80),
        // Transformation
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
        // Side effect
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
        // Mutation (add/append)
        (r"^add", INTENT_MUTATION, 0.85),
        (r"^append", INTENT_MUTATION, 0.85),
        (r"^insert", INTENT_MUTATION, 0.85),
        (r"^prepend", INTENT_MUTATION, 0.83),
        (r"^attach", INTENT_MUTATION, 0.80),
        (r"^merge", INTENT_MUTATION, 0.80),
        (r"^inject", INTENT_MUTATION, 0.75),
        (r"^receive", INTENT_IO_READ, 0.80),
    ];

    raw.iter()
        .map(|(pat, intent, conf)| VerbPattern {
            pattern: Regex::new(pat).expect("valid verb regex"),
            intent,
            confidence: *conf,
        })
        .collect()
});

/// Body patterns — per-intent pattern lists. Port of `_BODY_PATTERNS`.
static BODY_PATTERNS: Lazy<HashMap<&'static str, Vec<Regex>>> = Lazy::new(|| {
    let mut map: HashMap<&'static str, Vec<Regex>> = HashMap::new();
    let raw: &[(&str, &[&str])] = &[
        (
            INTENT_IO_READ,
            &[
                r"\bfetch\s*\(",
                r"\baxios\b",
                r"\bHttpClient\b",
                r"\.get\s*\(",
                r"await\s+\w+\.find",
                r"\bprisma\.\w+\.find",
                r"\bknex\(",
                r"localStorage\.getItem",
                r"sessionStorage\.getItem",
                r"\bfs\.read",
                r"\bsupabase\.",
            ],
        ),
        (
            INTENT_IO_WRITE,
            &[
                r"\.post\s*\(",
                r"\.put\s*\(",
                r"\.patch\s*\(",
                r"\.save\s*\(",
                r"\.create\s*\(",
                r"\.insert\s*\(",
                r"\.update\s*\(",
                r"\.upsert\s*\(",
                r"localStorage\.setItem",
                r"sessionStorage\.setItem",
                r"\bfs\.write",
            ],
        ),
        (
            INTENT_VALIDATION,
            &[
                r"\bthrow\s+new\b",
                r"\bthrow\b",
                r"throw.*Error",
                r"if\s*\(.*\)\s*throw",
            ],
        ),
        (
            INTENT_COMPUTATION,
            &[
                r"\bfor\s*\(",
                r"\bwhile\s*\(",
                r"\b\.reduce\s*\(",
                r"\bMath\.",
                r"[+\-*/]\s*\w",
                r"\bparseInt\b",
                r"\bparseFloat\b",
            ],
        ),
        (
            INTENT_SIDE_EFFECT,
            &[
                r"\bemit\s*\(",
                r"\.dispatch\s*\(",
                r"\bconsole\.",
                r"setState\s*\(",
                r"this\.state\s*=",
                r"\.addEventListener\s*\(",
                r"\bsetTimeout\s*\(",
                r"\bsetInterval\s*\(",
            ],
        ),
        (
            INTENT_TRANSFORMATION,
            &[
                r"\b\.map\s*\(",
                r"\b\.filter\s*\(",
                r"\bJSON\.parse\b",
                r"\bJSON\.stringify\b",
                r"\bObject\.assign\b",
                r"\bSpread\b|\.\.\.",
            ],
        ),
    ];

    for (intent, pats) in raw {
        let compiled = pats
            .iter()
            .map(|p| Regex::new(p).expect("valid body regex"))
            .collect();
        map.insert(*intent, compiled);
    }
    map
});

const _BODY_MATCH_WEIGHT: f32 = 0.25;

/// Naming signal — `(intent, confidence)`.
fn naming_signal(name: &str) -> (&'static str, f32) {
    if name.is_empty() {
        return (INTENT_UNKNOWN, 0.0);
    }
    if matches!(name, "constructor" | "__init__" | "initialize") {
        return (INTENT_FACTORY, 0.90);
    }
    // Event handler prefix `onXxx`
    if name.len() >= 3 && name.starts_with("on") {
        let bytes = name.as_bytes();
        if bytes[2].is_ascii_uppercase() || bytes[2] == b'_' {
            return (INTENT_SIDE_EFFECT, 0.72);
        }
    }
    for vp in VERB_PATTERNS.iter() {
        if vp.pattern.is_match(name) {
            return (vp.intent, vp.confidence);
        }
    }
    if name.len() <= 3 {
        return (INTENT_UNKNOWN, 0.15);
    }
    (INTENT_UNKNOWN, 0.0)
}

/// Body signal — return dominant intent (by match count) and confidence.
fn body_signal(code: &str) -> (Option<&'static str>, f32) {
    if code.is_empty() {
        return (None, 0.0);
    }
    let mut best: Option<(&'static str, f32)> = None;
    for (intent, patterns) in BODY_PATTERNS.iter() {
        let count = patterns.iter().filter(|p| p.is_match(code)).count();
        if count > 0 {
            let conf = ((count as f32) * _BODY_MATCH_WEIGHT).min(1.0);
            match best {
                None => best = Some((intent, conf)),
                Some((_, prev)) if conf > prev => best = Some((intent, conf)),
                _ => {}
            }
        }
    }
    match best {
        Some((i, c)) => (Some(i), (c * 1000.0).round() / 1000.0),
        None => (None, 0.0),
    }
}

/// Resolve the final intent following the same priority as Python:
/// usage ≥0.50 → usage; else type ≥0.65 (and disagrees with naming) → type;
/// else naming ≥0.60 and not UNKNOWN → naming; else body ≥0.50 → body;
/// else naming (possibly UNKNOWN).
fn resolve_intent(
    naming_intent: &'static str,
    naming_conf: f32,
    body_intent: Option<&'static str>,
    body_conf: f32,
) -> &'static str {
    if naming_intent != INTENT_UNKNOWN && naming_conf >= 0.60 {
        return naming_intent;
    }
    if let Some(bi) = body_intent {
        if body_conf >= 0.50 {
            return bi;
        }
    }
    naming_intent
}

/// Generate the natural-language summary for a (intent, subject) pair.
fn generate_summary(intent: &str, subject: &str, arity: u32, has_comment: bool, comment: &str) -> String {
    if has_comment {
        return comment
            .trim_start_matches('/')
            .trim_start_matches('*')
            .lines()
            .next()
            .unwrap_or("")
            .trim()
            .to_string();
    }
    let template = match intent {
        INTENT_RETRIEVAL => format!("Retrieves {}", subject),
        INTENT_IO_READ => format!("Reads {} from external source", subject),
        INTENT_MUTATION => format!("Updates or modifies {}", subject),
        INTENT_IO_WRITE => format!("Writes {} to persistent storage", subject),
        INTENT_PREDICATE => format!("Checks whether {}", subject),
        INTENT_VALIDATION => format!("Validates {}", subject),
        INTENT_COMPUTATION => format!("Calculates {}", subject),
        INTENT_FACTORY => format!("Creates a new {}", subject),
        INTENT_DELETION => format!("Deletes {}", subject),
        INTENT_SIDE_EFFECT => format!("Performs {} operation", subject),
        INTENT_TRANSFORMATION => format!("Transforms {}", subject),
        _ => format!("Performs unknown operation on {}", subject),
    };

    let mut summary = template;
    let mut enrichments: Vec<String> = Vec::new();
    if arity > 0 {
        if arity > 1 {
            enrichments.push(format!("takes {} parameters", arity));
        } else {
            enrichments.push("takes 1 parameter".to_string());
        }
    }
    if !enrichments.is_empty() {
        summary.push_str(&format!(" ({})", enrichments.join(", ")));
    }
    summary
}

/// Strip verb prefix and convert remainder to natural-language subject.
fn extract_subject(name: &str) -> String {
    // Drop first verb-prefix chunk
    let mut rest = name.to_string();
    for vp in VERB_PATTERNS.iter() {
        if let Some(m) = vp.pattern.find(name) {
            if m.start() == 0 {
                rest = name[m.end()..].to_string();
                break;
            }
        }
    }
    if rest.is_empty() {
        return "data".to_string();
    }
    // Drop leading underscores
    let rest = rest.trim_start_matches('_');
    // Strip leading articles
    let lower = rest.to_ascii_lowercase();
    let rest = if lower.starts_with("the ") {
        &rest[4..]
    } else if lower.starts_with("a ") {
        &rest[2..]
    } else if lower.starts_with("an ") {
        &rest[3..]
    } else {
        rest
    };
    // camelCase → space-separated (simple heuristic: insert space before each uppercase run)
    let mut spaced = String::with_capacity(rest.len());
    let mut prev_lower = false;
    for c in rest.chars() {
        if c.is_ascii_uppercase() {
            if prev_lower {
                spaced.push(' ');
            }
            spaced.extend(c.to_lowercase());
            prev_lower = false;
        } else if c == '_' {
            spaced.push(' ');
            prev_lower = false;
        } else {
            spaced.push(c);
            prev_lower = c.is_ascii_lowercase() || c.is_ascii_digit();
        }
    }
    let cleaned: String = spaced.split_whitespace().collect::<Vec<_>>().join(" ");
    if cleaned.is_empty() {
        "data".to_string()
    } else {
        cleaned
    }
}

/// One function's enrichment payload (the dict the Python side will receive).
pub struct EnrichedFunc {
    pub intent: &'static str,
    pub summary: String,
    pub confidence: f32,
    pub side_effect: bool,
    pub inferred_doc: bool,
}

/// Compute the enrichment result for a single function dict.
pub fn analyze(
    name: &str,
    code: &str,
    comment: &str,
    arity: u32,
    exported: bool,
) -> EnrichedFunc {
    let (naming_intent, naming_conf) = naming_signal(name);

    let (body_intent, body_conf) = body_signal(code);

    let intent = resolve_intent(naming_intent, naming_conf, body_intent, body_conf);

    let subject = extract_subject(name);
    let has_comment = !comment.is_empty();
    let summary = generate_summary(intent, &subject, arity, has_comment, comment);

    let inferred_doc = !has_comment;

    // Confidence — weighted sum of (naming, type, usage, body)
    // type and usage are zero in the C++ fast path (no TS type annotations here)
    let signals_naming = naming_conf;
    let signals_type = 0.0_f32;
    let signals_usage = 0.0_f32;
    let signals_body = body_conf;
    let confidence = compute_confidence(signals_naming, signals_type, signals_usage, signals_body, exported);

    let side_effect = matches!(intent, INTENT_SIDE_EFFECT | INTENT_IO_WRITE);

    EnrichedFunc {
        intent,
        summary,
        confidence,
        side_effect,
        inferred_doc,
    }
}

/// Replicates the Python `ConfidenceScorer.score_dict` shape:
///   weighted sum of {naming:0.40, type:0.20, usage:0.30, body:0.10}
/// with a tiny bonus when exported.
fn compute_confidence(naming: f32, ty: f32, usage: f32, body: f32, exported: bool) -> f32 {
    let base = 0.40 * naming + 0.20 * ty + 0.30 * usage + 0.10 * body;
    let bonus = if exported { 0.05 } else { 0.0 };
    let mut total = (base + bonus).min(1.0);
    if total < 0.0 {
        total = 0.0;
    }
    (total * 1000.0).round() / 1000.0
}

// ─────────────────────────────────────────────────────────────────────────────
// PyO3 surface
// ─────────────────────────────────────────────────────────────────────────────

/// Phase 4 entry point — mutate function dicts in place.
///
/// Mirrors `SemanticInferenceEngine.enrich_corpus(functions, calls)`.
#[pyfunction]
pub fn enrich_corpus_py(py: Python, functions: &PyList, _calls: &PyList) -> PyResult<()> {
    for idx in 0..functions.len() {
        let item = functions.get_item(idx)?;
        let dict = item.downcast::<PyDict>()?;

        let name = dict_field(dict, "name");
        let code = dict_field(dict, "code");
        let comment = dict_field(dict, "comment");
        let arity = dict
            .get_item("arity")
            .ok()
            .flatten()
            .and_then(|v| v.extract::<u32>().ok())
            .unwrap_or(0);
        let exported = dict
            .get_item("exported")
            .ok()
            .flatten()
            .and_then(|v| v.extract::<bool>().ok())
            .unwrap_or(false);

        let result = analyze(&name, &code, &comment, arity, exported);

        dict.set_item("intent", result.intent)?;
        dict.set_item("inferred_doc", result.inferred_doc)?;
        dict.set_item("doc_confidence", result.confidence)?;
        dict.set_item("side_effect", result.side_effect)?;

        // Build signals dict
        let signals = PyDict::new(py);
        let (naming_conf, _body_conf) = (naming_signal(&name).1, body_signal(&code).1);
        signals.set_item("naming", (naming_conf * 1000.0).round() / 1000.0)?;
        signals.set_item("type", 0.0_f32)?;
        signals.set_item("usage", 0.0_f32)?;
        let (_, body_conf_val) = body_signal(&code);
        signals.set_item("body", body_conf_val)?;
        dict.set_item("signals", signals)?;

        // Only overwrite summary when no developer-written comment
        if comment.is_empty() {
            dict.set_item("summary", &result.summary)?;
        }
    }
    Ok(())
}

fn dict_field(dict: &PyDict, key: &str) -> String {
    dict.get_item(key)
        .ok()
        .flatten()
        .and_then(|v| v.extract::<String>().ok())
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn naming_signal_getter() {
        let (i, c) = naming_signal("getUserById");
        assert_eq!(i, INTENT_RETRIEVAL);
        assert!(c > 0.9);
    }

    #[test]
    fn naming_signal_constructor() {
        let (i, _) = naming_signal("__init__");
        assert_eq!(i, INTENT_FACTORY);
    }

    #[test]
    fn naming_signal_unknown_short() {
        // "foo" is a short name with no verb prefix match → matches Python's
        // short-name fallback which returns UNKNOWN with 0.15 confidence.
        let (i, c) = naming_signal("foo");
        assert_eq!(i, INTENT_UNKNOWN);
        assert!((c - 0.15).abs() < 0.001);
    }

    #[test]
    fn body_signal_io_read() {
        // Body with only fetch() (no + / * * chars) — ensures io_read wins
        // over computation's `[+\-*/]\s*\w` regex.
        let (intent, conf) = body_signal("const x = await fetch();");
        assert_eq!(intent, Some(INTENT_IO_READ));
        assert!(conf > 0.0);
    }

    #[test]
    fn body_signal_validation() {
        let (intent, conf) = body_signal("if (x < 0) throw new Error('bad');");
        assert_eq!(intent, Some(INTENT_VALIDATION));
        assert!(conf > 0.0);
    }

    #[test]
    fn extract_subject_get_user() {
        let s = extract_subject("getUserById");
        assert!(s.contains("user"));
    }

    #[test]
    fn analyze_returns_summary() {
        let result = analyze("getUserById", "return db.find();", "", 1, false);
        assert_eq!(result.intent, INTENT_RETRIEVAL);
        assert!(!result.summary.is_empty());
        assert!(result.inferred_doc);
    }

    #[test]
    fn analyze_preserves_developer_comment() {
        let result = analyze("getUserById", "return db.find();", "/// Fetches the current user", 1, false);
        assert!(!result.inferred_doc);
        assert!(result.summary.starts_with("Fetches"));
    }
}