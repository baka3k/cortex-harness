//! Port của `code-tiny/tools/common/query_intent_classifier.py` + weight profiles
//! từ `retrieval_scorer.py`.
//!
//! Keyword matching là **substring** (`kw in q`) — không word boundary, replicate
//! đúng (nghĩa là "like" khớp cả "unlikely"). Regex dùng `(?i)` tương đương `re.I`.
//! Golden fixtures: `tests/fixtures/intent_golden.json`.

use regex::Regex;
use std::sync::OnceLock;

pub const INTENT_SEMANTIC: &str = "semantic";
pub const INTENT_STRUCTURAL: &str = "structural";
pub const INTENT_TEMPORAL: &str = "temporal";
pub const INTENT_DEFAULT: &str = "default";

/// Weight profiles — port nguyên văn `retrieval_scorer.py`.
/// Thứ tự key cố định: semantic, keyword, graph, freshness, confidence, usage.
pub fn weights_for_profile(intent: &str) -> &'static [(&'static str, f64)] {
    const DEFAULT: &[(&str, f64)] = &[
        ("semantic", 0.35),
        ("keyword", 0.20),
        ("graph", 0.20),
        ("freshness", 0.10),
        ("confidence", 0.10),
        ("usage", 0.05),
    ];
    const SEMANTIC: &[(&str, f64)] = &[
        ("semantic", 0.50),
        ("keyword", 0.15),
        ("graph", 0.10),
        ("freshness", 0.05),
        ("confidence", 0.15),
        ("usage", 0.05),
    ];
    const STRUCTURAL: &[(&str, f64)] = &[
        ("semantic", 0.20),
        ("keyword", 0.10),
        ("graph", 0.50),
        ("freshness", 0.05),
        ("confidence", 0.10),
        ("usage", 0.05),
    ];
    const TEMPORAL: &[(&str, f64)] = &[
        ("semantic", 0.20),
        ("keyword", 0.15),
        ("graph", 0.10),
        ("freshness", 0.45),
        ("confidence", 0.05),
        ("usage", 0.05),
    ];
    match intent {
        INTENT_SEMANTIC => SEMANTIC,
        INTENT_STRUCTURAL => STRUCTURAL,
        INTENT_TEMPORAL => TEMPORAL,
        _ => DEFAULT,
    }
}

/// Tương đương `get_weight_profile(intent)` — unknown intent → DEFAULT.
pub fn get_weight_profile(intent: &str) -> Vec<(&'static str, f64)> {
    weights_for_profile(intent).to_vec()
}

const STRUCTURAL_KEYWORDS: &[&str] = &[
    "who calls",
    "callers of",
    "callers",
    "call graph",
    "call site",
    "call sites",
    "called by",
    "calls",
    "where is",
    "where are",
    "dependencies of",
    "dependency",
    "dependents",
    "who uses",
    "used by",
    "uses of",
    "references to",
    "reference graph",
    "imports",
    "importers",
    "inherits from",
    "extends",
    "implements",
    "overrides",
    "subclasses",
    "subclass",
    "parent class",
    "superclass",
    "interface",
    "related to",
    "connected to",
    "neighbors of",
    "path from",
    "path to",
    "trace from",
    "trace to",
    "flow from",
    "graph neighbors",
    "graph proximity",
    "entry points",
    "entry point",
];

const TEMPORAL_KEYWORDS: &[&str] = &[
    "recent changes",
    "recently changed",
    "recently modified",
    "recently updated",
    "last modified",
    "last updated",
    "last changed",
    "just updated",
    "just changed",
    "dirty",
    "outdated",
    "stale",
    "fresh",
    "freshness",
    "what changed",
    "what has changed",
    "new changes",
    "latest changes",
    "latest version",
    "this week",
    "today",
    "yesterday",
    "since yesterday",
    "since last",
    "update history",
    "modified files",
    "changed files",
    "uncommitted",
    "incremental",
];

const SEMANTIC_KEYWORDS: &[&str] = &[
    "similar to",
    "similar logic",
    "like",
    "related logic",
    "semantically",
    "what does",
    "what do",
    "explain",
    "describe",
    "how does",
    "how do",
    "purpose of",
    "meaning of",
    "intent of",
    "equivalent to",
    "analogous",
    "find similar",
    "looks like",
    "same as",
    "concept",
    "conceptually",
];

struct Pattern {
    source: &'static str,
    intent: &'static str,
    regex: Regex,
}

fn patterns() -> &'static [Pattern] {
    static PATTERNS: OnceLock<Vec<Pattern>> = OnceLock::new();
    PATTERNS.get_or_init(|| {
        let defs: &[(&str, &str)] = &[
            // Structural
            (r"\bwho\s+(calls|uses|imports|references)\b", INTENT_STRUCTURAL),
            (r"\bcall(er|ers|ed|ing)?\s+(of|graph)\b", INTENT_STRUCTURAL),
            (r"\bdepend(s|enc(y|ies))?\s+of\b", INTENT_STRUCTURAL),
            (r"\bwhere\s+is\s+.+\s+used\b", INTENT_STRUCTURAL),
            (r"\b(trace|path|flow)\s+(from|to|between)\b", INTENT_STRUCTURAL),
            // Temporal
            (r"\b(recent(ly)?|last|latest)\s+(change|update|modif)", INTENT_TEMPORAL),
            (r"\b(dirty|stale|fresh|outdated|uncommitted)\b", INTENT_TEMPORAL),
            (r"\b(this|last)\s+(week|month|day|commit)\b", INTENT_TEMPORAL),
            // Semantic
            (r"\b(similar|like|analogous|equivalent)\s+(to|logic|code)\b", INTENT_SEMANTIC),
            (r"\b(explain|describe|what\s+(does|is|do))\b", INTENT_SEMANTIC),
        ];
        defs.iter()
            .map(|(source, intent)| {
                let regex = Regex::new(&format!(r"(?i){source}")).expect("pattern tĩnh hợp lệ");
                Pattern {
                    source,
                    intent,
                    regex,
                }
            })
            .collect()
    })
}

fn matches_any(q: &str, keywords: &[&str]) -> bool {
    keywords.iter().any(|kw| q.contains(kw))
}

/// Tương đương `classify_query(query)` — trả một trong 4 intent strings.
pub fn classify_query(query: &str) -> &'static str {
    let q = query.trim().to_lowercase();
    if q.is_empty() {
        return INTENT_DEFAULT;
    }
    // Thứ tự: structural (đặc trưng nhất) → temporal → semantic (generic nhất).
    if matches_any(&q, STRUCTURAL_KEYWORDS) {
        return INTENT_STRUCTURAL;
    }
    if matches_any(&q, TEMPORAL_KEYWORDS) {
        return INTENT_TEMPORAL;
    }
    if matches_any(&q, SEMANTIC_KEYWORDS) {
        return INTENT_SEMANTIC;
    }
    for pattern in patterns() {
        if pattern.regex.is_match(&q) {
            return pattern.intent;
        }
    }
    INTENT_DEFAULT
}

/// Kết quả giải thích của `classify_query_explain`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Explain {
    pub intent: &'static str,
    pub matched: String,
}

/// Tương đương `classify_query_explain(query)`.
pub fn classify_query_explain(query: &str) -> Explain {
    let q = query.trim().to_lowercase();
    if q.is_empty() {
        return Explain {
            intent: INTENT_DEFAULT,
            matched: "empty query".to_string(),
        };
    }
    for (keywords, intent) in [
        (STRUCTURAL_KEYWORDS, INTENT_STRUCTURAL),
        (TEMPORAL_KEYWORDS, INTENT_TEMPORAL),
        (SEMANTIC_KEYWORDS, INTENT_SEMANTIC),
    ] {
        for kw in keywords {
            if q.contains(kw) {
                return Explain {
                    intent,
                    matched: format!("keyword: '{kw}'"),
                };
            }
        }
    }
    for pattern in patterns() {
        if pattern.regex.is_match(&q) {
            return Explain {
                intent: pattern.intent,
                matched: format!("regex: '{}'", pattern.source),
            };
        }
    }
    Explain {
        intent: INTENT_DEFAULT,
        matched: "fallback".to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn keyword_priority_structural_first() {
        assert_eq!(classify_query("who calls the validateToken function"), INTENT_STRUCTURAL);
        assert_eq!(classify_query("dependencies of UserService"), INTENT_STRUCTURAL);
        assert_eq!(classify_query("what changed this week"), INTENT_TEMPORAL);
        assert_eq!(classify_query("find similar logic to payment flow"), INTENT_SEMANTIC);
        assert_eq!(classify_query(""), INTENT_DEFAULT);
        assert_eq!(classify_query("   "), INTENT_DEFAULT);
        assert_eq!(classify_query("get me coffee"), INTENT_DEFAULT);
    }

    #[test]
    fn regex_fallback_when_no_keyword() {
        // "callers" keyword có trong list; pattern check cần query không chứa keyword
        assert_eq!(classify_query("caller of function x"), INTENT_STRUCTURAL);
        assert_eq!(classify_query("recent updated code"), INTENT_TEMPORAL);
    }

    #[test]
    fn explain_reports_source() {
        let e = classify_query_explain("who calls validateToken");
        assert_eq!(e.intent, INTENT_STRUCTURAL);
        assert_eq!(e.matched, "keyword: 'who calls'");
        let e = classify_query_explain("recently updated code");
        assert_eq!(e.intent, INTENT_TEMPORAL);
        assert_eq!(e.matched, "keyword: 'recently updated'");
        // "updated recently" (đảo vị) không khớp keyword nào → fallback
        let e = classify_query_explain("someBrandNewThing updated recently");
        assert_eq!(e.matched, "fallback");
    }

    #[test]
    fn weights_profiles_complete() {
        for intent in [INTENT_SEMANTIC, INTENT_STRUCTURAL, INTENT_TEMPORAL, INTENT_DEFAULT] {
            let w = get_weight_profile(intent);
            assert_eq!(w.len(), 6);
            let total: f64 = w.iter().map(|(_, v)| v).sum();
            assert!((total - 1.0).abs() < 1e-9, "{intent} weights phải sum=1");
        }
        assert_eq!(get_weight_profile("unknown")[0].1, 0.35, "unknown → DEFAULT");
    }
}
