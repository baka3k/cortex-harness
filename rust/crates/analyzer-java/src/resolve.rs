//! Port `build_call_graph` phần graph write: function/class index từ
//! index_payloads (toàn bộ file khi incremental), `resolve_callee_id` +
//! `pick_candidate` (class+package → package → imports → first), external
//! classes cho type-edge target không resolve, JSON rows cho `write_all`.

use serde_json::{json, Map, Value};

use crate::javaparse::{CallEdge, FilePayload, Row};

// ── Function index ──────────────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub struct FuncEntry {
    pub symbol_id: String,
    pub class_name: Option<String>,
    pub package_name: Option<String>,
}

/// Index cho call resolution — giữ insertion order của dict Python:
/// `qualified_keys` cho vòng quét `qual.endswith(callee_name)` (key mới giữ
/// vị trí đầu; giá trị qua `qualified` là lần ghi cuối như dict).
#[derive(Default)]
pub struct FunctionIndex {
    by_name: std::collections::HashMap<String, Vec<FuncEntry>>,
    qualified: std::collections::HashMap<String, FuncEntry>,
    qualified_keys: Vec<String>,
    by_class_and_name: std::collections::HashMap<(String, String), Vec<FuncEntry>>,
}

impl FunctionIndex {
    /// Build từ index_payloads (ALL file khi incremental, selected khi full).
    pub fn build(payloads: &[&FilePayload]) -> Self {
        let mut index = FunctionIndex::default();
        for payload in payloads {
            for func in &payload.functions {
                let entry = FuncEntry {
                    symbol_id: func.symbol_id.clone(),
                    class_name: func.class_name.clone(),
                    package_name: func.package_name.clone(),
                };
                index
                    .by_name
                    .entry(func.name.clone())
                    .or_default()
                    .push(entry.clone());
                if !index.qualified.contains_key(&func.qualified_name) {
                    index.qualified_keys.push(func.qualified_name.clone());
                }
                index
                    .qualified
                    .insert(func.qualified_name.clone(), entry.clone());
                if let Some(class_name) = &func.class_name {
                    index
                        .by_class_and_name
                        .entry((class_name.clone(), func.name.clone()))
                        .or_default()
                        .push(entry);
                }
            }
        }
        index
    }

    /// `pick_candidate` — caller class+package → package → imports → đầu tiên.
    fn pick_candidate<'a>(
        &self,
        candidates: &'a [FuncEntry],
        call: &CallEdge,
    ) -> Option<&'a FuncEntry> {
        if let Some(caller_class) = &call.caller_class
            && !caller_class.is_empty()
        {
            for func in candidates {
                if func.class_name.as_ref() == Some(caller_class)
                    && func.package_name.as_ref() == call.caller_package.as_ref()
                {
                    return Some(func);
                }
            }
        }
        if let Some(caller_package) = &call.caller_package
            && !caller_package.is_empty()
        {
            for func in candidates {
                if func.package_name.as_ref() == Some(caller_package) {
                    return Some(func);
                }
            }
        }
        if !call.imports.is_empty() {
            for func in candidates {
                if let Some(package_name) = &func.package_name
                    && call
                        .imports
                        .iter()
                        .any(|imp| imp.starts_with(package_name.as_str()))
                {
                    return Some(func);
                }
            }
        }
        candidates.first()
    }

    /// `resolve_callee_id` — qualified → (class, method) → endswith → by_name.
    pub fn resolve_callee_id(&self, call: &CallEdge) -> Option<String> {
        let callee_name = &call.callee_name;
        let mut candidate: Option<&FuncEntry> = None;

        if callee_name.contains('.') {
            if let Some(entry) = self.qualified.get(callee_name) {
                candidate = Some(entry);
            } else {
                let parts: Vec<&str> = callee_name.split('.').collect();
                let method_name = parts[parts.len() - 1];
                let qualifier = if parts.len() >= 2 {
                    Some(parts[parts.len() - 2])
                } else {
                    None
                };
                if let Some(qualifier) = qualifier
                    && let Some(candidates) = self
                        .by_class_and_name
                        .get(&(qualifier.to_string(), method_name.to_string()))
                {
                    candidate = self.pick_candidate(candidates, call);
                }
                if candidate.is_none() {
                    for qual in &self.qualified_keys {
                        if qual.ends_with(callee_name.as_str()) {
                            candidate = self.qualified.get(qual);
                            break;
                        }
                    }
                }
            }
        }

        if candidate.is_none()
            && let Some(candidates) = self.by_name.get(callee_name)
        {
            candidate = self.pick_candidate(candidates, call);
        }

        candidate.map(|entry| entry.symbol_id.clone())
    }
}

// ── Class index + external classes ──────────────────────────────────────────

/// Kết quả resolve target của type edge — khớp logic 2 pass Python.
pub struct ClassIndex {
    pub by_qualified: std::collections::HashMap<String, String>,
    pub by_name: std::collections::HashMap<String, Vec<String>>,
}

impl ClassIndex {
    pub fn build(payloads: &[&FilePayload]) -> Self {
        let mut index = ClassIndex {
            by_qualified: std::collections::HashMap::new(),
            by_name: std::collections::HashMap::new(),
        };
        for payload in payloads {
            for class_def in &payload.classes {
                index
                    .by_qualified
                    .insert(class_def.qualified_name.clone(), class_def.symbol_id.clone());
                let simple = class_def
                    .name
                    .rsplit('.')
                    .next()
                    .unwrap_or(&class_def.name)
                    .to_string();
                index
                    .by_name
                    .entry(simple)
                    .or_default()
                    .push(class_def.symbol_id.clone());
            }
        }
        index
    }

    /// Resolve target_name theo 3 bước của Python (qualified → package-qualified
    /// → by_name[0]). Trả None khi không resolve (external class).
    pub fn resolve_target(&self, target_name: &str, source_package: Option<&str>) -> Option<String> {
        if target_name.contains('.')
            && let Some(id) = self.by_qualified.get(target_name)
        {
            return Some(id.clone());
        }
        if let Some(package_name) = source_package {
            let qualified = format!("{package_name}.{target_name}");
            if let Some(id) = self.by_qualified.get(&qualified) {
                return Some(id.clone());
            }
        }
        if let Some(candidates) = self.by_name.get(target_name)
            && let Some(first) = candidates.first()
        {
            return Some(first.clone());
        }
        None
    }
}

// ── Row assembly ────────────────────────────────────────────────────────────

pub struct AssembledGraph {
    pub projects: Vec<Row>,
    pub packages: Vec<Row>,
    pub namespaces: Vec<Row>,
    pub files: Vec<Row>,
    pub classes: Vec<Row>,
    pub function_types: Vec<Row>,
    pub functions: Vec<Row>,
    pub relations: Vec<Row>,
    pub calls: Vec<Row>,
}

fn relation_row(
    source_id: &str,
    source_label: &str,
    target_id: &str,
    target_label: &str,
    rel_type: &str,
    properties: Row,
) -> Row {
    let mut row = Map::new();
    row.insert("source_id".into(), json!(source_id));
    row.insert("source_label".into(), json!(source_label));
    row.insert("target_id".into(), json!(target_id));
    row.insert("target_label".into(), json!(target_label));
    row.insert("rel_type".into(), json!(rel_type));
    row.insert("properties".into(), Value::Object(properties));
    row
}

fn scope_props(row: &mut Row, project_id: &str, project_name: &str, language: &str, repo: &str, build_system: &str) {
    row.insert("project_id".into(), json!(project_id));
    row.insert("project_name".into(), json!(project_name));
    row.insert("language".into(), json!(language));
    row.insert("repo".into(), json!(repo));
    row.insert("build_system".into(), json!(build_system));
}

/// `_build_note` dùng lại cho namespace note.
fn build_note(code: &str, comment: &str, summary: &str) -> String {
    crate::javaparse::build_note(code, comment, summary)
}

/// Dựng toàn bộ node/rel rows như `build_call_graph` (graph-write pass).
/// `root_arg` là giá trị `--root` THÔ (project.root khớp Python).
#[allow(clippy::too_many_arguments)]
pub fn assemble_graph(
    selected_payloads: &[&FilePayload],
    _index_payloads: &[&FilePayload],
    class_index: &ClassIndex,
    function_index: &FunctionIndex,
    project_id: &str,
    project_name: &str,
    language: &str,
    repo: &str,
    build_system: &str,
    root_arg: &str,
) -> AssembledGraph {
    const ALLOWED_REL_TYPES: [&str; 5] = ["CONTAINS", "DECLARES", "EXTENDS", "IMPLEMENTS", "TAKES_FUNCTION"];

    let mut graph = AssembledGraph {
        projects: Vec::new(),
        packages: Vec::new(),
        namespaces: Vec::new(),
        files: Vec::new(),
        classes: Vec::new(),
        function_types: Vec::new(),
        functions: Vec::new(),
        relations: Vec::new(),
        calls: Vec::new(),
    };

    let mut project_row = Map::new();
    project_row.insert("id".into(), json!(project_id));
    project_row.insert("name".into(), json!(project_name));
    project_row.insert("language".into(), json!(language));
    project_row.insert("repo".into(), json!(repo));
    project_row.insert("root".into(), json!(root_arg));
    project_row.insert("build_system".into(), json!(build_system));
    graph.projects.push(project_row);

    let mut seen_packages: std::collections::HashSet<String> = std::collections::HashSet::new();
    let mut seen_namespaces: std::collections::HashSet<String> = std::collections::HashSet::new();
    // external_classes — dict insertion order (keys Vec + values HashMap).
    let mut external_class_keys: Vec<String> = Vec::new();
    let mut external_classes: std::collections::HashMap<String, ()> = std::collections::HashMap::new();

    // Pass 0: external classes cho type edges không resolve (khớp loop Python
    // trước `if code_writer`).
    for payload in selected_payloads {
        for edge in &payload.type_edges {
            let target_name = &edge.target_name;
            let resolved = class_index.resolve_target(target_name, edge.source_package.as_deref());
            if resolved.is_none() && !external_classes.contains_key(target_name) {
                external_classes.insert(target_name.clone(), ());
                external_class_keys.push(target_name.clone());
            }
        }
    }

    for payload in selected_payloads {
        let file_def = payload.file_def.as_ref().expect("file_def");
        let file_id = file_def.file_path.clone();
        let mut file_row = Map::new();
        file_row.insert("id".into(), json!(file_id));
        file_row.insert("path".into(), json!(file_id));
        file_row.insert("package_name".into(), json!(file_def.package_name));
        file_row.insert("start_line".into(), json!(file_def.start_line));
        file_row.insert("end_line".into(), json!(file_def.end_line));
        file_row.insert("code".into(), json!(file_def.code));
        file_row.insert("comment".into(), json!(file_def.comment));
        file_row.insert("summary".into(), json!(file_def.summary));
        file_row.insert("note".into(), json!(file_def.note));
        scope_props(
            &mut file_row,
            project_id,
            project_name,
            language,
            repo,
            build_system,
        );
        graph.files.push(file_row);
        graph.relations.push(relation_row(
            project_id,
            "Project",
            &file_id,
            "File",
            "CONTAINS",
            Map::new(),
        ));

        if let Some(package_def) = &payload.package_def {
            let pkg_name = &package_def.name;
            if !seen_packages.contains(pkg_name) {
                seen_packages.insert(pkg_name.clone());
                let mut pkg_row = Map::new();
                pkg_row.insert("id".into(), json!(pkg_name));
                pkg_row.insert("name".into(), json!(pkg_name));
                pkg_row.insert("start_line".into(), json!(package_def.start_line));
                pkg_row.insert("end_line".into(), json!(package_def.end_line));
                pkg_row.insert("code".into(), json!(package_def.code));
                pkg_row.insert("comment".into(), json!(package_def.comment));
                pkg_row.insert("summary".into(), json!(package_def.summary));
                pkg_row.insert("note".into(), json!(package_def.note));
                scope_props(
                    &mut pkg_row,
                    project_id,
                    project_name,
                    language,
                    repo,
                    build_system,
                );
                graph.packages.push(pkg_row);
            }
            let namespace_id = format!("namespace::{pkg_name}");
            if !seen_namespaces.contains(&namespace_id) {
                seen_namespaces.insert(namespace_id.clone());
                let namespace_summary = if package_def.comment.is_empty() {
                    Value::String(String::new())
                } else {
                    json!(package_def.comment)
                };
                let namespace_summary_str = package_def.comment.clone();
                let namespace_note = build_note(
                    &package_def.code,
                    &package_def.comment,
                    &namespace_summary_str,
                );
                let mut ns_row = Map::new();
                ns_row.insert("id".into(), json!(namespace_id));
                ns_row.insert("name".into(), json!(pkg_name));
                ns_row.insert("qualified_name".into(), json!(pkg_name));
                ns_row.insert("file_path".into(), json!(file_id));
                ns_row.insert("start_line".into(), json!(package_def.start_line));
                ns_row.insert("end_line".into(), json!(package_def.end_line));
                ns_row.insert("code".into(), json!(package_def.code));
                ns_row.insert("comment".into(), json!(package_def.comment));
                ns_row.insert("summary".into(), namespace_summary);
                ns_row.insert("note".into(), json!(namespace_note));
                scope_props(
                    &mut ns_row,
                    project_id,
                    project_name,
                    language,
                    repo,
                    build_system,
                );
                graph.namespaces.push(ns_row);
            }
            graph.relations.push(relation_row(
                pkg_name,
                "Package",
                &file_id,
                "File",
                "CONTAINS",
                Map::new(),
            ));
            graph.relations.push(relation_row(
                &namespace_id,
                "Namespace",
                &file_id,
                "File",
                "CONTAINS",
                Map::new(),
            ));
            graph.relations.push(relation_row(
                pkg_name,
                "Package",
                &namespace_id,
                "Namespace",
                "CONTAINS",
                Map::new(),
            ));
        }

        for class_def in &payload.classes {
            let mut class_row = Map::new();
            class_row.insert("id".into(), json!(class_def.symbol_id));
            class_row.insert("name".into(), json!(class_def.name));
            class_row.insert("qualified_name".into(), json!(class_def.qualified_name));
            class_row.insert("kind".into(), json!(class_def.kind));
            class_row.insert("package_name".into(), json!(class_def.package_name));
            class_row.insert("file_path".into(), json!(class_def.file_path));
            class_row.insert("start_line".into(), json!(class_def.start_line));
            class_row.insert("end_line".into(), json!(class_def.end_line));
            class_row.insert("code".into(), json!(class_def.code));
            class_row.insert("comment".into(), json!(class_def.comment));
            class_row.insert("summary".into(), json!(class_def.summary));
            class_row.insert("note".into(), json!(class_def.note));
            class_row.insert("visibility".into(), json!(class_def.visibility));
            class_row.insert("is_public_api".into(), json!(class_def.is_public_api));
            class_row.insert(
                "visibility_source".into(),
                json!(class_def.visibility_source),
            );
            class_row.insert("export_evidence".into(), json!(class_def.export_evidence));
            class_row.insert("signature".into(), json!(class_def.signature));
            scope_props(
                &mut class_row,
                project_id,
                project_name,
                language,
                repo,
                build_system,
            );
            graph.classes.push(class_row);
            if !class_def.file_path.is_empty() {
                graph.relations.push(relation_row(
                    &file_id,
                    "File",
                    &class_def.symbol_id,
                    "Class",
                    "CONTAINS",
                    Map::new(),
                ));
            }
        }

        for func_type in &payload.function_types {
            let mut ft_row = Map::new();
            ft_row.insert("id".into(), json!(func_type.symbol_id));
            ft_row.insert("type_signature".into(), json!(func_type.type_signature));
            ft_row.insert("file_path".into(), json!(func_type.file_path));
            ft_row.insert("start_line".into(), json!(func_type.start_line));
            ft_row.insert("end_line".into(), json!(func_type.end_line));
            ft_row.insert("code".into(), json!(func_type.code));
            scope_props(
                &mut ft_row,
                project_id,
                project_name,
                language,
                repo,
                build_system,
            );
            graph.function_types.push(ft_row);
        }

        for func in &payload.functions {
            let mut func_row = Map::new();
            func_row.insert("id".into(), json!(func.symbol_id));
            func_row.insert("name".into(), json!(func.name));
            func_row.insert("qualified_name".into(), json!(func.qualified_name));
            func_row.insert("kind".into(), json!(func.kind));
            func_row.insert("class_name".into(), json!(func.class_name));
            func_row.insert("package_name".into(), json!(func.package_name));
            func_row.insert("scope_name".into(), Value::Null);
            func_row.insert("file_path".into(), json!(func.file_path));
            func_row.insert("start_line".into(), json!(func.start_line));
            func_row.insert("end_line".into(), json!(func.end_line));
            func_row.insert("arity".into(), json!(func.arity));
            func_row.insert("code".into(), json!(func.code));
            func_row.insert("comment".into(), json!(func.comment));
            func_row.insert("summary".into(), json!(func.summary));
            func_row.insert("note".into(), json!(func.note));
            func_row.insert("exported".into(), json!(func.is_public_api));
            func_row.insert("visibility".into(), json!(func.visibility));
            func_row.insert("is_public_api".into(), json!(func.is_public_api));
            func_row.insert(
                "visibility_source".into(),
                json!(func.visibility_source),
            );
            func_row.insert("export_evidence".into(), json!(func.export_evidence));
            func_row.insert("signature".into(), json!(func.signature));
            scope_props(
                &mut func_row,
                project_id,
                project_name,
                language,
                repo,
                build_system,
            );
            graph.functions.push(func_row);
            graph.relations.push(relation_row(
                &file_id,
                "File",
                &func.symbol_id,
                "Function",
                "CONTAINS",
                Map::new(),
            ));
            if let Some(class_name) = &func.class_name {
                let owner_class_id =
                    crate::javaparse::class_id(func.package_name.as_deref(), class_name);
                graph.relations.push(relation_row(
                    &owner_class_id,
                    "Class",
                    &func.symbol_id,
                    "Function",
                    "DECLARES",
                    Map::new(),
                ));
            }
        }

        for edge in &payload.type_edges {
            let target_name = &edge.target_name;
            let resolved = class_index.resolve_target(target_name, edge.source_package.as_deref());
            let target_id = resolved.unwrap_or_else(|| target_name.clone());
            if ALLOWED_REL_TYPES.contains(&edge.rel_type.as_str()) {
                graph.relations.push(relation_row(
                    &edge.source_id,
                    "Class",
                    &target_id,
                    "Class",
                    &edge.rel_type,
                    Map::new(),
                ));
            }
        }

        for rel in &payload.relations {
            if ALLOWED_REL_TYPES.contains(&rel.rel_type.as_str()) {
                graph.relations.push(relation_row(
                    &rel.source_id,
                    &rel.source_label,
                    &rel.target_id,
                    &rel.target_label,
                    &rel.rel_type,
                    rel.properties.clone(),
                ));
            }
        }

        for call in &payload.calls {
            let callee_id = call
                .callee_id
                .clone()
                .or_else(|| function_index.resolve_callee_id(call));
            if let Some(callee_id) = callee_id
                && !callee_id.is_empty()
            {
                let mut call_row = Map::new();
                call_row.insert("caller_id".into(), json!(call.caller_id));
                call_row.insert("callee_id".into(), json!(callee_id));
                // Explicit scope: khớp journal metadata project_id của
                // orchestrator run phía Python (write_calls bắt buộc).
                call_row.insert("project_id".into(), json!(project_id));
                graph.calls.push(call_row);
            }
        }
    }

    // External classes — append sau loop chính (khớp Python).
    for key in &external_class_keys {
        let mut class_row = Map::new();
        class_row.insert("id".into(), json!(key));
        class_row.insert("name".into(), json!(key));
        class_row.insert("qualified_name".into(), json!(key));
        class_row.insert("kind".into(), json!("external"));
        class_row.insert("package_name".into(), Value::Null);
        class_row.insert("file_path".into(), json!(""));
        class_row.insert("start_line".into(), json!(0));
        class_row.insert("end_line".into(), json!(0));
        class_row.insert("code".into(), json!(""));
        class_row.insert("comment".into(), json!(""));
        class_row.insert("summary".into(), json!(""));
        class_row.insert("note".into(), json!(""));
        class_row.insert("visibility".into(), json!("unknown"));
        class_row.insert("is_public_api".into(), json!(false));
        class_row.insert("visibility_source".into(), json!("source-modifier"));
        class_row.insert("export_evidence".into(), json!(""));
        class_row.insert("signature".into(), json!(""));
        scope_props(
            &mut class_row,
            project_id,
            project_name,
            language,
            repo,
            build_system,
        );
        graph.classes.push(class_row);
    }

    graph
}
