//! Port of the deterministic entity/graph assembly of
//! `doc-tiny/graphrag_ingest_langextract.py` and the normalizers of
//! `doc-tiny/entity_extractors.py`.
//!
//! Covered functions (all kept byte-compatible with the Python reference):
//! - `_normalize_entity_name` / `_entity_id` (uuid5 over NAMESPACE_URL)
//! - `entity_extractors._find_span` / `_normalize_entities` / `_normalize_relations`
//! - `build_graph_components_from_entities` (order-preserving, like a Python dict)
//! - `_select_primary_mention`

use std::collections::HashMap;

use serde_json::{Map, Value};
use sha1::{Digest, Sha1};

/// `uuid.NAMESPACE_URL` — RFC 4122 namespace bytes.
const NAMESPACE_URL: [u8; 16] = [
    0x6b, 0xa7, 0xb8, 0x11, 0x9d, 0xad, 0x11, 0xd1, 0x80, 0xb4, 0x00, 0xc0, 0x4f, 0xd4, 0x30, 0xc8,
];

/// `str(uuid.uuid5(uuid.NAMESPACE_URL, key))` — SHA-1 based UUID v5, hyphenated lowercase.
pub fn uuid5_namespace_url(key: &str) -> String {
    let mut hasher = Sha1::new();
    hasher.update(NAMESPACE_URL);
    hasher.update(key.as_bytes());
    let digest = hasher.finalize();
    let mut bytes = [0u8; 16];
    bytes.copy_from_slice(&digest[..16]);
    bytes[6] = (bytes[6] & 0x0f) | 0x50; // version 5
    bytes[8] = (bytes[8] & 0x3f) | 0x80; // RFC 4122 variant
    format!(
        "{:02x}{:02x}{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}{:02x}{:02x}{:02x}{:02x}",
        bytes[0],
        bytes[1],
        bytes[2],
        bytes[3],
        bytes[4],
        bytes[5],
        bytes[6],
        bytes[7],
        bytes[8],
        bytes[9],
        bytes[10],
        bytes[11],
        bytes[12],
        bytes[13],
        bytes[14],
        bytes[15]
    )
}

/// `uuid.uuid4()` string (only used by the non-merge branch, excluded from parity).
pub fn uuid4_string() -> String {
    let mut bytes = [0u8; 16];
    getrandom::getrandom(&mut bytes).expect("OS RNG unavailable");
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    format!(
        "{:02x}{:02x}{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}{:02x}{:02x}{:02x}{:02x}",
        bytes[0],
        bytes[1],
        bytes[2],
        bytes[3],
        bytes[4],
        bytes[5],
        bytes[6],
        bytes[7],
        bytes[8],
        bytes[9],
        bytes[10],
        bytes[11],
        bytes[12],
        bytes[13],
        bytes[14],
        bytes[15]
    )
}

/// Port of `_normalize_entity_name(name, mode)`.
pub fn normalize_entity_name(name: &str, mode: &str) -> String {
    use regex::Regex;
    static EDGE_RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    static WS_RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    static AGGR_RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    let edge_re = EDGE_RE.get_or_init(|| {
        Regex::new(r#"^["`“”‘’'()\[\]{}<>]+|["`“”‘’'()\[\]{}<>]+$"#).expect("regex")
    });
    let ws_re = WS_RE.get_or_init(|| Regex::new(r"\s+").expect("regex"));
    let aggr_re = AGGR_RE.get_or_init(|| Regex::new(r"[^0-9a-zA-Z+#. ]+").expect("regex"));

    let mut cleaned = py_str_trim(name).to_lowercase();
    if cleaned.is_empty() {
        return String::new();
    }
    cleaned = edge_re.replace_all(&cleaned, "").into_owned();
    cleaned = cleaned.replace(['_', '/', '-'], " ");
    cleaned = py_str_trim(&ws_re.replace_all(&cleaned, " ")).to_string();
    if mode == "aggressive" {
        cleaned = aggr_re.replace_all(&cleaned, " ").into_owned();
        cleaned = py_str_trim(&ws_re.replace_all(&cleaned, " ")).to_string();
    }
    cleaned
}

/// Python `str.strip()` semantics (trim + C0 file separators).
fn py_str_trim(s: &str) -> &str {
    s.trim_matches(|c: char| c.is_whitespace() || matches!(c, '\u{1c}'..='\u{1f}'))
}

/// Port of `_entity_id(name, ent_type, name_norm, project_id_normalized)`.
pub fn entity_id(
    name: &str,
    ent_type: &str,
    name_norm: Option<&str>,
    project_id_normalized: Option<&str>,
) -> String {
    let name_part = match name_norm {
        Some(norm) if !norm.is_empty() => norm.to_string(),
        _ => normalize_entity_name(name, "aggressive"),
    };
    let key = match project_id_normalized {
        Some(project) => format!("{project}::{ent_type}::{name_part}"),
        None => format!("{ent_type}::{name_part}"),
    };
    uuid5_namespace_url(&key)
}

/// Port of `entity_extractors._find_span` — char-index span of the first
/// case-insensitive occurrence of `name` inside `text`.
pub fn find_span(text: &str, name: &str) -> Option<(usize, usize)> {
    if text.is_empty() || name.is_empty() {
        return None;
    }
    let lower_text = text.to_lowercase();
    let lower_name = name.to_lowercase();
    let byte_start = lower_text.find(&lower_name)?;
    let char_start = lower_text[..byte_start].chars().count();
    Some((char_start, char_start + name.chars().count()))
}

fn as_str_field(item: &Value, key: &str) -> String {
    item.get(key)
        .map(|v| match v {
            Value::Null => String::new(),
            Value::String(s) => s.clone(),
            other => other.to_string(),
        })
        .unwrap_or_default()
        .trim()
        .to_string()
}

fn opt_f64(item: &Value, keys: &[&str]) -> Option<f64> {
    for key in keys {
        let num = item
            .get(*key)
            .filter(|v| !v.is_null())
            .and_then(Value::as_f64);
        if num.is_some() {
            return num;
        }
    }
    None
}

fn opt_i64(item: &Value, keys: &[&str]) -> Option<i64> {
    for key in keys {
        let num = item
            .get(*key)
            .filter(|v| !v.is_null())
            .and_then(Value::as_i64);
        if num.is_some() {
            return num;
        }
    }
    None
}

/// Port of `entity_extractors._normalize_entities(entities, text)`.
pub fn normalize_entities(entities: &[Value], text: Option<&str>) -> Vec<Value> {
    let mut normalized = Vec::new();
    for item in entities {
        let name = as_str_field(item, "name");
        if name.is_empty() {
            continue;
        }
        let etype = as_str_field(item, "type");
        let mut confidence = opt_f64(item, &["confidence"]);
        if confidence.is_none() {
            confidence = opt_f64(item, &["score"]);
        }
        let mut start_char = opt_i64(item, &["start_char", "start"]);
        let mut end_char = opt_i64(item, &["end_char", "end"]);
        if let Some(text) = text
            && (start_char.is_none() || end_char.is_none())
            && let Some((s, e)) = find_span(text, &name)
        {
            start_char = Some(s as i64);
            end_char = Some(e as i64);
        }
        let mut payload = Map::new();
        payload.insert("name".to_string(), Value::String(name));
        payload.insert(
            "type".to_string(),
            Value::String(if etype.is_empty() {
                "UNKNOWN".to_string()
            } else {
                etype
            }),
        );
        if let Some(conf) = confidence {
            payload.insert("confidence".to_string(), serde_json::json!(conf));
        }
        if let (Some(s), Some(e)) = (start_char, end_char) {
            payload.insert("start_char".to_string(), serde_json::json!(s));
            payload.insert("end_char".to_string(), serde_json::json!(e));
        }
        normalized.push(Value::Object(payload));
    }
    normalized
}

/// Port of `entity_extractors._normalize_relations(relations)`.
pub fn normalize_relations(relations: &[Value]) -> Vec<Value> {
    let mut normalized = Vec::new();
    for item in relations {
        let source = as_str_field(item, "source");
        let target = as_str_field(item, "target");
        if source.is_empty() || target.is_empty() {
            continue;
        }
        let relation = as_str_field(item, "relation");
        normalized.push(serde_json::json!({
            "source": source,
            "target": target,
            "relation": if relation.is_empty() { "RELATED".to_string() } else { relation },
        }));
    }
    normalized
}

/// Port of `_select_primary_mention(mentions)`.
pub fn select_primary_mention(mentions: &Value) -> Value {
    let items = match mentions.as_array() {
        Some(arr) if !arr.is_empty() => arr.clone(),
        _ => return Value::Null,
    };
    let mut sorted = items;
    sorted.sort_by(|a, b| {
        let conf_a = a.get("confidence").and_then(Value::as_f64).unwrap_or(0.0);
        let conf_b = b.get("confidence").and_then(Value::as_f64).unwrap_or(0.0);
        conf_b
            .partial_cmp(&conf_a)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| {
                let start_a = a.get("start_char").and_then(Value::as_i64);
                let start_b = b.get("start_char").and_then(Value::as_i64);
                let key_a = start_a.unwrap_or(1_000_000_000);
                let key_b = start_b.unwrap_or(1_000_000_000);
                key_a.cmp(&key_b)
            })
    });
    sorted[0].clone()
}

/// Order-preserving map mirroring Python `dict` semantics for node merge keys.
struct NodeMap {
    entries: Vec<(String, Value)>,
    index: HashMap<String, usize>,
}

impl NodeMap {
    fn new() -> Self {
        Self {
            entries: Vec::new(),
            index: HashMap::new(),
        }
    }

    fn contains(&self, key: &str) -> bool {
        self.index.contains_key(key)
    }

    fn get_mut(&mut self, key: &str) -> Option<&mut Value> {
        self.index.get(key).map(|&i| &mut self.entries[i].1)
    }

    fn insert(&mut self, key: String, node: Value) {
        self.index.insert(key.clone(), self.entries.len());
        self.entries.push((key, node));
    }
}

fn mention_object(entity: &Value, name: &str) -> Value {
    let start = entity.get("start_char");
    let end = entity.get("end_char");
    if start.is_none() || start.is_some_and(Value::is_null) {
        return Value::Null;
    }
    if end.is_none() || end.is_some_and(Value::is_null) {
        return Value::Null;
    }
    let mut mention = Map::new();
    mention.insert(
        "start_char".to_string(),
        start.cloned().unwrap_or(Value::Null),
    );
    mention.insert("end_char".to_string(), end.cloned().unwrap_or(Value::Null));
    mention.insert("surface".to_string(), Value::String(name.to_string()));
    if let Some(conf) = entity.get("confidence").filter(|v| !v.is_null()) {
        mention.insert("confidence".to_string(), conf.clone());
    }
    Value::Object(mention)
}

fn make_node(
    name: &str,
    ent_type: &str,
    name_norm: &str,
    mention: &Value,
    project_id_normalized: Option<&str>,
) -> Value {
    let mut node = Map::new();
    node.insert(
        "id".to_string(),
        Value::String(entity_id(
            name,
            ent_type,
            Some(name_norm),
            project_id_normalized,
        )),
    );
    node.insert("name".to_string(), Value::String(name.to_string()));
    node.insert("type".to_string(), Value::String(ent_type.to_string()));
    node.insert(
        "name_norm".to_string(),
        Value::String(name_norm.to_string()),
    );
    node.insert(
        "mentions".to_string(),
        Value::Array(if mention.is_null() {
            Vec::new()
        } else {
            vec![mention.clone()]
        }),
    );
    node.insert(
        "confidence".to_string(),
        if mention.is_null() {
            Value::Null
        } else {
            mention.get("confidence").cloned().unwrap_or(Value::Null)
        },
    );
    Value::Object(node)
}

/// Port of `build_graph_components_from_entities`.
///
/// Returns the ordered `(merge_key, node_dict)` pairs (Python dict insertion
/// order matters for `coalesce(e.name, ...)` first-write semantics) plus the
/// cleaned relation list.
pub fn build_graph_components_from_entities(
    entities: &[Value],
    relations: &[Value],
    merge_entities: bool,
    normalize_mode: &str,
    project_id_normalized: Option<&str>,
) -> (Vec<(String, Value)>, Vec<Value>) {
    let mut nodes = NodeMap::new();
    let mut name_index: HashMap<String, String> = HashMap::new();

    for ent in entities {
        let name = as_str_field(ent, "name");
        if name.is_empty() {
            continue;
        }
        let mut ent_type = as_str_field(ent, "type");
        if ent_type.is_empty() {
            ent_type = "UNKNOWN".to_string();
        }
        let name_norm = normalize_entity_name(&name, normalize_mode);
        if name_norm.is_empty() {
            continue;
        }
        let mention = mention_object(ent, &name);
        if merge_entities {
            let key = match project_id_normalized {
                Some(project) => format!("{project}::{ent_type}::{name_norm}"),
                None => format!("{ent_type}::{name_norm}"),
            };
            if !nodes.contains(&key) {
                nodes.insert(
                    key.clone(),
                    make_node(
                        &name,
                        &ent_type,
                        &name_norm,
                        &mention,
                        project_id_normalized,
                    ),
                );
            } else {
                let node = nodes.get_mut(&key).expect("key exists");
                let existing_len = node["name"]
                    .as_str()
                    .map(str::chars)
                    .map(Iterator::count)
                    .unwrap_or(0);
                if name.chars().count() > existing_len {
                    node["name"] = Value::String(name.clone());
                }
                if !mention.is_null() {
                    if let Some(mentions) = node["mentions"].as_array_mut() {
                        mentions.push(mention.clone());
                    }
                    if let Some(conf) = mention.get("confidence").and_then(Value::as_f64) {
                        let existing = node.get("confidence").and_then(Value::as_f64);
                        if existing.is_none() || conf > existing.expect("checked") {
                            node["confidence"] = serde_json::json!(conf);
                        }
                    }
                }
            }
            name_index
                .entry(name_norm.clone())
                .or_insert_with(|| key.clone());
        } else {
            let key = uuid4_string();
            nodes.insert(
                key.clone(),
                make_node(
                    &name,
                    &ent_type,
                    &name_norm,
                    &mention,
                    project_id_normalized,
                ),
            );
            name_index.entry(name_norm.clone()).or_insert(key);
        }
    }

    let mut cleaned_relations: Vec<Value> = Vec::new();
    for rel in relations {
        let source = as_str_field(rel, "source");
        let target = as_str_field(rel, "target");
        if source.is_empty() || target.is_empty() {
            continue;
        }
        let mut rel_type = as_str_field(rel, "relation");
        if rel_type.is_empty() {
            rel_type = "RELATED".to_string();
        }
        let source_norm = normalize_entity_name(&source, normalize_mode);
        let target_norm = normalize_entity_name(&target, normalize_mode);
        if source_norm.is_empty() || target_norm.is_empty() {
            continue;
        }

        // Ensure the source node exists (creating UNKNOWN fallbacks like Python).
        if !name_index.contains_key(&source_norm) {
            let key = match (merge_entities, project_id_normalized) {
                (true, Some(project)) => format!("{project}::UNKNOWN::{source_norm}"),
                (true, None) => format!("UNKNOWN::{source_norm}"),
                (false, _) => uuid4_string(),
            };
            let id = if merge_entities {
                entity_id(
                    &source,
                    "UNKNOWN",
                    Some(&source_norm),
                    project_id_normalized,
                )
            } else {
                key.clone()
            };
            let mut node = Map::new();
            node.insert("id".to_string(), Value::String(id));
            node.insert("name".to_string(), Value::String(source.clone()));
            node.insert("type".to_string(), Value::String("UNKNOWN".to_string()));
            node.insert("name_norm".to_string(), Value::String(source_norm.clone()));
            node.insert("mentions".to_string(), Value::Array(Vec::new()));
            node.insert("confidence".to_string(), Value::Null);
            nodes.insert(key.clone(), Value::Object(node));
            name_index.insert(source_norm.clone(), key);
        }
        if !name_index.contains_key(&target_norm) {
            let key = match (merge_entities, project_id_normalized) {
                (true, Some(project)) => format!("{project}::UNKNOWN::{target_norm}"),
                (true, None) => format!("UNKNOWN::{target_norm}"),
                (false, _) => uuid4_string(),
            };
            let id = if merge_entities {
                entity_id(
                    &target,
                    "UNKNOWN",
                    Some(&target_norm),
                    project_id_normalized,
                )
            } else {
                key.clone()
            };
            let mut node = Map::new();
            node.insert("id".to_string(), Value::String(id));
            node.insert("name".to_string(), Value::String(target.clone()));
            node.insert("type".to_string(), Value::String("UNKNOWN".to_string()));
            node.insert("name_norm".to_string(), Value::String(target_norm.clone()));
            node.insert("mentions".to_string(), Value::Array(Vec::new()));
            node.insert("confidence".to_string(), Value::Null);
            nodes.insert(key.clone(), Value::Object(node));
            name_index.insert(target_norm.clone(), key);
        }

        let source_key = name_index.get(&source_norm).expect("source key").clone();
        let target_key = name_index.get(&target_norm).expect("target key").clone();
        let source_id = nodes.get_mut(&source_key).expect("source node")["id"]
            .as_str()
            .expect("id str")
            .to_string();
        let target_id = nodes.get_mut(&target_key).expect("target node")["id"]
            .as_str()
            .expect("id str")
            .to_string();
        cleaned_relations.push(serde_json::json!({
            "source_id": source_id,
            "target_id": target_id,
            "type": rel_type,
        }));
    }

    (nodes.entries, cleaned_relations)
}

/// Convenience: expose node keys in insertion order (parity diagnostics).
pub fn node_keys(nodes: &[(String, Value)]) -> Vec<String> {
    nodes.iter().map(|(k, _)| k.clone()).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::Path;

    fn golden() -> Value {
        let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/py_golden.json");
        serde_json::from_str(&std::fs::read_to_string(&path).expect("golden fixture"))
            .expect("golden json")
    }

    #[test]
    fn uuid5_matches_python() {
        assert_eq!(
            uuid5_namespace_url("p14doc::ORG::acme systems"),
            "38ce0028-3907-5071-9e25-2c6b0a9d299f"
        );
    }

    #[test]
    fn entity_id_matches_python() {
        let g = golden();
        let ids = &g["entity_id"];
        assert_eq!(entity_id("Acme Systems", "ORG", None, None), ids["simple"]);
        assert_eq!(
            entity_id("Plug&Charge!", "TECH", Some("plug charge"), Some("p14doc")),
            ids["namespaced"]
        );
        assert_eq!(
            entity_id(
                "some target",
                "UNKNOWN",
                Some("some target"),
                Some("p14doc")
            ),
            ids["unknown"]
        );
    }

    #[test]
    fn normalize_matches_python() {
        let g = golden();
        let cases = &g["normalize_entity_name"];
        assert_eq!(
            normalize_entity_name("\"Hello-World_\"", "aggressive"),
            cases["quotes"]
        );
        assert_eq!(
            normalize_entity_name("ISO/IEC 18013-5", "aggressive"),
            cases["iso"]
        );
        assert_eq!(normalize_entity_name("   ", "aggressive"), cases["blank"]);
        assert_eq!(
            normalize_entity_name("Plug&Charge", "basic"),
            cases["basic_mode"]
        );
        assert_eq!(
            normalize_entity_name("Plug&Charge", "aggressive"),
            cases["aggressive_amp"]
        );
        assert_eq!(
            normalize_entity_name("“Berlin”", "aggressive"),
            cases["unicode_quotes"]
        );
        assert_eq!(
            normalize_entity_name("__Acme--Corp/Systems__", "aggressive"),
            cases["mixed"]
        );
    }

    #[test]
    fn assembly_matches_python() {
        let g = golden();
        let case = &g["assembly"];
        let (nodes, relations) = build_graph_components_from_entities(
            case["input"]["entities"].as_array().expect("entities"),
            case["input"]["relations"].as_array().expect("relations"),
            true,
            "aggressive",
            Some("p14doc"),
        );
        let actual = serde_json::json!({
            "nodes": nodes.iter().map(|(_, n)| n).collect::<Vec<_>>(),
            "relations": relations,
            "node_keys_in_order": node_keys(&nodes),
        });
        let expected = serde_json::json!({
            "nodes": case["nodes"],
            "relations": case["relations"],
            "node_keys_in_order": case["node_keys_in_order"],
        });
        assert_eq!(actual, expected);
    }

    #[test]
    fn primary_mention_matches_python() {
        let g = golden();
        let cases = &g["select_primary_mention"];
        assert_eq!(
            select_primary_mention(&cases["by_confidence"]),
            cases["by_confidence"][1]
        );
        assert_eq!(
            select_primary_mention(&cases["tie_by_start"]),
            cases["tie_by_start"][1]
        );
        assert_eq!(
            select_primary_mention(&cases["no_confidence"]),
            cases["no_confidence"][1]
        );
        assert_eq!(select_primary_mention(&cases["empty"]), Value::Null);
    }

    #[test]
    fn find_span_char_indices() {
        assert_eq!(find_span("hello World hello", "World"), Some((6, 11)));
        assert_eq!(find_span("xin chào Thế Giới", "thế giới"), Some((9, 17)));
        assert_eq!(find_span("abc", "xyz"), None);
        assert_eq!(find_span("", "a"), None);
    }
}
