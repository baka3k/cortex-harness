//! Port của `perl_analyzer.py::build_graph_rows` — map normalized Perl
//! records sang canonical writer rows (Namespace/File/Function/Field +
//! CONTAINS/DECLARES/IMPORTS + CALLS).

use std::collections::{BTreeMap, BTreeSet};

use serde_json::{json, Map, Value};

use cortex_graph_writer::language_writer::LanguageCodeWriter;

use crate::models::{AnalysisResult, SymbolRecord};

pub type Row = Map<String, Value>;

fn common_fields(
    project_id: &str,
    project_name: &str,
    repo: &str,
    build_system: &str,
) -> Row {
    let mut row = Row::new();
    row.insert("project_id".into(), json!(project_id));
    row.insert("project_name".into(), json!(project_name));
    row.insert("language".into(), json!("perl"));
    row.insert("repo".into(), json!(repo));
    row.insert("build_system".into(), json!(build_system));
    row
}

fn symbol_label(symbol: &SymbolRecord) -> &'static str {
    match symbol.kind.as_str() {
        "package" => "Namespace",
        "subroutine" => "Function",
        "variable" => "Field",
        other => panic!("unknown perl symbol kind: {other}"),
    }
}

fn merged_row(fields: &[(&str, Value)], common: &Row) -> Row {
    let mut row = Row::new();
    for (key, value) in fields {
        row.insert((*key).to_string(), value.clone());
    }
    for (key, value) in common {
        row.insert(key.clone(), value.clone());
    }
    row
}

fn relation_row(
    source_id: &str,
    source_label: &str,
    target_id: &str,
    target_label: &str,
    rel_type: &str,
    project_id: &str,
    properties: Row,
) -> Row {
    let mut row = Row::new();
    row.insert("source_id".into(), json!(source_id));
    row.insert("source_label".into(), json!(source_label));
    row.insert("target_id".into(), json!(target_id));
    row.insert("target_label".into(), json!(target_label));
    row.insert("rel_type".into(), json!(rel_type));
    row.insert(
        "properties".into(),
        Value::Object(properties),
    );
    row.insert("project_id".into(), json!(project_id));
    row
}

pub struct GraphRows {
    pub files: Vec<Row>,
    pub namespaces: Vec<Row>,
    pub functions: Vec<Row>,
    pub fields: Vec<Row>,
    pub relations: Vec<Row>,
    pub calls: Vec<Row>,
}

/// `build_graph_rows` — Python sort theo `id` cho node rows; relations/calls
/// dedup theo key-tuple nhưng giữ insertion order (dict.values()).
pub fn build_graph_rows(
    result: &AnalysisResult,
    project_name: &str,
    repo: &str,
    build_system: &str,
) -> GraphRows {
    let common = common_fields(&result.project_id, project_name, repo, build_system);
    let mut files: Vec<Row> = Vec::new();
    let mut namespaces: Vec<Row> = Vec::new();
    let mut functions: Vec<Row> = Vec::new();
    let mut fields: Vec<Row> = Vec::new();
    let mut relations: Vec<Row> = Vec::new();
    let mut calls: Vec<Row> = Vec::new();
    // package_ids[(fq_name, file_path)] = symbol_id — dict giữ ghi đè cuối.
    let mut package_ids: BTreeMap<(String, String), String> = BTreeMap::new();

    for record in &result.files {
        let summary = format!("Perl source ({} coverage)", record.coverage);
        let note = format!(
            "parser={}; grammar={}; sha256={}; errors={}",
            record.parser_version, record.grammar_version, record.content_sha256, record.error_count
        );
        files.push(merged_row(
            &[
                ("id", json!(record.file_path)),
                ("path", json!(record.file_path)),
                ("start_line", json!(1)),
                ("end_line", json!(record.line_count)),
                ("code", json!("")),
                ("comment", json!("")),
                ("summary", json!(summary)),
                ("note", json!(note)),
            ],
            &common,
        ));
    }

    for symbol in &result.symbols {
        let base_summary = if !symbol.documentation.is_empty() {
            symbol.documentation.clone()
        } else {
            format!("Perl {}: {}", symbol.kind, symbol.fq_name)
        };
        let note = format!(
            "declaration_kind={}; prototype={}; attributes={}",
            symbol.declaration_kind,
            symbol.prototype,
            symbol.attributes.join(",")
        );
        let mut base = merged_row(
            &[
                ("id", json!(symbol.symbol_id)),
                ("name", json!(symbol.name)),
                ("qualified_name", json!(symbol.fq_name)),
                ("file_path", json!(symbol.file_path)),
                ("start_line", json!(symbol.span.start_line)),
                ("end_line", json!(symbol.span.end_line)),
                ("code", json!(symbol.code)),
                ("comment", json!(symbol.documentation)),
                ("summary", json!(base_summary)),
                ("note", json!(note)),
            ],
            &common,
        );
        if symbol.kind == "package" {
            package_ids.insert(
                (symbol.fq_name.clone(), symbol.file_path.clone()),
                symbol.symbol_id.clone(),
            );
            namespaces.push(base);
        } else if symbol.kind == "subroutine" {
            base.insert("kind".into(), json!("function"));
            base.insert("class_name".into(), json!(""));
            base.insert("package_name".into(), json!(symbol.package));
            base.insert("scope_name".into(), json!(symbol.scope));
            base.insert("start_byte".into(), json!(symbol.span.start_byte));
            base.insert("end_byte".into(), json!(symbol.span.end_byte));
            base.insert("arity".into(), json!(symbol.arity));
            base.insert("exported".into(), json!(false));
            base.insert("external".into(), json!(false));
            base.insert("builtin".into(), json!(false));
            base.insert("react_role".into(), json!(""));
            base.insert("middleware_kind".into(), json!(""));
            functions.push(base);
        } else if symbol.kind == "variable" {
            base.insert("scope_name".into(), json!(symbol.scope));
            base.insert("type_signature".into(), json!(symbol.declaration_kind));
            fields.push(base);
        }
        relations.push(relation_row(
            &symbol.file_path,
            "File",
            &symbol.symbol_id,
            symbol_label(symbol),
            "CONTAINS",
            &result.project_id,
            {
                let mut props = Row::new();
                props.insert("project_id".into(), json!(result.project_id));
                props
            },
        ));
    }

    for symbol in &result.symbols {
        if symbol.kind != "subroutine" && symbol.kind != "variable" {
            continue;
        }
        if let Some(package_id) = package_ids.get(&(symbol.package.clone(), symbol.file_path.clone()))
        {
            relations.push(relation_row(
                package_id,
                "Namespace",
                &symbol.symbol_id,
                symbol_label(symbol),
                "DECLARES",
                &result.project_id,
                {
                    let mut props = Row::new();
                    props.insert("project_id".into(), json!(result.project_id));
                    props
                },
            ));
        }
    }

    for item in &result.imports {
        if !item.resolved_path.is_empty() {
            relations.push(relation_row(
                &item.file_path,
                "File",
                &item.resolved_path,
                "File",
                "IMPORTS",
                &result.project_id,
                {
                    let mut props = Row::new();
                    props.insert("module".into(), json!(item.module));
                    props.insert("kind".into(), json!(item.kind));
                    props.insert("conditional".into(), json!(item.is_conditional));
                    props
                },
            ));
        }
    }

    for reference in &result.references {
        if reference.resolution_status == "resolved"
            && !reference.source_symbol_id.is_empty()
            && !reference.target_symbol_id.is_empty()
        {
            let mut call = Row::new();
            call.insert("caller_id".into(), json!(reference.source_symbol_id));
            call.insert("callee_id".into(), json!(reference.target_symbol_id));
            call.insert("call_type".into(), json!(reference.kind));
            // Python reference chạy dưới orchestrator với journal env
            // (configure_journal_env) nên `_require_call_project_scope` lấy
            // project_id từ `journal_config.metadata.project_id`. Rust writer
            // không có journal runtime ⇒ gắn trực tiếp project_id vào call
            // row — cùng giá trị, cùng graph outcome.
            call.insert("project_id".into(), json!(result.project_id));
            calls.push(call);
        }
    }

    let sort_by_id = |rows: &mut Vec<Row>| {
        rows.sort_by(|a, b| {
            let a_id = a.get("id").and_then(Value::as_str).unwrap_or("");
            let b_id = b.get("id").and_then(Value::as_str).unwrap_or("");
            a_id.cmp(b_id)
        });
    };
    sort_by_id(&mut files);
    sort_by_id(&mut namespaces);
    sort_by_id(&mut functions);
    sort_by_id(&mut fields);

    // Dedup kiểu dict-comprehension Python: `{key: row}` — key trùng GIỮ
    // row GẦN NHẤT (last-write-wins) nhưng giữ vị trí xuất hiện đầu tiên.
    let mut relation_positions: BTreeMap<(String, String, String, String, String), usize> =
        BTreeMap::new();
    let mut deduped_relations: Vec<Row> = Vec::new();
    for row in relations {
        let key = (
            row.get("source_label").and_then(Value::as_str).unwrap_or("").to_string(),
            row.get("source_id").and_then(Value::as_str).unwrap_or("").to_string(),
            row.get("rel_type").and_then(Value::as_str).unwrap_or("").to_string(),
            row.get("target_label").and_then(Value::as_str).unwrap_or("").to_string(),
            row.get("target_id").and_then(Value::as_str).unwrap_or("").to_string(),
        );
        match relation_positions.get(&key) {
            Some(&position) => deduped_relations[position] = row,
            None => {
                relation_positions.insert(key, deduped_relations.len());
                deduped_relations.push(row);
            }
        }
    }
    let mut call_positions: BTreeMap<(String, String, String), usize> = BTreeMap::new();
    let mut deduped_calls: Vec<Row> = Vec::new();
    for row in calls {
        let key = (
            row.get("caller_id").and_then(Value::as_str).unwrap_or("").to_string(),
            row.get("callee_id").and_then(Value::as_str).unwrap_or("").to_string(),
            row.get("call_type").and_then(Value::as_str).unwrap_or("").to_string(),
        );
        match call_positions.get(&key) {
            Some(&position) => deduped_calls[position] = row,
            None => {
                call_positions.insert(key, deduped_calls.len());
                deduped_calls.push(row);
            }
        }
    }

    GraphRows {
        files,
        namespaces,
        functions,
        fields,
        relations: deduped_relations,
        calls: deduped_calls,
    }
}

/// `_write_graph` body sau khi có store: cleanup (incremental) → write_all
/// với `use_full_writers=True` (namespaces → files+repo edges → functions →
/// fields → relations_typed → calls).
pub fn write_graph(
    writer: &mut LanguageCodeWriter,
    result: &AnalysisResult,
    project_name: &str,
    repo: &str,
    build_system: &str,
    incremental: bool,
) -> Result<BTreeMap<String, i64>, String> {
    let rows = build_graph_rows(result, project_name, repo, build_system);
    let cleanup_targets: BTreeSet<String> = result
        .changed_paths
        .iter()
        .cloned()
        .chain(result.deleted_paths.iter().cloned())
        .collect();
    if incremental && !cleanup_targets.is_empty() {
        let sorted: Vec<String> = cleanup_targets.into_iter().collect();
        if writer.verbose {
            println!(
                "[cleanup][graph] deleting graph data for {} files",
                sorted.len()
            );
        }
        let store = writer.store.as_mut();
        let (deleted_nodes, deleted_unknown_functions) =
            cortex_analyzer_framework::cleanup::cleanup_graph_files(
                store,
                &result.project_id,
                &sorted,
            )
            .map_err(|error| error.to_string())?;
        if writer.verbose {
            println!(
                "[cleanup][graph] deleted_nodes={deleted_nodes} deleted_unknown_functions={deleted_unknown_functions}"
            );
        }
    }
    let payload = cortex_graph_writer::language_writer::WriteAllPayload {
        projects: &[],
        packages: &[],
        namespaces: &rows.namespaces,
        files: &rows.files,
        classes: &[],
        types: &[],
        function_types: &[],
        functions: &rows.functions,
        fields: &rows.fields,
        aliases: &[],
        templates: &[],
        relations: &rows.relations,
        calls: &rows.calls,
        calls_with_site: &[],
        properties: &[],
        events: &[],
        interfaces: &[],
        enums: &[],
        constants: &[],
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
        files_variant: cortex_graph_writer::language_writer::FilesVariant::Default,
    };
    writer.write_all(&payload).map_err(|error| error.to_string())
}
