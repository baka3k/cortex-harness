//! Graph emission — mirror `csharp_analyzer.py::build_call_graph` write path:
//! Project/Namespace/File/Type/Function/Property/Field/Event/Constant(delegate)
//! node rows + CONTAINS relations (File→*) + CALLS resolution, ghi qua
//! `cortex_graph_writer::language_writer::LanguageCodeWriter::write_all`
//! (`use_full_writers=true`, `files_variant="default"` — khớp lệnh
//! `write_all` của entry Python).

use std::collections::BTreeMap;
use std::path::Path;

use serde_json::{json, Value};
use cortex_graph_writer::json_row::Row;
use cortex_graph_writer::language_writer::{FilesVariant, LanguageCodeWriter, WriteAllPayload};

use crate::payload::project_meta;

/// Function index của build_call_graph: by name và by (name, arity).
pub struct FunctionIndex {
    by_name: BTreeMap<String, Vec<IndexEntry>>,
    by_name_arity: BTreeMap<(String, i64), Vec<IndexEntry>>,
}

struct IndexEntry {
    symbol_id: String,
    scope_name: String,
    arity: i64,
}

impl FunctionIndex {
    /// Index từ toàn bộ payloads (khớp `index_payloads` của Python).
    pub fn build(payloads: &[Value]) -> Self {
        let mut index = Self {
            by_name: BTreeMap::new(),
            by_name_arity: BTreeMap::new(),
        };
        for payload in payloads {
            for function in arr(payload, "functions") {
                let entry = IndexEntry {
                    symbol_id: function["symbol_id"].as_str().unwrap_or("").to_string(),
                    scope_name: function["scope_name"].as_str().unwrap_or("").to_string(),
                    arity: function["arity"].as_i64().unwrap_or(-1),
                };
                let name = function["name"].as_str().unwrap_or("").to_string();
                index
                    .by_name
                    .entry(name.clone())
                    .or_default()
                    .push(IndexEntry {
                        symbol_id: entry.symbol_id.clone(),
                        scope_name: entry.scope_name.clone(),
                        arity: entry.arity,
                    });
                if function["arity"].is_i64() {
                    index
                        .by_name_arity
                        .entry((name, entry.arity))
                        .or_default()
                        .push(entry);
                }
            }
        }
        index
    }

    /// `resolve_callee_id` — by (name, arity) → by name → single → scope match.
    fn resolve(&self, callee_name: &str, callee_arity: Option<i64>, caller_scope: &str) -> Option<&str> {
        let candidates: &[IndexEntry] = match callee_arity {
            Some(arity) if self.by_name_arity.contains_key(&(callee_name.to_string(), arity)) => {
                &self.by_name_arity[&(callee_name.to_string(), arity)]
            }
            _ => self.by_name.get(callee_name)?,
        };
        match candidates {
            [] => None,
            [single] => Some(single.symbol_id.as_str()),
            many => {
                if !caller_scope.is_empty() {
                    let scoped: Vec<&IndexEntry> = many
                        .iter()
                        .filter(|entry| entry.scope_name == caller_scope)
                        .collect();
                    if let [single] = scoped[..] {
                        return Some(single.symbol_id.as_str());
                    }
                }
                None
            }
        }
    }
}

#[allow(clippy::too_many_arguments)]
fn with_meta(
    mut row: Row,
    project_id: &str,
    project_name: &str,
    language: &str,
    repo: &str,
    build_system: &str,
) -> Row {
    for (key, value) in project_meta(project_id, project_name, language, repo, build_system) {
        row.insert(key.to_string(), value);
    }
    row
}

fn contains_relation(
    source_id: &str,
    source_label: &str,
    target_id: &str,
    target_label: &str,
    properties: Value,
) -> Row {
    let mut row = Row::new();
    row.insert("source_id".into(), json!(source_id));
    row.insert("source_label".into(), json!(source_label));
    row.insert("target_id".into(), json!(target_id));
    row.insert("target_label".into(), json!(target_label));
    row.insert("rel_type".into(), json!("CONTAINS"));
    row.insert("properties".into(), properties);
    row
}

/// Slice rỗng an toàn cho value thiếu key — tránh temporary borrow.
fn arr<'a>(container: &'a Value, key: &str) -> &'a [Value] {
    container
        .get(key)
        .and_then(Value::as_array)
        .map(Vec::as_slice)
        .unwrap_or(&[])
}

/// Toàn bộ row sets của write_all.
pub struct GraphRows {
    pub projects: Vec<Row>,
    pub namespaces: Vec<Row>,
    pub files: Vec<Row>,
    pub types: Vec<Row>,
    pub functions: Vec<Row>,
    pub properties: Vec<Row>,
    pub fields: Vec<Row>,
    pub events: Vec<Row>,
    pub constants: Vec<Row>,
    pub relations: Vec<Row>,
    pub calls: Vec<Row>,
}

/// Build graph rows từ payloads — khớp vòng lặp `for payload in
/// selected_payloads` của build_call_graph (node shapes + CONTAINS + calls).
#[allow(clippy::too_many_arguments)]
pub fn build_graph_rows(
    payloads: &[Value],
    project_id: &str,
    project_name: &str,
    language: &str,
    repo: &str,
    build_system: &str,
    root: &str,
) -> GraphRows {
    let index = FunctionIndex::build(payloads);
    let mut rows = GraphRows {
        projects: Vec::new(),
        namespaces: Vec::new(),
        files: Vec::new(),
        types: Vec::new(),
        functions: Vec::new(),
        properties: Vec::new(),
        fields: Vec::new(),
        events: Vec::new(),
        constants: Vec::new(),
        relations: Vec::new(),
        calls: Vec::new(),
    };

    // Project row — khớp `all_projects` (id/name/language/repo/root/build_system).
    let mut project = Row::new();
    project.insert("id".into(), json!(project_id));
    project.insert("name".into(), json!(project_name));
    project.insert("language".into(), json!(language));
    project.insert("repo".into(), json!(repo));
    project.insert("root".into(), json!(root));
    project.insert("build_system".into(), json!(build_system));
    rows.projects.push(project);

    for payload in payloads {
        let file_def = &payload["file_def"];
        let file_id = file_def["file_path"].as_str().unwrap_or("").to_string();

        let mut file = Row::new();
        file.insert("id".into(), json!(file_id));
        file.insert("path".into(), json!(file_id));
        file.insert(
            "start_line".into(),
            json!(file_def["start_line"].as_i64().unwrap_or(0)),
        );
        file.insert(
            "end_line".into(),
            json!(file_def["end_line"].as_i64().unwrap_or(0)),
        );
        for key in ["code", "comment", "summary", "note"] {
            file.insert(key.into(), file_def[key].clone());
        }
        let file = with_meta(file, project_id, project_name, language, repo, build_system);
        rows.files.push(file);
        rows.relations.push(contains_relation(
            project_id,
            "Project",
            &file_id,
            "File",
            json!({}),
        ));

        for namespace in arr(payload, "namespaces") {
            let mut row = Row::new();
            row.insert("id".into(), namespace["symbol_id"].clone());
            for key in [
                "name",
                "qualified_name",
                "file_path",
                "start_line",
                "end_line",
                "code",
                "comment",
                "summary",
                "note",
            ] {
                row.insert(key.into(), namespace[key].clone());
            }
            let row = with_meta(row, project_id, project_name, language, repo, build_system);
            rows.namespaces.push(row);
            rows.relations.push(contains_relation(
                &file_id,
                "File",
                namespace["symbol_id"].as_str().unwrap_or(""),
                "Namespace",
                json!({}),
            ));
        }

        for type_def in arr(payload, "types") {
            let mut row = Row::new();
            row.insert("id".into(), type_def["symbol_id"].clone());
            for key in [
                "name",
                "qualified_name",
                "kind",
                "file_path",
                "start_line",
                "end_line",
                "code",
                "comment",
                "summary",
                "note",
            ] {
                row.insert(key.into(), type_def[key].clone());
            }
            row.insert("exported".into(), json!(false));
            let row = with_meta(row, project_id, project_name, language, repo, build_system);
            rows.types.push(row);
            rows.relations.push(contains_relation(
                &file_id,
                "File",
                type_def["symbol_id"].as_str().unwrap_or(""),
                "Type",
                json!({}),
            ));
        }

        for function in arr(payload, "functions") {
            let mut row = Row::new();
            row.insert("id".into(), function["symbol_id"].clone());
            for key in [
                "name",
                "qualified_name",
                "kind",
                "scope_name",
                "file_path",
                "start_line",
                "end_line",
                "arity",
                "code",
                "comment",
                "summary",
                "note",
            ] {
                row.insert(key.into(), function[key].clone());
            }
            row.insert("class_name".into(), Value::Null);
            row.insert("package_name".into(), Value::Null);
            row.insert("exported".into(), json!(false));
            let row = with_meta(row, project_id, project_name, language, repo, build_system);
            rows.functions.push(row);
            rows.relations.push(contains_relation(
                &file_id,
                "File",
                function["symbol_id"].as_str().unwrap_or(""),
                "Function",
                json!({}),
            ));
        }

        for property in arr(payload, "properties") {
            let prop_id = match property.get("symbol_id").and_then(Value::as_str) {
                Some(symbol_id) if !symbol_id.is_empty() => symbol_id.to_string(),
                _ => format!(
                    "{}@{file_id}",
                    property
                        .get("qualified_name")
                        .and_then(Value::as_str)
                        .or_else(|| property.get("name").and_then(Value::as_str))
                        .unwrap_or("")
                ),
            };
            let mut row = Row::new();
            row.insert("id".into(), json!(prop_id));
            row.insert("name".into(), property["name"].clone());
            row.insert("qualified_name".into(), property["qualified_name"].clone());
            row.insert("type_name".into(), property["type_name"].clone());
            row.insert("accessibility".into(), property["accessibility"].clone());
            row.insert("is_static".into(), json!(property["is_static"].as_bool().unwrap_or(false)));
            row.insert("is_virtual".into(), json!(property["is_virtual"].as_bool().unwrap_or(false)));
            row.insert("is_override".into(), json!(property["is_override"].as_bool().unwrap_or(false)));
            row.insert("is_abstract".into(), json!(property["is_abstract"].as_bool().unwrap_or(false)));
            row.insert("kind".into(), json!(property["kind"].as_str().unwrap_or("property")));
            row.insert("file_path".into(), json!(file_id));
            row.insert("start_line".into(), json!(property["start_line"].as_i64().unwrap_or(0)));
            row.insert("end_line".into(), json!(property["end_line"].as_i64().unwrap_or(0)));
            let row = with_meta(row, project_id, project_name, language, repo, build_system);
            rows.properties.push(row);
            let mut props = Row::new();
            props.insert(
                "accessibility".into(),
                json!(property["accessibility"].as_str().unwrap_or("")),
            );
            rows.relations.push(contains_relation(
                &file_id,
                "File",
                &prop_id,
                "Property",
                Value::Object(props),
            ));
        }

        for field in arr(payload, "fields") {
            let field_id = match field.get("symbol_id").and_then(Value::as_str) {
                Some(symbol_id) if !symbol_id.is_empty() => symbol_id.to_string(),
                _ => format!(
                    "{}@{file_id}",
                    field
                        .get("qualified_name")
                        .and_then(Value::as_str)
                        .or_else(|| field.get("name").and_then(Value::as_str))
                        .unwrap_or("")
                ),
            };
            let mut row = Row::new();
            row.insert("id".into(), json!(field_id));
            row.insert("name".into(), field["name"].clone());
            row.insert("qualified_name".into(), field["qualified_name"].clone());
            row.insert("type_name".into(), field["type_name"].clone());
            row.insert("accessibility".into(), field["accessibility"].clone());
            row.insert("is_static".into(), json!(field["is_static"].as_bool().unwrap_or(false)));
            row.insert("is_const".into(), json!(field["is_const"].as_bool().unwrap_or(false)));
            row.insert("is_readonly".into(), json!(field["is_readonly"].as_bool().unwrap_or(false)));
            row.insert("constant_value".into(), field["constant_value"].clone());
            row.insert("kind".into(), json!("field"));
            row.insert("file_path".into(), json!(file_id));
            row.insert("start_line".into(), json!(field["start_line"].as_i64().unwrap_or(0)));
            row.insert("end_line".into(), json!(field["end_line"].as_i64().unwrap_or(0)));
            let row = with_meta(row, project_id, project_name, language, repo, build_system);
            rows.fields.push(row);
            rows.relations.push(contains_relation(
                &file_id,
                "File",
                &field_id,
                "Field",
                json!({}),
            ));
        }

        for event in arr(payload, "events") {
            let event_id = match event.get("symbol_id").and_then(Value::as_str) {
                Some(symbol_id) if !symbol_id.is_empty() => symbol_id.to_string(),
                _ => format!(
                    "{}@{file_id}",
                    event
                        .get("qualified_name")
                        .and_then(Value::as_str)
                        .or_else(|| event.get("name").and_then(Value::as_str))
                        .unwrap_or("")
                ),
            };
            let mut row = Row::new();
            row.insert("id".into(), json!(event_id));
            row.insert("name".into(), event["name"].clone());
            row.insert("qualified_name".into(), event["qualified_name"].clone());
            row.insert("delegate_type".into(), event["delegate_type"].clone());
            row.insert("accessibility".into(), event["accessibility"].clone());
            row.insert("is_static".into(), json!(event["is_static"].as_bool().unwrap_or(false)));
            row.insert("kind".into(), json!("event"));
            row.insert("file_path".into(), json!(file_id));
            row.insert("start_line".into(), json!(event["start_line"].as_i64().unwrap_or(0)));
            row.insert("end_line".into(), json!(event["end_line"].as_i64().unwrap_or(0)));
            let row = with_meta(row, project_id, project_name, language, repo, build_system);
            rows.events.push(row);
            rows.relations.push(contains_relation(
                &file_id,
                "File",
                &event_id,
                "Event",
                json!({}),
            ));
        }

        for delegate in arr(payload, "delegates") {
            let delegate_id = match delegate.get("symbol_id").and_then(Value::as_str) {
                Some(symbol_id) if !symbol_id.is_empty() => symbol_id.to_string(),
                _ => format!(
                    "{}@{file_id}",
                    delegate
                        .get("qualified_name")
                        .and_then(Value::as_str)
                        .or_else(|| delegate.get("name").and_then(Value::as_str))
                        .unwrap_or("")
                ),
            };
            let mut row = Row::new();
            row.insert("id".into(), json!(delegate_id));
            row.insert("name".into(), delegate["name"].clone());
            row.insert("qualified_name".into(), delegate["qualified_name"].clone());
            row.insert("return_type".into(), delegate["return_type"].clone());
            row.insert("type_parameters".into(), delegate["type_parameters"].clone());
            row.insert("parameters".into(), delegate["parameters"].clone());
            row.insert("kind".into(), json!("delegate"));
            row.insert("file_path".into(), json!(file_id));
            row.insert("start_line".into(), json!(delegate["start_line"].as_i64().unwrap_or(0)));
            row.insert("end_line".into(), json!(delegate["end_line"].as_i64().unwrap_or(0)));
            let row = with_meta(row, project_id, project_name, language, repo, build_system);
            // Python ghi delegates qua lane `constants` (write_constants_full).
            rows.constants.push(row);
            rows.relations.push(contains_relation(
                &file_id,
                "File",
                &delegate_id,
                "Delegate",
                json!({}),
            ));
        }

        // Payload relations — chỉ CONTAINS được phép (allowed_rel_types).
        for relation in arr(payload, "relations") {
            if relation["rel_type"].as_str() != Some("CONTAINS") {
                continue;
            }
            let mut row = Row::new();
            row.insert("source_id".into(), relation["source_id"].clone());
            row.insert("source_label".into(), relation["source_label"].clone());
            row.insert("target_id".into(), relation["target_id"].clone());
            row.insert("target_label".into(), relation["target_label"].clone());
            row.insert("rel_type".into(), relation["rel_type"].clone());
            row.insert("properties".into(), relation["properties"].clone());
            rows.relations.push(row);
        }

        // Calls — resolve qua function index khi callee_id thiếu.
        for call in arr(payload, "calls") {
            let resolved = match call.get("callee_id").and_then(Value::as_str) {
                Some(callee_id) if !callee_id.is_empty() => Some(callee_id.to_string()),
                _ => {
                    let callee_name = call["callee_name"].as_str().unwrap_or("");
                    let callee_arity = call["callee_arity"].as_i64();
                    let caller_scope = call["caller_scope"].as_str().unwrap_or("");
                    index
                        .resolve(callee_name, callee_arity, caller_scope)
                        .map(str::to_string)
                }
            };
            if let Some(callee_id) = resolved {
                let mut row = Row::new();
                row.insert("caller_id".into(), json!(call["caller_id"].as_str().unwrap_or("")));
                row.insert("callee_id".into(), json!(callee_id));
                // Explicit scope: khớp journal metadata project_id phía Python.
                row.insert("project_id".into(), json!(project_id));
                rows.calls.push(row);
            }
        }
    }
    rows
}

/// Ghi graph qua LanguageCodeWriter::write_all — `use_full_writers=true`,
/// `files_variant="default"` (khớp entry Python). Trả counts dict.
pub fn write_graph(
    writer: &mut LanguageCodeWriter,
    rows: &GraphRows,
) -> Result<BTreeMap<String, i64>, String> {
    let payload = WriteAllPayload {
        projects: &rows.projects,
        packages: &[],
        namespaces: &rows.namespaces,
        files: &rows.files,
        classes: &[],
        types: &rows.types,
        function_types: &[],
        functions: &rows.functions,
        fields: &rows.fields,
        aliases: &[],
        templates: &[],
        relations: &rows.relations,
        calls: &rows.calls,
        calls_with_site: &[],
        properties: &rows.properties,
        events: &rows.events,
        interfaces: &[],
        enums: &[],
        constants: &rows.constants,
        variables: &[],
        navigators: &[],
        has_routes: &[],
        param_lists: &[],
        workflows: &[],
        workflow_steps: &[],
        call_evidence_sites: &[],
        call_evidence_observations: &[],
        build_configurations: &[],
        semantic_coverage: &[],
        proc_function_joins: &[],
        proc_host_declarations: &[],
        use_full_writers: true,
        files_variant: FilesVariant::Default,
    };
    let counts = writer.write_all(&payload).map_err(|error| error.to_string())?;
    Ok(counts)
}

/// Kiểm tra graph-write có bị tắt qua env không (khớp `graph_writes_disabled`).
pub fn graph_writes_disabled() -> bool {
    matches!(
        std::env::var("CORTEX_DISABLE_GRAPH")
            .unwrap_or_default()
            .trim()
            .to_lowercase()
            .as_str(),
        "1" | "true" | "yes" | "on"
    )
}

/// build_graph_rows cần root string như Python `all_projects[0]["root"]`.
#[allow(dead_code)] // phase-03: chưa wire — giữ cho wiring composition/message lane sau
fn root_string(root: &Path) -> String {
    root.to_string_lossy().to_string()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn sample_payload() -> Value {
        json!({
            "namespaces": [{
                "symbol_id": "namespace::App",
                "qualified_name": "App",
                "name": "App",
                "file_path": "Program.cs",
                "start_line": 1, "end_line": 1,
                "code": "", "comment": "", "summary": "", "note": "",
            }],
            "types": [{
                "symbol_id": "App.Program",
                "qualified_name": "App.Program",
                "name": "Program",
                "kind": "class",
                "file_path": "Program.cs",
                "start_line": 1, "end_line": 5,
                "code": "", "comment": "", "summary": "", "note": "",
            }],
            "functions": [
                {
                    "symbol_id": "App.Program::Main/1@Program.cs",
                    "qualified_name": "App.Program::Main",
                    "name": "Main",
                    "kind": "method",
                    "scope_name": "App.Program",
                    "file_path": "Program.cs",
                    "start_line": 2, "end_line": 4,
                    "arity": 1,
                    "code": "", "comment": "", "summary": "", "note": "",
                },
                {
                    "symbol_id": "App.Program::Helper/0@Program.cs",
                    "qualified_name": "App.Program::Helper",
                    "name": "Helper",
                    "kind": "method",
                    "scope_name": "App.Program",
                    "file_path": "Program.cs",
                    "start_line": 5, "end_line": 6,
                    "arity": 0,
                    "code": "", "comment": "", "summary": "", "note": "",
                }
            ],
            "calls": [
                {"caller_id": "App.Program::Main/1@Program.cs", "callee_name": "Helper", "callee_id": null, "callee_arity": 0, "caller_scope": "App.Program"},
                {"caller_id": "App.Program::Main/1@Program.cs", "callee_name": "Missing", "callee_id": null, "callee_arity": null, "caller_scope": "App.Program"},
            ],
            "fields": [],
            "events": [],
            "delegates": [],
            "parameters": [],
            "properties": [],
            "relations": [],
            "file_def": {
                "file_path": "Program.cs",
                "start_line": 1, "end_line": 10,
                "code": "x", "comment": "", "summary": "", "note": "",
            },
        })
    }

    #[test]
    fn graph_rows_mirror_python_shapes() {
        let payloads = vec![sample_payload()];
        let rows = build_graph_rows(
            &payloads,
            "proj",
            "Proj",
            "csharp",
            "/repo",
            "",
            "/corpus",
        );
        assert_eq!(rows.projects.len(), 1);
        assert_eq!(rows.projects[0]["id"], "proj");
        assert_eq!(rows.projects[0]["root"], "/corpus");
        assert_eq!(rows.files.len(), 1);
        assert_eq!(rows.files[0]["path"], "Program.cs");
        assert_eq!(rows.types.len(), 1);
        assert_eq!(rows.functions.len(), 2);
        assert_eq!(rows.namespaces.len(), 1);
        // CONTAINS: Project→File, File→Namespace, File→Type, 2×File→Function.
        let contains = rows
            .relations
            .iter()
            .filter(|row| row["rel_type"] == "CONTAINS")
            .count();
        assert_eq!(contains, 5);
        // Project→File CONTAINS sẽ bị write_all lọc (source_id ∈ project_ids).
        let project_contains = rows
            .relations
            .iter()
            .filter(|row| row["source_id"] == "proj" && row["rel_type"] == "CONTAINS")
            .count();
        assert_eq!(project_contains, 1);
        // Calls: Helper resolve qua (name, arity); Missing không resolve.
        assert_eq!(rows.calls.len(), 1);
        assert_eq!(rows.calls[0]["callee_id"], "App.Program::Helper/0@Program.cs");
        assert_eq!(rows.calls[0]["project_id"], "proj");
    }
}
