//! Call resolution + row assembly — port `php_analyzer.py::build_call_graph`
//! phần graph write: function index (by_name / by_name_arity),
//! `resolve_callee_id`, JSON rows cho `LanguageCodeWriter.write_all`.

use std::collections::BTreeMap;

use serde_json::{json, Map, Value};

use crate::phpparse::{CallEdge, FilePayload, FunctionDef, NamespaceDef, Row, TypeDef};

#[derive(Clone)]
struct FuncEntry {
    symbol_id: String,
    scope_name: Option<String>,
}

/// Index dùng cho call resolution — thứ tự push giữ insertion order của
/// Python dict (mảng Vec theo key).
#[derive(Default)]
pub struct FunctionIndex {
    by_name: BTreeMap<String, Vec<FuncEntry>>,
    by_name_arity: BTreeMap<(String, i64), Vec<FuncEntry>>,
}

impl FunctionIndex {
    pub fn build(payloads: &[FilePayload]) -> Self {
        let mut index = FunctionIndex::default();
        for payload in payloads {
            for func in &payload.functions {
                let entry = FuncEntry {
                    symbol_id: func.symbol_id.clone(),
                    scope_name: func.scope_name.clone(),
                };
                index
                    .by_name
                    .entry(func.name.clone())
                    .or_default()
                    .push(entry.clone());
                index
                    .by_name_arity
                    .entry((func.name.clone(), func.arity))
                    .or_default()
                    .push(entry);
            }
        }
        index
    }

    /// `resolve_callee_id` — candidates theo (name, arity) trước, rỗng thì
    /// theo name; 1 candidate → chọn; nhiều thì lọc theo caller_scope.
    fn resolve(&self, call: &CallEdge) -> Option<String> {
        let name = &call.callee_name;
        // Python: `call.get("callee_arity") is not None` luôn đúng với PHP
        // (`_count_arguments` trả int) nên luôn tra by_name_arity trước.
        let candidates = match self.by_name_arity.get(&(name.clone(), call.callee_arity)) {
            Some(candidates) if !candidates.is_empty() => candidates,
            _ => self.by_name.get(name)?,
        };
        if candidates.len() == 1 {
            return Some(candidates[0].symbol_id.clone());
        }
        if let Some(caller_scope) = &call.caller_scope {
            let scoped: Vec<&FuncEntry> = candidates
                .iter()
                .filter(|c| c.scope_name.as_deref() == Some(caller_scope.as_str()))
                .collect();
            if scoped.len() == 1 {
                return Some(scoped[0].symbol_id.clone());
            }
        }
        None
    }
}

pub struct AssembledGraph {
    pub projects: Vec<Row>,
    pub namespaces: Vec<Row>,
    pub files: Vec<Row>,
    pub types: Vec<Row>,
    pub functions: Vec<Row>,
    pub relations: Vec<Row>,
    pub calls: Vec<Row>,
}

/// Dựng toàn bộ node/rel rows như `build_call_graph` (graph write pass).
#[allow(clippy::too_many_arguments)]
pub fn assemble_graph(
    payloads: &[FilePayload],
    index: &FunctionIndex,
    project_id: &str,
    project_name: &str,
    language: &str,
    repo: &str,
    build_system: &str,
    root: &str,
) -> AssembledGraph {
    let mut projects = Vec::new();
    let mut namespaces: Vec<Row> = Vec::new();
    let mut files = Vec::new();
    let mut types = Vec::new();
    let mut functions = Vec::new();
    let mut relations = Vec::new();
    let mut calls = Vec::new();

    projects.push({
        let mut row = Map::new();
        row.insert("id".into(), json!(project_id));
        row.insert("name".into(), json!(project_name));
        row.insert("language".into(), json!(language));
        row.insert("repo".into(), json!(repo));
        row.insert("root".into(), json!(root));
        row.insert("build_system".into(), json!(build_system));
        row
    });

    for payload in payloads {
        let file_def = payload.file_def.as_ref().expect("file_def");
        let file_id = file_def.file_path.clone();
        files.push({
            let mut row = Map::new();
            row.insert("id".into(), json!(file_id));
            row.insert("path".into(), json!(file_id));
            row.insert("start_line".into(), json!(file_def.start_line));
            row.insert("end_line".into(), json!(file_def.end_line));
            row.insert("code".into(), json!(file_def.code));
            row.insert("comment".into(), json!(file_def.comment));
            row.insert("summary".into(), json!(file_def.summary));
            row.insert("note".into(), json!(file_def.note));
            row.insert("imports".into(), json!(file_def.imports));
            row.insert("exports".into(), json!(file_def.exports));
            row.insert("jsx_tags".into(), json!(file_def.jsx_tags));
            row.insert("jsx_components".into(), json!(file_def.jsx_components));
            row.insert("project_id".into(), json!(project_id));
            row.insert("project_name".into(), json!(project_name));
            row.insert("language".into(), json!(language));
            row.insert("repo".into(), json!(repo));
            row.insert("build_system".into(), json!(build_system));
            row
        });
        relations.push({
            let mut row = Map::new();
            row.insert("source_id".into(), json!(project_id));
            row.insert("target_id".into(), json!(file_id));
            row.insert("rel_type".into(), json!("CONTAINS"));
            row.insert("properties".into(), json!({}));
            row
        });
        for ns in &payload.namespaces {
            namespaces.push(namespace_node_row(
                ns,
                project_id,
                project_name,
                language,
                repo,
                build_system,
            ));
            relations.push(contains_row(&file_id, &ns.symbol_id));
        }
        for type_def in &payload.types {
            types.push(type_node_row(
                type_def,
                project_id,
                project_name,
                language,
                repo,
                build_system,
            ));
            relations.push(contains_row(&file_id, &type_def.symbol_id));
        }
        for func in &payload.functions {
            functions.push(function_node_row(
                func,
                project_id,
                project_name,
                language,
                repo,
                build_system,
            ));
            relations.push(contains_row(&file_id, &func.symbol_id));
        }
        for rel in &payload.relations {
            relations.push({
                let mut row = Map::new();
                row.insert("source_id".into(), json!(rel.source_id));
                row.insert("target_id".into(), json!(rel.target_id));
                row.insert("rel_type".into(), json!(rel.rel_type));
                row.insert("properties".into(), Value::Object(rel.properties.clone()));
                row
            });
        }
        for call in &payload.calls {
            if let Some(callee_id) = index.resolve(call) {
                calls.push({
                    let mut row = Map::new();
                    row.insert("caller_id".into(), json!(call.caller_id));
                    row.insert("callee_id".into(), json!(callee_id));
                    // Explicit scope: khớp journal metadata project_id của
                    // orchestrator run, giữ journal-less run hợp lệ (contract
                    // `_require_call_project_scope` của writer). php_analyzer.py
                    // tham chiếu chưa gửi field này — writer (Py + Rust) đều
                    // từ chối row thiếu project_id.
                    row.insert("project_id".into(), json!(project_id));
                    row
                });
            }
        }
    }

    AssembledGraph {
        projects,
        namespaces,
        files,
        types,
        functions,
        relations,
        calls,
    }
}

fn contains_row(source_id: &str, target_id: &str) -> Row {
    let mut row = Map::new();
    row.insert("source_id".into(), json!(source_id));
    row.insert("target_id".into(), json!(target_id));
    row.insert("rel_type".into(), json!("CONTAINS"));
    row.insert("properties".into(), json!({}));
    row
}

#[allow(clippy::too_many_arguments)]
fn namespace_node_row(
    ns: &NamespaceDef,
    project_id: &str,
    project_name: &str,
    language: &str,
    repo: &str,
    build_system: &str,
) -> Row {
    let mut row = Map::new();
    row.insert("id".into(), json!(ns.symbol_id));
    row.insert("name".into(), json!(ns.name));
    row.insert("qualified_name".into(), json!(ns.qualified_name));
    row.insert("file_path".into(), json!(ns.file_path));
    row.insert("start_line".into(), json!(ns.start_line));
    row.insert("end_line".into(), json!(ns.end_line));
    row.insert("code".into(), json!(ns.code));
    row.insert("comment".into(), json!(ns.comment));
    row.insert("summary".into(), json!(ns.summary));
    row.insert("note".into(), json!(ns.note));
    row.insert("project_id".into(), json!(project_id));
    row.insert("project_name".into(), json!(project_name));
    row.insert("language".into(), json!(language));
    row.insert("repo".into(), json!(repo));
    row.insert("build_system".into(), json!(build_system));
    row
}

#[allow(clippy::too_many_arguments)]
fn type_node_row(
    type_def: &TypeDef,
    project_id: &str,
    project_name: &str,
    language: &str,
    repo: &str,
    build_system: &str,
) -> Row {
    let mut row = Map::new();
    row.insert("id".into(), json!(type_def.symbol_id));
    row.insert("name".into(), json!(type_def.name));
    row.insert("qualified_name".into(), json!(type_def.qualified_name));
    row.insert("kind".into(), json!(type_def.kind));
    row.insert("file_path".into(), json!(type_def.file_path));
    row.insert("start_line".into(), json!(type_def.start_line));
    row.insert("end_line".into(), json!(type_def.end_line));
    row.insert("code".into(), json!(type_def.code));
    row.insert("comment".into(), json!(type_def.comment));
    row.insert("summary".into(), json!(type_def.summary));
    row.insert("note".into(), json!(type_def.note));
    row.insert("exported".into(), json!(type_def.exported));
    row.insert("project_id".into(), json!(project_id));
    row.insert("project_name".into(), json!(project_name));
    row.insert("language".into(), json!(language));
    row.insert("repo".into(), json!(repo));
    row.insert("build_system".into(), json!(build_system));
    row
}

#[allow(clippy::too_many_arguments)]
fn function_node_row(
    func: &FunctionDef,
    project_id: &str,
    project_name: &str,
    language: &str,
    repo: &str,
    build_system: &str,
) -> Row {
    let mut row = Map::new();
    row.insert("id".into(), json!(func.symbol_id));
    row.insert("name".into(), json!(func.name));
    row.insert("qualified_name".into(), json!(func.qualified_name));
    row.insert("kind".into(), json!(func.kind));
    row.insert("scope_name".into(), json!(func.scope_name));
    row.insert("class_name".into(), json!(Value::Null));
    row.insert("package_name".into(), json!(Value::Null));
    row.insert("file_path".into(), json!(func.file_path));
    row.insert("start_line".into(), json!(func.start_line));
    row.insert("end_line".into(), json!(func.end_line));
    row.insert("arity".into(), json!(func.arity));
    row.insert("code".into(), json!(func.code));
    row.insert("comment".into(), json!(func.comment));
    row.insert("summary".into(), json!(func.summary));
    row.insert("note".into(), json!(func.note));
    row.insert("exported".into(), json!(func.exported));
    row.insert("project_id".into(), json!(project_id));
    row.insert("project_name".into(), json!(project_name));
    row.insert("language".into(), json!(language));
    row.insert("repo".into(), json!(repo));
    row.insert("build_system".into(), json!(build_system));
    row
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::phpparse::CallEdge;

    fn entry(symbol_id: &str, scope: Option<&str>) -> FuncEntry {
        FuncEntry {
            symbol_id: symbol_id.to_string(),
            scope_name: scope.map(str::to_string),
        }
    }

    #[test]
    fn resolve_prefers_arity_then_scope() {
        let mut index = FunctionIndex::default();
        index.by_name.insert(
            "helper".into(),
            vec![entry("a@x.php", Some("Ns::A")), entry("b@y.php", None)],
        );
        index.by_name_arity.insert(
            ("helper".into(), 1),
            vec![entry("a@x.php", Some("Ns::A")), entry("b@y.php", None)],
        );
        index
            .by_name_arity
            .insert(("helper".into(), 2), vec![entry("b@y.php", None)]);

        // Arity hit đơn → chọn ngay.
        let call = CallEdge {
            caller_id: "c@z.php".into(),
            caller_scope: None,
            callee_name: "helper".into(),
            callee_arity: 2,
        };
        assert_eq!(index.resolve(&call).as_deref(), Some("b@y.php"));

        // Arity hit nhiều → lọc caller_scope còn đúng 1.
        let call = CallEdge {
            caller_id: "c@z.php".into(),
            caller_scope: Some("Ns::A".into()),
            callee_name: "helper".into(),
            callee_arity: 1,
        };
        assert_eq!(index.resolve(&call).as_deref(), Some("a@x.php"));

        // Arity miss → by_name nhiều, scope không khớp → None.
        let call = CallEdge {
            caller_id: "c@z.php".into(),
            caller_scope: Some("Other".into()),
            callee_name: "helper".into(),
            callee_arity: 9,
        };
        assert_eq!(index.resolve(&call), None);

        // Unknown name → None.
        let call = CallEdge {
            caller_id: "c@z.php".into(),
            caller_scope: None,
            callee_name: "missing".into(),
            callee_arity: 0,
        };
        assert_eq!(index.resolve(&call), None);
    }
}
