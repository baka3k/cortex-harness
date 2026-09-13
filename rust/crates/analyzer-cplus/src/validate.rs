//! Port `tools/common/payload_validation.py::validate_cplus_payload` —
//! preflight filter trước mọi graph effect: identity checks, dedupe
//! declaration-vs-definition, quan hệ endpoint/caller resolution.

use std::collections::{BTreeMap, HashSet};

use serde_json::{json, Value};

use crate::cparse::FilePayload;
use crate::identity::json_ensure_ascii;
use crate::position::{basename, normpath};

#[allow(dead_code)]
pub const PAYLOAD_SCHEMA_VERSION: &str = "1.1";

pub const PROC_NODE_LABELS: [&str; 5] = [
    "SqlStatement",
    "SqlDirective",
    "SqlCursor",
    "SqlHostVariable",
    "DatabaseTable",
];

const PROC_RELATION_TYPES: [&str; 9] = [
    "DECLARES_STATEMENT",
    "DECLARES_DIRECTIVE",
    "BINDS_PARAMETER",
    "DECLARES_CURSOR",
    "REFERENCES_CURSOR",
    "REFERENCES_STATEMENT",
    "READS_FROM",
    "WRITES_TO",
    "REFERENCES_TABLE",
];

fn proc_relation_endpoints(rel_type: &str) -> Option<[(&'static str, &'static str); 1]> {
    match rel_type {
        "DECLARES_STATEMENT" => Some([("Function", "SqlStatement")]),
        "DECLARES_DIRECTIVE" => Some([("Function", "SqlDirective")]),
        "BINDS_PARAMETER" => Some([("SqlStatement", "SqlHostVariable")]),
        "DECLARES_CURSOR" => Some([("Function", "SqlCursor")]),
        "REFERENCES_CURSOR" => Some([("SqlStatement", "SqlCursor")]),
        "REFERENCES_STATEMENT" => Some([("SqlStatement", "SqlStatement")]),
        "READS_FROM" | "WRITES_TO" | "REFERENCES_TABLE" => {
            Some([("SqlStatement", "DatabaseTable")])
        }
        _ => None,
    }
}

pub const COLLECTION_LABELS: [(&str, &str); 10] = [
    ("namespaces", "Namespace"),
    ("types", "Type"),
    ("function_types", "FunctionType"),
    ("functions", "Function"),
    ("fields", "Field"),
    ("aliases", "Alias"),
    ("templates", "Template"),
    ("resources", "Resource"),
    ("resource_elements", "ResourceElement"),
    ("proc_nodes", "SqlStatement"),
];

const INTEGER_FIELDS: [&str; 5] = [
    "start_byte",
    "end_byte",
    "start_line",
    "end_line",
    "arity",
];

fn required_fields(collection: &str) -> &'static [&'static str] {
    match collection {
        "namespaces" => &[
            "symbol_id", "name", "qualified_name", "file_path", "start_line",
            "end_line", "code", "comment", "summary", "note",
        ],
        "types" => &[
            "symbol_id", "name", "qualified_name", "kind", "file_path", "start_line",
            "end_line", "code", "comment", "summary", "note",
        ],
        "function_types" => &[
            "symbol_id", "type_signature", "file_path", "start_line", "end_line", "code",
        ],
        "functions" => &[
            "symbol_id", "name", "qualified_name", "kind", "scope_name",
            "file_path", "start_line", "end_line", "arity", "code", "comment",
            "summary", "note",
        ],
        "fields" => &[
            "symbol_id", "name", "qualified_name", "scope_name", "type_signature",
            "file_path", "start_line", "end_line", "code",
        ],
        "aliases" => &[
            "symbol_id", "name", "qualified_name", "kind", "target_name", "file_path",
            "start_line", "end_line", "code",
        ],
        "templates" => &["symbol_id", "name", "file_path", "start_line", "end_line", "code"],
        "resources" => &[
            "symbol_id", "name", "qualified_name", "kind", "resource_symbol", "file_path",
            "start_line", "end_line", "code", "comment", "summary", "note",
        ],
        "resource_elements" => &[
            "symbol_id", "name", "qualified_name", "kind", "file_path", "start_line",
            "end_line", "code", "comment", "summary", "note",
        ],
        "proc_nodes" => &[
            "symbol_id", "name", "qualified_name", "kind", "file_path", "start_line",
            "end_line", "code", "comment", "summary", "note",
        ],
        _ => &[],
    }
}

fn has_forbidden_control(value: &str) -> bool {
    value.chars().any(char::is_control)
}

/// `_identity_reason` — trả None khi hợp lệ.
fn identity_reason(value: Option<&str>, name: bool) -> Option<&'static str> {
    let Some(value) = value else {
        return Some("malformed_declarator_capture");
    };
    if value.trim().is_empty() || has_forbidden_control(value) {
        return Some("malformed_declarator_capture");
    }
    if value != value.trim() {
        return Some("malformed_declarator_capture");
    }
    if name {
        let stripped = value.trim();
        if stripped.starts_with('#')
            || stripped.contains("#define")
            || stripped.contains("#if")
        {
            return Some("preprocessor_leakage");
        }
        if ["/*", "*/", "//"].iter().any(|m| stripped.contains(m)) {
            return Some("comment_leakage");
        }
    }
    None
}

/// `normalize_relative_path` — Err khi path rỗng/absolute/escape root.
#[allow(clippy::result_unit_err)]
pub fn normalize_relative_path(value: Option<&str>) -> Result<String, ()> {
    let Some(value) = value else {
        return Err(());
    };
    if value.trim().is_empty() || has_forbidden_control(value) {
        return Err(());
    }
    let candidate = value.replace('\\', "/");
    if candidate.starts_with('/')
        || (candidate.len() > 1 && candidate.as_bytes()[1] == b':')
    {
        return Err(());
    }
    let normalized = normpath(&candidate);
    if normalized.is_empty() || normalized == "." || normalized == ".." || normalized.starts_with("../")
    {
        return Err(());
    }
    Ok(normalized)
}

fn span_is_valid(row: &serde_json::Map<String, Value>) -> bool {
    for (start_key, end_key) in [("start_byte", "end_byte"), ("start_line", "end_line")] {
        if !row.contains_key(start_key) && !row.contains_key(end_key) {
            continue;
        }
        let start = row.get(start_key).and_then(Value::as_i64).unwrap_or(0);
        let end = row.get(end_key).and_then(Value::as_i64).unwrap_or(0);
        if start < 0 || end < start {
            return false;
        }
    }
    true
}

fn record_identity(row: &serde_json::Map<String, Value>) -> String {
    row.get("id")
        .and_then(Value::as_str)
        .or_else(|| row.get("symbol_id").and_then(Value::as_str))
        .unwrap_or("")
        .to_string()
}

fn record_path(row: &serde_json::Map<String, Value>, default: &str) -> String {
    row.get("file_path")
        .and_then(Value::as_str)
        .or_else(|| row.get("source_path").and_then(Value::as_str))
        .unwrap_or(default)
        .to_string()
}

fn invalid_required_fields(collection: &str, row: &serde_json::Map<String, Value>) -> bool {
    for field_name in required_fields(collection) {
        let Some(value) = row.get(*field_name) else {
            return true;
        };
        if INTEGER_FIELDS.contains(field_name) {
            if value.is_boolean() || !value.is_i64() {
                return true;
            }
        } else if *field_name == "scope_name" {
            if !value.is_null() && !value.is_string() {
                return true;
            }
        } else if !value.is_string() {
            return true;
        }
    }
    false
}

/// `identity_merge_fingerprint` — canonical JSON của các field ngữ nghĩa.
pub fn identity_merge_fingerprint(label: &str, record: &serde_json::Map<String, Value>) -> String {
    let shared = ["name", "qualified_name", "kind"];
    let label_fields: &[&str] = match label {
        "Function" => &[
            "scope_name",
            "arity",
            "identity_schema",
            "signature",
            "parameter_types",
            "qualifiers",
            "template_arity",
            "linkage",
        ],
        "FunctionType" => &["type_signature"],
        "Field" => &["scope_name", "type_signature"],
        "Alias" => &["target_name"],
        "Resource" => &["resource_symbol"],
        "ResourceElement" => &["resource_symbol", "dialog_symbol", "control_type"],
        _ => &[],
    };
    let mut semantic: BTreeMap<String, Value> = BTreeMap::new();
    for field in shared.iter().chain(label_fields.iter()) {
        if let Some(value) = record.get(*field) {
            semantic.insert((*field).to_string(), value.clone());
        }
    }
    if label == "Function" && semantic.get("kind").and_then(Value::as_str) == Some("declaration") {
        semantic.insert("kind".into(), json!("function"));
    }
    let mut encoded = String::from("{");
    let mut first = true;
    for (key, value) in &semantic {
        if !first {
            encoded.push(',');
        }
        first = false;
        encoded.push_str(&json_ensure_ascii(key));
        encoded.push(':');
        encoded.push_str(&crate::quality::canonical_json(value));
    }
    encoded.push('}');
    encoded
}

/// Preference: function body thắng declaration tương đương.
fn duplicate_preference(label: &str, record: &serde_json::Map<String, Value>) -> i64 {
    if label == "Function" && record.get("kind").and_then(Value::as_str) == Some("function") {
        1
    } else {
        0
    }
}

/// Kết quả validation của một payload.
pub struct ValidatedPayload {
    pub payload: FilePayload,
    /// Số record bị quarantine (artifact accounting).
    #[allow(dead_code)]
    pub quarantined: usize,
    pub file_quarantined: bool,
    /// (label, identity) accepted của payload này (kể cả File/Project).
    #[allow(dead_code)]
    pub accepted_ids: HashSet<(String, String)>,
    /// (label, identity) invalid — để loại relation/call tham chiếu.
    #[allow(dead_code)]
    pub invalid_keys: HashSet<(String, String)>,
}

/// `validate_cplus_payload`.
pub fn validate_cplus_payload(
    payload: &FilePayload,
    project_id: &str,
    known_identities: Option<&HashSet<(String, String)>>,
    blocked_identities: Option<&HashSet<(String, String)>>,
) -> ValidatedPayload {
    let mut quarantined = 0usize;
    let file_def = payload.file_def.clone().unwrap_or_default();
    let quality_provenance_tier = payload
        .parse_meta
        .get("quality")
        .and_then(|q| q.get("tier"))
        .and_then(Value::as_str)
        .unwrap_or("");
    let _ = quality_provenance_tier;
    let structural_backend = payload
        .parse_meta
        .get("parser_backend")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let source_path_raw = file_def
        .get("file_path")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let source_path = match normalize_relative_path(Some(&source_path_raw)) {
        Ok(path) => path,
        Err(()) => {
            // INVALID_PATH — quarantine toàn payload.
            let out = FilePayload::default();
            quarantined += 1;
            let accepted = HashSet::new();
            return ValidatedPayload {
                payload: out,
                quarantined,
                file_quarantined: true,
                accepted_ids: accepted,
                invalid_keys: HashSet::new(),
            };
        }
    };

    if structural_backend == "libclang" {
        // LEGACY_STRUCTURE_BACKEND — không xảy ra trên backend tree-sitter.
        return ValidatedPayload {
            payload: FilePayload::default(),
            quarantined: quarantined + 1,
            file_quarantined: true,
            accepted_ids: HashSet::new(),
            invalid_keys: HashSet::new(),
        };
    }

    // file_quarantined: file_def.parse_quality.tier == quarantined HOẶC
    // evidence_policy.strong_relations_allowed == False.
    let file_quarantined = file_def
        .get("parse_quality")
        .and_then(|q| q.get("tier"))
        .and_then(Value::as_str)
        .map(|tier| tier == "quarantined")
        .unwrap_or(false);

    let mut accepted_ids: HashSet<(String, String)> = HashSet::new();
    accepted_ids.insert(("Project".to_string(), project_id.to_string()));
    accepted_ids.insert(("File".to_string(), source_path.clone()));
    let mut invalid_keys: HashSet<(String, String)> = HashSet::new();

    let mut out = FilePayload::default();

    for (collection, default_label) in COLLECTION_LABELS {
        let rows = payload.rows_of(collection);
        let mut rows: Vec<serde_json::Map<String, Value>> = rows.to_vec();
        if file_quarantined {
            for row in &rows {
                let label = row
                    .get("label")
                    .and_then(Value::as_str)
                    .unwrap_or(default_label)
                    .to_string();
                invalid_keys.insert((label.clone(), record_identity(row)));
                quarantined += 1;
            }
            rows.clear();
            out.set_rows_of(collection, Vec::new());
            continue;
        }

        let mut by_identity: BTreeMap<String, Vec<(usize, serde_json::Map<String, Value>)>> =
            BTreeMap::new();
        let mut prevalidated: Vec<(usize, serde_json::Map<String, Value>)> = Vec::new();
        let mut dropped: HashSet<usize> = HashSet::new();
        for (order, mut row) in rows.into_iter().enumerate() {
            let label = row
                .get("label")
                .and_then(Value::as_str)
                .unwrap_or(default_label)
                .to_string();
            let identity = record_identity(&row);
            let mut reason: Option<&'static str> = None;
            if collection == "proc_nodes" {
                let row_label = row.get("label").and_then(Value::as_str).unwrap_or("").trim();
                if row_label.is_empty() || !PROC_NODE_LABELS.contains(&row_label) {
                    reason = Some("invalid_record");
                }
            }
            if reason.is_none() {
                reason = identity_reason(Some(identity.as_str()), false);
            }
            if reason.is_none() && row.contains_key("name") {
                let name = row.get("name").and_then(Value::as_str);
                reason = identity_reason(name, true);
            }
            if reason.is_none() && invalid_required_fields(collection, &row) {
                reason = Some("invalid_record");
            }
            if reason.is_none() && !span_is_valid(&row) {
                reason = Some("invalid_span");
            }
            let owner = match normalize_relative_path(Some(&record_path(&row, &source_path))) {
                Ok(owner) => owner,
                Err(()) => {
                    if reason.is_none() {
                        reason = Some("missing_owner");
                    }
                    String::new()
                }
            };
            if !owner.is_empty() && owner != source_path && reason.is_none() {
                reason = Some("missing_owner");
            }
            if let Some(_reason) = reason {
                invalid_keys.insert((label, identity));
                dropped.insert(order);
                quarantined += 1;
                continue;
            }
            row.insert("file_path".into(), json!(owner));
            by_identity.entry(identity.clone()).or_default().push((order, row.clone()));
            prevalidated.push((order, row));
        }

        // conflicts: identity có >1 fingerprint khác nhau.
        let mut conflicts: HashSet<String> = HashSet::new();
        let mut preferred_index: BTreeMap<String, (usize, i64)> = BTreeMap::new();
        for (identity, candidates) in &by_identity {
            let mut fingerprints: HashSet<String> = HashSet::new();
            let mut best: Option<(usize, i64)> = None;
            for (order, row) in candidates {
                let label = row
                    .get("label")
                    .and_then(Value::as_str)
                    .unwrap_or(default_label);
                fingerprints.insert(identity_merge_fingerprint(label, row));
                let preference = duplicate_preference(label, row);
                // Python max() trả phần tử ĐẦU TIÊN đạt max.
                match best {
                    None => best = Some((*order, preference)),
                    Some((_, best_pref)) if preference > best_pref => {
                        best = Some((*order, preference));
                    }
                    _ => {}
                }
            }
            if fingerprints.len() > 1 {
                conflicts.insert(identity.clone());
            }
            if let Some(best) = best {
                preferred_index.insert(identity.clone(), best);
            }
        }

        let mut accepted_rows: Vec<serde_json::Map<String, Value>> = Vec::new();
        let mut seen: HashSet<String> = HashSet::new();
        for (order, row) in &prevalidated {
            let label = row
                .get("label")
                .and_then(Value::as_str)
                .unwrap_or(default_label)
                .to_string();
            let identity = record_identity(row);
            if blocked_identities.is_some_and(|blocked| blocked.contains(&(label.clone(), identity.clone())))
            {
                invalid_keys.insert((label, identity));
                quarantined += 1;
                continue;
            }
            if conflicts.contains(&identity) {
                invalid_keys.insert((label, identity));
                quarantined += 1;
                continue;
            }
            if preferred_index.get(&identity).map(|(idx, _)| idx) != Some(order) {
                // duplicate đã lọc (rejected, không quarantine).
                continue;
            }
            if !seen.insert(identity.clone()) {
                continue;
            }
            accepted_rows.push(row.clone());
            accepted_ids.insert((label, identity));
        }
        out.set_rows_of(collection, accepted_rows);
    }

    // ── Relations ───────────────────────────────────────────────────────────
    let mut filtered_relations: Vec<serde_json::Map<String, Value>> = Vec::new();
    for relation in &payload.relations {
        let has_all = ["source_label", "target_label", "source_id", "target_id", "rel_type"]
            .iter()
            .all(|f| relation.contains_key(*f));
        if !has_all {
            quarantined += 1;
            continue;
        }
        let source_label = relation.get("source_label").and_then(Value::as_str).unwrap_or("");
        let target_label = relation.get("target_label").and_then(Value::as_str).unwrap_or("");
        let source_id = relation.get("source_id").and_then(Value::as_str).unwrap_or("");
        let target_id = relation.get("target_id").and_then(Value::as_str).unwrap_or("");
        let rel_type = relation.get("rel_type").and_then(Value::as_str).unwrap_or("");
        if PROC_RELATION_TYPES.contains(&rel_type) {
            let endpoints = proc_relation_endpoints(rel_type).unwrap_or_default();
            if !endpoints
                .iter()
                .any(|(s, t)| *s == source_label && *t == target_label)
            {
                quarantined += 1;
                continue;
            }
        }
        let source_key = (source_label.to_string(), source_id.to_string());
        let target_key = (target_label.to_string(), target_id.to_string());
        let unresolved = invalid_keys.contains(&source_key)
            || invalid_keys.contains(&target_key)
            || known_identities.is_some_and(|registry| {
                !registry.contains(&source_key) || !registry.contains(&target_key)
            });
        if file_quarantined || unresolved {
            quarantined += 1;
            continue;
        }
        filtered_relations.push(relation.clone());
    }
    out.relations = filtered_relations;

    // ── Calls ───────────────────────────────────────────────────────────────
    let mut filtered_calls: Vec<serde_json::Map<String, Value>> = Vec::new();
    for call in &payload.calls {
        let caller_id = call.get("caller_id").and_then(Value::as_str).unwrap_or("");
        let caller_key = ("Function".to_string(), caller_id.to_string());
        if file_quarantined
            || invalid_keys.contains(&caller_key)
            || !accepted_ids.contains(&caller_key)
        {
            quarantined += 1;
            continue;
        }
        filtered_calls.push(call.clone());
    }
    out.calls = filtered_calls;

    out.file_def = payload.file_def.clone();
    out.using_namespaces = payload.using_namespaces.clone();
    out.using_imports = payload.using_imports.clone();
    out.includes = payload.includes.clone();
    out.macros = payload.macros.clone();
    out.parse_meta = payload.parse_meta.clone();
    out.proc_nodes = out.proc_nodes.clone();

    let _ = basename; // unused import guard
    ValidatedPayload {
        payload: out,
        quarantined,
        file_quarantined,
        accepted_ids,
        invalid_keys,
    }
}

impl FilePayload {
    /// Public accessor cho preflight identity scan.
    pub fn rows_of_pub(&self, collection: &str) -> &Vec<serde_json::Map<String, Value>> {
        self.rows_of(collection)
    }

    fn rows_of(&self, collection: &str) -> &Vec<serde_json::Map<String, Value>> {
        match collection {
            "namespaces" => &self.namespaces,
            "types" => &self.types,
            "function_types" => &self.function_types,
            "functions" => &self.functions,
            "fields" => &self.fields,
            "aliases" => &self.aliases,
            "templates" => &self.templates,
            "resources" => &self.resources,
            "resource_elements" => &self.resource_elements,
            _ => &self.proc_nodes,
        }
    }

    fn set_rows_of(&mut self, collection: &str, rows: Vec<serde_json::Map<String, Value>>) {
        match collection {
            "namespaces" => self.namespaces = rows,
            "types" => self.types = rows,
            "function_types" => self.function_types = rows,
            "functions" => self.functions = rows,
            "fields" => self.fields = rows,
            "aliases" => self.aliases = rows,
            "templates" => self.templates = rows,
            "resources" => self.resources = rows,
            "resource_elements" => self.resource_elements = rows,
            _ => self.proc_nodes = rows,
        }
    }
}
