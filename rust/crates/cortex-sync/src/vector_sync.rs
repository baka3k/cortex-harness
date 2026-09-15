//! Phase-06 vector plane — port of `code-tiny/tools/common/primary_vector_sync.py`.
//!
//! Parity contract (plan `260915-analyzer-layer-rust-cutover` phase-06):
//! every function here must reproduce the Python reference byte-for-byte on
//! fixture evidence — the gates G1 (point id), G2 (redaction) and G4
//! (stale filter + collection config) are measured against this module.
//!
//! Deliberate Python-re semantics kept visible in code (spike findings C8):
//! * `str.strip()`/`str.isspace()` treat U+001C..U+001F as whitespace while
//!   Rust's `char::is_whitespace` does not — `py_strip`/`PY_SPACE_CLASS`
//!   close that gap;
//! * `deterministic_point_id` joins `(parser, project_id, root_scope,
//!   symbol_id)` — NOT the positional signature order `(parser, project_id,
//!   symbol_id, root_scope)`;
//! * tier 2 of `redact_text` uses a named backreference, so the plain
//!   `regex` crate cannot express it — `fancy-regex` it is.

use fancy_regex::Regex as FancyRegex;
use serde_json::{Map, Value, json};
use std::sync::OnceLock;
use uuid::Uuid;

use cortex_graph_writer::project_scope::project_id_lookup_key;

/// `_POINT_NAMESPACE` — primary_vector_sync.py:43.
pub const POINT_NAMESPACE: &str = "6694a056-5f64-5e1a-b5fc-b1ef4ec630db";
/// `MAX_VECTOR_TEXT_CHARS` — primary_vector_sync.py:44.
pub const MAX_VECTOR_TEXT_CHARS: usize = 16_000;
/// `qdrant_batch_size` child default (go_analyzer.py `--qdrant-batch-size`).
pub const QDRANT_UPSERT_BATCH: usize = 128;

/// Python `\s` over `str` (CPython `Py_UNICODE_ISSPACE`) additionally matches
/// U+001C..U+001F, which the Rust `\s` class excludes. Used verbatim inside
/// every redaction pattern to keep the engines aligned.
const PY_WS: &str = r"[\s\x{1C}-\x{1F}]";

fn private_key_re() -> &'static FancyRegex {
    static RE: OnceLock<FancyRegex> = OnceLock::new();
    RE.get_or_init(|| {
        FancyRegex::new("(?s)-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----")
            .expect("tier-1 pattern compiles")
    })
}

fn quoted_secret_re() -> &'static FancyRegex {
    static RE: OnceLock<FancyRegex> = OnceLock::new();
    RE.get_or_init(|| {
        FancyRegex::new(&format!(
            "(?sim)([\"']?(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|passwd|secret|token)[\"']?{ws}*[:=]{ws}*)(?P<quote>[\"'])(.*?)(?P=quote)",
            ws = PY_WS,
        ))
        .expect("tier-2 pattern compiles")
    })
}

fn unquoted_secret_re() -> &'static FancyRegex {
    static RE: OnceLock<FancyRegex> = OnceLock::new();
    RE.get_or_init(|| {
        FancyRegex::new(&format!(
            "(?im)([\"']?(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|passwd|secret|token)[\"']?{ws}*[:=]{ws}*)([^{ws},;]+)",
            ws = PY_WS,
        ))
        .expect("tier-3 pattern compiles")
    })
}

/// `str(value or "")` for JSON scalars: null/False/0/""/[]/{} are falsy in
/// Python and collapse to `""` before stringification; the two literal
/// headers built by callers are always plain strings already.
pub(crate) fn py_or_empty_str(value: &Value) -> String {
    match value {
        Value::Null => String::new(),
        Value::Bool(false) => String::new(),
        Value::String(s) => s.clone(),
        other => py_str(other),
    }
}

/// Python `str(x)` over JSON scalars. Containers fall back to a
/// `repr`-shaped rendering — analyzer rows only ever carry scalars in the
/// fields this module stringifies.
pub(crate) fn py_str(value: &Value) -> String {
    match value {
        Value::Null => "None".to_string(),
        Value::Bool(true) => "True".to_string(),
        Value::Bool(false) => "False".to_string(),
        Value::String(s) => s.clone(),
        Value::Number(number) => {
            if let Some(int) = number.as_i64() {
                int.to_string()
            } else if let Some(uint) = number.as_u64() {
                uint.to_string()
            } else {
                // Python float repr is shortest-roundtrip; f64 Display is the
                // same algorithm for every value rows can hold.
                number.to_string()
            }
        }
        Value::Array(items) => {
            let parts: Vec<String> = items.iter().map(py_repr).collect();
            format!("[{}]", parts.join(", "))
        }
        Value::Object(map) => {
            let parts: Vec<String> = map
                .iter()
                .map(|(k, v)| format!("{}: {}", py_repr(&Value::String(k.clone())), py_repr(v)))
                .collect();
            format!("{{{}}}", parts.join(", "))
        }
    }
}

fn py_repr(value: &Value) -> String {
    match value {
        Value::String(s) => {
            if s.contains('\'') && !s.contains('"') {
                format!("\"{}\"", s)
            } else {
                format!("'{}'", s.replace('\\', "\\\\").replace('\'', "\\'"))
            }
        }
        Value::Null => "None".to_string(),
        Value::Bool(true) => "True".to_string(),
        Value::Bool(false) => "False".to_string(),
        other => py_str(other),
    }
}

/// Python truthiness of `x or y` chains: null/False/0/0.0/""/[]/{} are falsy.
fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::String(s) => !s.is_empty(),
        Value::Number(n) => n.as_f64().map(|f| f != 0.0).unwrap_or(true),
        Value::Array(a) => !a.is_empty(),
        Value::Object(m) => !m.is_empty(),
    }
}

/// First truthy value among `row[key]` lookups, `""` when none (Python
/// `str(source.get(a) or source.get(b) or "")` chains).
fn first_or(row: &Map<String, Value>, keys: &[&str]) -> String {
    for key in keys {
        if let Some(value) = row.get(*key)
            && truthy(value)
        {
            return py_str(value);
        }
    }
    String::new()
}

/// `str.strip()` — strips Python's whitespace set, which includes
/// U+001C..U+001F (see module docs).
pub(crate) fn py_strip(value: &str) -> &str {
    let is_space = |c: char| c.is_whitespace() || ('\u{1C}'..='\u{1F}').contains(&c);
    let mut text = value;
    while let Some(stripped) = text.strip_prefix(is_space) {
        text = stripped;
    }
    while let Some(stripped) = text.strip_suffix(is_space) {
        text = stripped;
    }
    text
}

/// Python `str.lower()` (full Unicode lowercasing, like `str.strip` above
/// this is what `deterministic_point_id` applies to `parser` only).
fn py_lower(value: &str) -> String {
    value.to_lowercase()
}

/// `deterministic_point_id` — primary_vector_sync.py:81-89.
///
/// Join order is `(parser, project_id, root_scope, symbol_id)` — swapped
/// against the (parser, project_id, symbol_id, root_scope) signature, which
/// is the hazard the spike flagged; do not "fix" it.
pub fn deterministic_point_id(
    parser: &str,
    project_id: &str,
    symbol_id: &str,
    root_scope: &str,
) -> Result<String, String> {
    let identity = [
        py_lower(py_strip(parser)),
        py_strip(project_id).to_string(),
        py_strip(root_scope).to_string(),
        py_strip(symbol_id).to_string(),
    ]
    .join("\0");
    if py_strip(parser).is_empty()
        || py_strip(project_id).is_empty()
        || py_strip(root_scope).is_empty()
        || py_strip(symbol_id).is_empty()
    {
        return Err(
            "parser, project_id, root_scope, and symbol_id are required for vector identity"
                .to_string(),
        );
    }
    let namespace = Uuid::parse_str(POINT_NAMESPACE)
        .map_err(|error| format!("invalid point namespace: {error}"))?;
    Ok(Uuid::new_v5(&namespace, identity.as_bytes()).to_string())
}

/// `redact_text` — primary_vector_sync.py:69-78. Three ordered passes:
/// private-key block → quoted assignment (backreference) → unquoted
/// assignment. Pass 3 re-scans the output of pass 2 exactly like Python
/// (`key="x"` lands as `key=[REDACTED]` — quotes collapse).
pub fn redact_text(value: &Value) -> String {
    let text = py_or_empty_str(value);
    let text = private_key_re().replace_all(&text, "[REDACTED PRIVATE KEY]");
    let text = redact_quoted(&text);
    unquoted_secret_re()
        .replace_all(&text, |caps: &fancy_regex::Captures| {
            format!("{}[REDACTED]", caps.get(1).map_or("", |m| m.as_str()))
        })
        .into_owned()
}

/// `re.sub` with a callable replacement: `{group1}{quote}[REDACTED]{quote}`.
/// `fancy_regex::replace_all` short-circuits on the first match error and
/// drops that match silently — mirrors `re` by treating an error as a
/// non-match and continuing the scan (fixtures prove it never fires).
fn redact_quoted(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut last = 0usize;
    for match_result in quoted_secret_re().captures_iter(text) {
        let Ok(caps) = match_result else { continue };
        let whole = caps.get(0).expect("tier-2 match spans the whole match");
        if whole.start() < last {
            continue;
        }
        let prefix = caps.get(1).map_or("", |m| m.as_str());
        let quote = caps.name("quote").map_or("", |m| m.as_str());
        out.push_str(&text[last..whole.start()]);
        out.push_str(prefix);
        out.push_str(quote);
        out.push_str("[REDACTED]");
        out.push_str(quote);
        last = whole.end();
    }
    out.push_str(&text[last..]);
    out
}

/// `_bounded_text` — primary_vector_sync.py:92-96: drop `None`/`""` parts,
/// join `\n`, redact, then truncate to `min(max_chars, 16000)` CODE POINTS
/// (Python slices by code point, not byte).
pub fn bounded_text(parts: &[Value], max_chars: usize) -> Result<String, String> {
    if max_chars == 0 {
        return Err("max_chars must be positive".to_string());
    }
    let kept: Vec<String> = parts
        .iter()
        .filter(|part| !part.is_null() && part.as_str() != Some(""))
        .map(py_str)
        .collect();
    let text = redact_text(&Value::String(kept.join("\n")));
    let limit = max_chars.min(MAX_VECTOR_TEXT_CHARS);
    Ok(text.chars().take(limit).collect())
}

/// `VectorDocument` — id + embeddable text + stored payload.
#[derive(Debug, Clone)]
pub struct VectorDocument {
    pub id: String,
    pub text: String,
    pub payload: Map<String, Value>,
}

/// `documents_from_payloads` — primary_vector_sync.py:100-156 for one row.
/// Returns the document or the Python `ValueError` text.
pub fn document_from_payload(
    source: &Map<String, Value>,
    parser: &str,
    root_scope: &str,
    max_chars: usize,
) -> Result<VectorDocument, String> {
    let symbol_id = py_strip(&first_or(source, &["symbol_id", "id"])).to_string();
    let project_id = py_strip(&first_or(source, &["project_id"])).to_string();
    if symbol_id.is_empty() || project_id.is_empty() {
        return Err("vector payloads require symbol_id/id and project_id".to_string());
    }
    let file_path = first_or(source, &["file_path", "path"])
        .replace('\\', "/");
    let name = {
        let raw = source.get("name");
        if raw.map(truthy).unwrap_or(false) {
            py_str(raw.unwrap())
        } else if !file_path.is_empty() {
            file_path.clone()
        } else {
            symbol_id.clone()
        }
    };
    let qualified_name = {
        let raw = source.get("qualified_name");
        if raw.map(truthy).unwrap_or(false) {
            py_str(raw.unwrap())
        } else {
            name.clone()
        }
    };
    let node_type = {
        let mut picked = String::new();
        for key in ["node_type", "kind"] {
            if let Some(value) = source.get(key)
                && truthy(value)
            {
                picked = py_str(value);
                break;
            }
        }
        if picked.is_empty() { "symbol".to_string() } else { picked }
    };
    let mut parts: Vec<Value> = vec![json!(format!("{node_type}: {qualified_name}"))];
    if !file_path.is_empty() {
        parts.push(json!(format!("file: {file_path}")));
    }
    for key in ["summary", "comment", "note", "code"] {
        parts.push(source.get(key).cloned().unwrap_or(Value::Null));
    }
    let text = bounded_text(&parts, max_chars)?;
    let mut payload = Map::new();
    payload.insert("node_type".into(), json!(node_type));
    payload.insert("symbol_id".into(), json!(symbol_id));
    payload.insert("project_id".into(), json!(project_id));
    payload.insert(
        cortex_graph_writer::project_scope::PROJECT_ID_NORMALIZED_FIELD.into(),
        json!(project_id_lookup_key(Some(&project_id)).unwrap_or_default()),
    );
    let project_name = {
        let raw = source.get("project_name");
        if raw.map(truthy).unwrap_or(false) { py_str(raw.unwrap()) } else { project_id.clone() }
    };
    payload.insert("project_name".into(), json!(project_name));
    let language = {
        let raw = source.get("language");
        if raw.map(truthy).unwrap_or(false) { py_str(raw.unwrap()) } else { parser.to_string() }
    };
    payload.insert("language".into(), json!(language));
    payload.insert("repo".into(), json!(first_or(source, &["repo"])));
    payload.insert("file_path".into(), json!(file_path));
    payload.insert("name".into(), json!(name));
    payload.insert("qualified_name".into(), json!(qualified_name));
    payload.insert("parser".into(), json!(parser));
    payload.insert("root_scope".into(), json!(root_scope));
    payload.insert("text".into(), json!(text));
    for key in ["start_line", "end_line"] {
        if let Some(value) = source.get(key)
            && !value.is_null()
        {
            payload.insert(key.into(), value.clone());
        }
    }
    let id = deterministic_point_id(parser, &project_id, &symbol_id, root_scope)?;
    Ok(VectorDocument { id, text, payload })
}

/// `documents_from_rows` — primary_vector_sync.py:159-182 over the
/// embedding-input artifact. Categories arrive as an ordered list of
/// `(name, rows)` pairs (the child serialized its `_prepare_write_rows`
/// dict insertion order); `relations`/`calls` are skipped, every other
/// category contributes payloads with the `default_type` quirk
/// (`category[:-1]` when it ends with `s` — yes, `aliases` becomes
/// `aliase`; that is what the Python reference stores).
pub fn documents_from_categories(
    categories: &[(String, Vec<Map<String, Value>>)],
    parser: &str,
    root_scope: &str,
    max_chars: usize,
) -> Result<Vec<VectorDocument>, String> {
    let mut documents = Vec::new();
    for (category, rows) in categories {
        if category == "relations" || category == "calls" {
            continue;
        }
        let default_type = if category.ends_with('s') {
            category[..category.len() - 1].to_string()
        } else {
            category.clone()
        };
        for row in rows {
            let mut item = row.clone();
            if !item.contains_key("symbol_id") {
                let id = item.get("id").cloned().unwrap_or(Value::Null);
                item.insert("symbol_id".into(), id);
            }
            if !item.get("node_type").map(truthy).unwrap_or(false) {
                let kind = item.get("kind").filter(|v| truthy(v)).cloned();
                item.insert(
                    "node_type".into(),
                    kind.unwrap_or_else(|| json!(default_type)),
                );
            }
            documents.push(document_from_payload(&item, parser, root_scope, max_chars)?);
        }
    }
    Ok(documents)
}

/// `_delete_stale` filter dict — primary_vector_sync.py:226-259. Returns
/// `None` for the early-out (`!full_replace && paths.is_empty()`).
///
/// Key order matters for byte-match against the Python reference: this
/// workspace builds serde_json with `preserve_order`, so the serialized bytes
/// follow the INSERTION order below — deliberately the same order
/// primary_vector_sync._delete_stale builds its dict (`must`, then `must_not`;
/// `key`, then `match`). Pinned byte-for-byte by the g4 fixture tests.
pub fn stale_filter(
    parser: &str,
    project_id: &str,
    root_scope: &str,
    cleanup_paths: &[String],
    keep_ids: &[String],
    full_replace: bool,
) -> Option<Value> {
    let mut paths: Vec<String> = cleanup_paths
        .iter()
        .map(|path| path.replace('\\', "/"))
        .filter(|path| !path.is_empty())
        .collect::<std::collections::BTreeSet<String>>()
        .into_iter()
        .collect();
    paths.sort();
    if !full_replace && paths.is_empty() {
        return None;
    }
    let mut must: Vec<Value> = vec![
        json!({
            "key": cortex_graph_writer::project_scope::PROJECT_ID_NORMALIZED_FIELD,
            "match": {"value": project_id_lookup_key(Some(project_id)).unwrap_or_default()},
        }),
        json!({"key": "parser", "match": {"value": parser}}),
        json!({"key": "root_scope", "match": {"value": root_scope}}),
    ];
    if !full_replace {
        must.push(json!({"key": "file_path", "match": {"any": paths}}));
    }
    let mut filter = Map::new();
    filter.insert("must".into(), Value::Array(must));
    if !keep_ids.is_empty() {
        filter.insert(
            "must_not".into(),
            json!([{"has_id": keep_ids.iter().map(|id| Value::String(id.clone())).collect::<Vec<_>>()}]),
        );
    }
    Some(Value::Object(filter))
}

/// `SCOPE_INDEX_FIELDS` — primary_vector_sync.py:188-193 (tuple order is
/// the create_payload_index call order).
pub const SCOPE_INDEX_FIELDS: [&str; 4] = [
    cortex_graph_writer::project_scope::PROJECT_ID_NORMALIZED_FIELD,
    "parser",
    "root_scope",
    "file_path",
];

/// `vector_configured` — bool((url or "").strip()). Parity surface for the
/// embedding-pass gate (unit-tested); the orchestrator currently gates on
/// `Args::qdrant_url`, so mark it retained rather than delete.
#[allow(dead_code)]
pub fn vector_configured(url: Option<&str>) -> bool {
    url.map(|value| !py_strip(value).is_empty()).unwrap_or(false)
}

/// `_COLLECTION_RE.fullmatch` — validate_target, primary_vector_sync.py:64-67.
pub fn validate_collection_name(collection: &str) -> Result<(), String> {
    let ok = !collection.is_empty()
        && collection.chars().all(|c| {
            c.is_ascii_alphanumeric() || matches!(c, '_' | '-' | '.')
        });
    if ok {
        Ok(())
    } else {
        Err("Qdrant collection must contain only letters, digits, '_', '-', or '.'".to_string())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn row(pairs: &[(&str, Value)]) -> Map<String, Value> {
        let mut map = Map::new();
        for (key, value) in pairs {
            map.insert((*key).to_string(), value.clone());
        }
        map
    }

    // ── G1: point id ────────────────────────────────────────────────────────
    #[test]
    fn point_id_matches_python_reference() {
        // Reference values computed live by CPython's uuid5 (join order
        // parser|project|root_scope|symbol). Bulk fixtures come from
        // scripts/rust_parity/gen_vector_plane_fixtures.py.
        let cases = [
            (
                ("go", "Proj", "func:main", "/repo"),
                "66daf30b-28d0-50d1-9280-d0ec386ed7a0",
            ),
            (
                ("Go ", " Proj ", " func:main ", " /repo "),
                "66daf30b-28d0-50d1-9280-d0ec386ed7a0",
            ),
        ];
        for ((parser, project, symbol, scope), expected) in cases {
            assert_eq!(
                deterministic_point_id(parser, project, symbol, scope).unwrap(),
                expected,
                "join order parser|project|scope|symbol must match Python"
            );
        }
    }

    #[test]
    fn point_id_lowercases_parser_only() {
        let a = deterministic_point_id("GO", "P", "S", "R").unwrap();
        let b = deterministic_point_id("go", "P", "S", "R").unwrap();
        assert_eq!(a, b);
        let c = deterministic_point_id("go", "P", "s", "R").unwrap();
        assert_ne!(a, c, "symbol case must be preserved");
    }

    #[test]
    fn point_id_rejects_empty_fields() {
        assert!(deterministic_point_id("go", "  ", "S", "R").is_err());
        assert!(deterministic_point_id("go", "P", "", "R").is_err());
    }

    #[test]
    fn point_id_strips_unicode_separators() {
        // Python strip() eats U+001C..U+001F; a naive Rust trim would not.
        let with = deterministic_point_id("go", "P\u{1C}", "S", "R").unwrap();
        let without = deterministic_point_id("go", "P", "S", "R").unwrap();
        assert_eq!(with, without);
    }

    // ── G2: redaction ───────────────────────────────────────────────────────
    #[test]
    fn redacts_private_key_blocks() {
        let text = json!("head\n-----BEGIN RSA PRIVATE KEY-----\nMIIBOg\n-----END RSA PRIVATE KEY-----\ntail"); // sensitive-guard:allow synthetic PEM-shape fixture
        let redacted = redact_text(&text);
        assert_eq!(redacted, "head\n[REDACTED PRIVATE KEY]\ntail");
    }

    #[test]
    fn quoted_then_unquoted_pass_chain() {
        // tier 2 keeps the quotes, tier 3 collapses them — Python order quirk.
        assert_eq!(redact_text(&json!("api_key=\"abc\"")), "api_key=[REDACTED]");
        assert_eq!(redact_text(&json!("password: 'p1'")), "password: [REDACTED]"); // sensitive-guard:allow synthetic redaction test input
    }

    #[test]
    fn unquoted_redaction_stops_at_python_whitespace() {
        let text = json!("secret=abc\u{1C}def");
        // Python \s stops the value at U+001C; plain Rust \s would not.
        assert_eq!(redact_text(&text), "secret=[REDACTED]\u{1C}def");
    }

    #[test]
    fn redaction_case_insensitive_keywords() {
        assert_eq!(redact_text(&json!("API_KEY = xy12")), "API_KEY = [REDACTED]");
    }

    // ── bounded text ────────────────────────────────────────────────────────
    #[test]
    fn bounded_text_skips_empty_parts_and_truncates_by_codepoint() {
        let parts = vec![json!("file: a.go"), Value::Null, json!(""), json!("émoji 🚀 tail")];
        let text = bounded_text(&parts, 2000).unwrap();
        assert_eq!(text, "file: a.go\némoji 🚀 tail");
        let cut = bounded_text(&parts, 11).unwrap();
        assert_eq!(cut.chars().count(), 11);
        assert_eq!(cut, "file: a.go\n");
    }

    #[test]
    fn bounded_text_rejects_zero_max_chars() {
        assert!(bounded_text(&[json!("x")], 0).is_err());
    }

    #[test]
    fn bounded_text_caps_at_16000() {
        let long = "a".repeat(20_000);
        let text = bounded_text(&[json!(long)], 99_999).unwrap();
        assert_eq!(text.chars().count(), MAX_VECTOR_TEXT_CHARS);
    }

    // ── documents ───────────────────────────────────────────────────────────
    #[test]
    fn document_maps_row_fields_python_style() {
        let source = row(&[
            ("id", json!("fn:run")),
            ("project_id", json!("proj")),
            ("file_path", json!("src\\main.go")),
            ("name", json!("run")),
            ("qualified_name", json!("main.run")),
            ("node_type", json!("function")),
            ("code", json!("func run() {}")),
            ("summary", Value::Null),
            ("start_line", json!(3)),
        ]);
        let doc = document_from_payload(&source, "go", "/repo", 4000).unwrap();
        assert_eq!(doc.text, "function: main.run\nfile: src/main.go\nfunc run() {}");
        assert_eq!(doc.payload["file_path"], json!("src/main.go"));
        assert_eq!(doc.payload["start_line"], json!(3));
        assert!(!doc.payload.contains_key("end_line"));
        assert_eq!(doc.payload["project_id_normalized"], json!("proj"));
        assert_eq!(doc.id, deterministic_point_id("go", "proj", "fn:run", "/repo").unwrap());
    }

    #[test]
    fn documents_skip_relations_calls_and_singularize_categories() {
        let categories = vec![
            ("relations".to_string(), vec![row(&[("id", json!("r1"))])]),
            ("calls".to_string(), vec![row(&[("id", json!("c1"))])]),
            ("aliases".to_string(), vec![row(&[
                ("id", json!("a1")),
                ("project_id", json!("p")),
            ])]),
        ];
        let docs = documents_from_categories(&categories, "go", "/r", 4000).unwrap();
        assert_eq!(docs.len(), 1);
        // category[:-1] quirk: "aliases" -> "aliase"
        assert_eq!(docs[0].payload["node_type"], json!("aliase"));
    }

    #[test]
    fn documents_require_symbol_and_project() {
        let categories = vec![("files".to_string(), vec![row(&[("id", json!("f1"))])])];
        let error = documents_from_categories(&categories, "go", "/r", 4000).unwrap_err();
        assert_eq!(error, "vector payloads require symbol_id/id and project_id");
    }

    // ── G4: stale filter shape ──────────────────────────────────────────────
    #[test]
    fn stale_filter_shape_and_early_out() {
        assert!(stale_filter("go", "p", "/r", &[], &[], false).is_none());
        let keep = vec!["id-keep".to_string()];
        let paths = vec!["b.go".to_string(), "a.go".to_string(), "b.go".to_string()];
        let filter = stale_filter("go", "p", "/r", &paths, &keep, false).unwrap();
        let text = serde_json::to_string(&filter).unwrap();
        assert_eq!(
            text,
            r#"{"must":[{"key":"project_id_normalized","match":{"value":"p"}},{"key":"parser","match":{"value":"go"}},{"key":"root_scope","match":{"value":"/r"}},{"key":"file_path","match":{"any":["a.go","b.go"]}}],"must_not":[{"has_id":["id-keep"]}]}"#
        );
    }

    #[test]
    fn stale_filter_full_replace_drops_file_path() {
        let filter = stale_filter("go", "P", "/r", &[], &[], true).unwrap();
        let text = serde_json::to_string(&filter).unwrap();
        assert_eq!(
            text,
            r#"{"must":[{"key":"project_id_normalized","match":{"value":"p"}},{"key":"parser","match":{"value":"go"}},{"key":"root_scope","match":{"value":"/r"}}]}"#
        );
    }

    #[test]
    fn vector_configured_and_collection_validation() {
        assert!(vector_configured(Some(" /x ")));
        assert!(!vector_configured(Some("  ")));
        assert!(!vector_configured(None));
        assert!(validate_collection_name("proj_abc-1.__x2").is_ok());
        assert!(validate_collection_name("bad name").is_err());
        assert!(validate_collection_name("").is_err());
    }
}
