//! Call resolution + row assembly — port `build_call_graph` phần graph write:
//! function index, `resolve_callee_id` (arity → self-field type → caller
//! scope), IMPORTS edges, OVERRIDES edges, JSON rows cho `write_all`.

use std::collections::BTreeMap;

use serde_json::{json, Map, Value};

use crate::pyparse::{CallEdge, ClassDef, FilePayload, FunctionDef, Row};

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
    class_self_fields: BTreeMap<String, BTreeMap<String, String>>,
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
            for cls in &payload.classes {
                if !cls.self_fields.is_empty() {
                    index
                        .class_self_fields
                        .insert(cls.name.clone(), cls.self_fields.clone());
                }
            }
        }
        index
    }

    /// `resolve_callee_id` — arity candidates → by_name → single → self-field
    /// type → caller scope.
    fn resolve(&self, call: &CallEdge) -> Option<String> {
        let name = &call.callee_name;
        let mut candidates: Option<&Vec<FuncEntry>> = None;
        if let Some(arity) = call.callee_arity {
            candidates = self.by_name_arity.get(&(name.clone(), arity));
        }
        let candidates = match candidates {
            Some(candidates) if !candidates.is_empty() => candidates,
            _ => self.by_name.get(name)?,
        };
        if candidates.len() == 1 {
            return Some(candidates[0].symbol_id.clone());
        }
        // Self-field type resolution: self.repo.save() → UserRepository.save
        if let Some(receiver) = &call.callee_receiver
            && let Some(field_name) = receiver.strip_prefix("self.").and_then(|rest| {
                rest.split('.').next().filter(|first| !first.is_empty())
            }) {
                let caller_scope = call.caller_scope.clone().unwrap_or_default();
                let class_name = caller_scope.rsplit("::").next().unwrap_or("");
                if !class_name.is_empty()
                    && let Some(fields) = self.class_self_fields.get(class_name)
                        && let Some(resolved_type) = fields.get(field_name) {
                            let type_scoped: Vec<&FuncEntry> = candidates
                                .iter()
                                .filter(|c| {
                                    c.scope_name
                                        .as_deref()
                                        .unwrap_or("")
                                        .ends_with(resolved_type.as_str())
                                })
                                .collect();
                            if type_scoped.len() == 1 {
                                return Some(type_scoped[0].symbol_id.clone());
                            }
                        }
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
    module_to_rel_path: &std::collections::HashMap<String, String>,
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
        // IMPORTS edges: file → imported file
        for imp_str in &file_def.imports {
            let Some(target_path) =
                crate::pyparse::resolve_import_path(imp_str, module_to_rel_path)
            else {
                continue;
            };
            if target_path != file_id {
                relations.push({
                    let mut row = Map::new();
                    row.insert("source_id".into(), json!(file_id));
                    row.insert("target_id".into(), json!(target_path));
                    row.insert("rel_type".into(), json!("IMPORTS"));
                    row.insert("properties".into(), json!({}));
                    row
                });
            }
        }
        for ns in &payload.namespaces {
            namespaces.push(value_row(ns));
            relations.push(contains_row(&file_id, ns_id(ns)));
        }
        for class_def in &payload.classes {
            types.push(class_node_row(class_def, project_id, project_name, language, repo, build_system));
            relations.push(contains_row(&file_id, &class_def.symbol_id));
            for base in &class_def.base_classes {
                relations.push({
                    let mut row = Map::new();
                    row.insert("source_id".into(), json!(class_def.symbol_id));
                    row.insert("target_id".into(), json!(base));
                    row.insert("rel_type".into(), json!("INHERITS_FROM"));
                    row.insert("properties".into(), json!({ "base_name": base }));
                    row
                });
            }
        }
        for func in &payload.functions {
            functions.push(function_node_row(func, project_id, project_name, language, repo, build_system));
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
                    // orchestrator run; giữ journal-less run hợp lệ.
                    row.insert("project_id".into(), json!(project_id));
                    row
                });
            }
        }
    }

    // ── OVERRIDES edges — method_by_class: {scope: {method_name: id}} ────
    let mut method_by_class: BTreeMap<String, BTreeMap<String, String>> = BTreeMap::new();
    for func_row in &functions {
        let scope = func_row
            .get("scope_name")
            .and_then(Value::as_str)
            .unwrap_or("");
        if !scope.is_empty() {
            let name = func_row.get("name").and_then(Value::as_str).unwrap_or("");
            let id = func_row.get("id").and_then(Value::as_str).unwrap_or("");
            method_by_class
                .entry(scope.to_string())
                .or_default()
                .insert(name.to_string(), id.to_string());
        }
    }
    for payload in payloads {
        for cls in &payload.classes {
            let cls_id = &cls.symbol_id;
            for base in &cls.base_classes {
                let Some(base_methods) = method_by_class.get(base) else {
                    continue;
                };
                let my_methods = method_by_class.get(cls_id);
                let Some(my_methods) = my_methods else { continue };
                for (method_name, method_id) in my_methods {
                    if let Some(base_method_id) = base_methods.get(method_name) {
                        relations.push({
                            let mut row = Map::new();
                            row.insert("source_id".into(), json!(method_id));
                            row.insert("target_id".into(), json!(base_method_id));
                            row.insert("rel_type".into(), json!("OVERRIDES"));
                            row.insert("properties".into(), json!({}));
                            row
                        });
                    }
                }
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

fn value_row(value: &Value) -> Row {
    value.as_object().cloned().unwrap_or_default()
}

fn ns_id(ns: &Value) -> &str {
    ns.get("symbol_id").and_then(Value::as_str).unwrap_or("")
}

fn class_node_row(
    class_def: &ClassDef,
    project_id: &str,
    project_name: &str,
    language: &str,
    repo: &str,
    build_system: &str,
) -> Row {
    let mut row = Map::new();
    row.insert("id".into(), json!(class_def.symbol_id));
    row.insert("name".into(), json!(class_def.name));
    row.insert("qualified_name".into(), json!(class_def.qualified_name));
    row.insert("kind".into(), json!(class_def.kind));
    row.insert("file_path".into(), json!(class_def.file_path));
    row.insert("start_line".into(), json!(class_def.start_line));
    row.insert("end_line".into(), json!(class_def.end_line));
    row.insert("code".into(), json!(class_def.code));
    row.insert("comment".into(), json!(class_def.comment));
    row.insert("summary".into(), json!(class_def.summary));
    row.insert("note".into(), json!(class_def.note));
    row.insert("exported".into(), json!(class_def.exported));
    row.insert("base_classes".into(), json!(class_def.base_classes));
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
    row.insert("intent".into(), json!(func.intent));
    row.insert("inferred_doc".into(), json!(func.inferred_doc));
    row.insert("doc_confidence".into(), json!(func.doc_confidence));
    row.insert("side_effect".into(), json!(func.side_effect));
    row.insert("is_entrypoint".into(), json!(func.is_entrypoint));
    row.insert("entrypoint_kind".into(), json!(func.entrypoint_kind));
    row.insert("project_id".into(), json!(project_id));
    row.insert("project_name".into(), json!(project_name));
    row.insert("language".into(), json!(language));
    row.insert("repo".into(), json!(repo));
    row.insert("build_system".into(), json!(build_system));
    row
}
