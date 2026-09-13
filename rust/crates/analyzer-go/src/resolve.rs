//! Row assembly — port `go_analyzer.py::_build_note`/`_repo_name`/
//! `_with_common_fields`/`_prepare_write_rows`: JSON rows cho
//! `LanguageCodeWriter::write_all`. CALLS rows thêm `project_id` tường minh
//! (contract `_require_call_project_scope` của writer — Python tham chiếu chạy
//! qua journal-shadow env để nhận cùng giá trị).

use serde_json::{Value, json};

use crate::goparse::{AliasDef, CallEdge, FieldDef, FilePayload, FunctionDef, NamespaceDef, Row, TemplateDef, TypeDef};

/// `_build_note` — Summary/Comment/Code sections nối "\n\n".
pub fn build_note(code: &str, comment: &str, summary: &str) -> String {
    let mut parts: Vec<String> = Vec::new();
    if !summary.is_empty() {
        parts.push(format!("Summary:\n{summary}"));
    }
    if !comment.is_empty() {
        parts.push(format!("Comment:\n{comment}"));
    }
    if !code.is_empty() {
        parts.push(format!("Code:\n{code}"));
    }
    parts.join("\n\n")
}

/// `_with_common_fields` — setdefault summary/note + project scope fields.
fn with_common_fields(
    mut row: Row,
    project_id: &str,
    project_name: &str,
    language: &str,
    repo: &str,
    build_system: &str,
) -> Row {
    if !row.contains_key("summary") {
        let comment = row
            .get("comment")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        row.insert("summary".into(), json!(comment));
    }
    if !row.contains_key("note") {
        let code = row
            .get("code")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        let comment = row
            .get("comment")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        let summary = row
            .get("summary")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        row.insert("note".into(), json!(build_note(&code, &comment, &summary)));
    }
    row.insert("project_id".into(), json!(project_id));
    row.insert("project_name".into(), json!(project_name));
    row.insert("language".into(), json!(language));
    row.insert("repo".into(), json!(repo));
    row.insert("build_system".into(), json!(build_system));
    row
}

pub struct AssembledGraph {
    pub files: Vec<Row>,
    pub namespaces: Vec<Row>,
    pub types: Vec<Row>,
    pub functions: Vec<Row>,
    pub fields: Vec<Row>,
    pub aliases: Vec<Row>,
    pub templates: Vec<Row>,
    pub relations: Vec<Row>,
    pub calls: Vec<Row>,
}

/// `_prepare_write_rows` — thứ tự payload và rows giữ nguyên.
pub fn prepare_write_rows(
    payloads: &[FilePayload],
    project_id: &str,
    project_name: &str,
    language: &str,
    repo: &str,
    build_system: &str,
) -> AssembledGraph {
    let mut files: Vec<Row> = Vec::new();
    let mut namespaces: Vec<Row> = Vec::new();
    let mut types: Vec<Row> = Vec::new();
    let mut functions: Vec<Row> = Vec::new();
    let mut fields: Vec<Row> = Vec::new();
    let mut aliases: Vec<Row> = Vec::new();
    let mut templates: Vec<Row> = Vec::new();
    let mut relations: Vec<Row> = Vec::new();
    let mut calls: Vec<Row> = Vec::new();

    for payload in payloads {
        let file_def = payload.file_def.as_ref().expect("file_def");
        let rel_path = file_def.file_path.replace('\\', "/");
        let file_row = {
            let mut row = Row::new();
            row.insert("id".into(), json!(rel_path));
            row.insert("path".into(), json!(rel_path));
            row.insert("start_line".into(), json!(file_def.start_line));
            row.insert("end_line".into(), json!(file_def.end_line));
            row.insert("code".into(), json!(file_def.code));
            row.insert("comment".into(), json!(file_def.comment));
            row.insert("summary".into(), json!(file_def.summary));
            row
        };
        files.push(with_common_fields(
            file_row,
            project_id,
            project_name,
            language,
            repo,
            build_system,
        ));

        for item in &payload.namespaces {
            namespaces.push(namespace_row(item, project_id, project_name, language, repo, build_system));
        }
        for item in &payload.types {
            types.push(type_row(item, project_id, project_name, language, repo, build_system));
        }
        for item in &payload.functions {
            functions.push(function_row(item, project_id, project_name, language, repo, build_system));
        }
        for item in &payload.fields {
            fields.push(field_row(item, project_id, project_name, language, repo, build_system));
        }
        for item in &payload.aliases {
            aliases.push(alias_row(item, project_id, project_name, language, repo, build_system));
        }
        for item in &payload.templates {
            templates.push(template_row(item, project_id, project_name, language, repo, build_system));
        }
        for item in &payload.relations {
            let mut row = Row::new();
            row.insert("source_id".into(), json!(item.source_id));
            row.insert("source_label".into(), json!(item.source_label));
            row.insert("target_id".into(), json!(item.target_id));
            row.insert("target_label".into(), json!(item.target_label));
            row.insert("rel_type".into(), json!(item.rel_type));
            row.insert("properties".into(), Value::Object(item.properties.clone()));
            relations.push(row);
        }
        for item in &payload.calls {
            if item.callee_id.is_some() {
                calls.push(call_row(item, project_id));
            }
        }
    }

    AssembledGraph {
        files,
        namespaces,
        types,
        functions,
        fields,
        aliases,
        templates,
        relations,
        calls,
    }
}

fn namespace_row(
    ns: &NamespaceDef,
    project_id: &str,
    project_name: &str,
    language: &str,
    repo: &str,
    build_system: &str,
) -> Row {
    let mut row = Row::new();
    row.insert("id".into(), json!(ns.symbol_id));
    row.insert("qualified_name".into(), json!(ns.qualified_name));
    row.insert("name".into(), json!(ns.name));
    row.insert("file_path".into(), json!(ns.file_path));
    row.insert("start_line".into(), json!(ns.start_line));
    row.insert("end_line".into(), json!(ns.end_line));
    row.insert("code".into(), json!(ns.code));
    row.insert("comment".into(), json!(ns.comment));
    with_common_fields(row, project_id, project_name, language, repo, build_system)
}

fn type_row(
    type_def: &TypeDef,
    project_id: &str,
    project_name: &str,
    language: &str,
    repo: &str,
    build_system: &str,
) -> Row {
    let mut row = Row::new();
    row.insert("id".into(), json!(type_def.symbol_id));
    row.insert("qualified_name".into(), json!(type_def.qualified_name));
    row.insert("name".into(), json!(type_def.name));
    row.insert("kind".into(), json!(type_def.kind));
    row.insert("file_path".into(), json!(type_def.file_path));
    row.insert("start_line".into(), json!(type_def.start_line));
    row.insert("end_line".into(), json!(type_def.end_line));
    row.insert("code".into(), json!(type_def.code));
    row.insert("comment".into(), json!(type_def.comment));
    with_common_fields(row, project_id, project_name, language, repo, build_system)
}

fn function_row(
    func: &FunctionDef,
    project_id: &str,
    project_name: &str,
    language: &str,
    repo: &str,
    build_system: &str,
) -> Row {
    let mut row = Row::new();
    row.insert("id".into(), json!(func.symbol_id));
    row.insert("qualified_name".into(), json!(func.qualified_name));
    row.insert("name".into(), json!(func.name));
    row.insert("kind".into(), json!(func.kind));
    row.insert("scope_name".into(), json!(func.scope_name));
    row.insert("file_path".into(), json!(func.file_path));
    row.insert("start_byte".into(), json!(func.start_byte));
    row.insert("end_byte".into(), json!(func.end_byte));
    row.insert("start_line".into(), json!(func.start_line));
    row.insert("end_line".into(), json!(func.end_line));
    row.insert("arity".into(), json!(func.arity));
    row.insert("code".into(), json!(func.code));
    row.insert("comment".into(), json!(func.comment));
    // setdefault class_name/package_name = scope_name (None giữ nguyên null).
    row.insert("class_name".into(), json!(func.scope_name));
    row.insert("package_name".into(), json!(func.scope_name));
    with_common_fields(row, project_id, project_name, language, repo, build_system)
}

fn field_row(
    field: &FieldDef,
    project_id: &str,
    project_name: &str,
    language: &str,
    repo: &str,
    build_system: &str,
) -> Row {
    let mut row = Row::new();
    row.insert("id".into(), json!(field.symbol_id));
    row.insert("qualified_name".into(), json!(field.qualified_name));
    row.insert("name".into(), json!(field.name));
    row.insert("scope_name".into(), json!(field.scope_name));
    row.insert("type_signature".into(), json!(field.type_signature));
    row.insert("file_path".into(), json!(field.file_path));
    row.insert("start_line".into(), json!(field.start_line));
    row.insert("end_line".into(), json!(field.end_line));
    row.insert("code".into(), json!(field.code));
    with_common_fields(row, project_id, project_name, language, repo, build_system)
}

fn alias_row(
    alias: &AliasDef,
    project_id: &str,
    project_name: &str,
    language: &str,
    repo: &str,
    build_system: &str,
) -> Row {
    let mut row = Row::new();
    row.insert("id".into(), json!(alias.symbol_id));
    row.insert("qualified_name".into(), json!(alias.qualified_name));
    row.insert("name".into(), json!(alias.name));
    row.insert("kind".into(), json!(alias.kind));
    row.insert("target_name".into(), json!(alias.target_name));
    row.insert("file_path".into(), json!(alias.file_path));
    row.insert("start_line".into(), json!(alias.start_line));
    row.insert("end_line".into(), json!(alias.end_line));
    row.insert("code".into(), json!(alias.code));
    with_common_fields(row, project_id, project_name, language, repo, build_system)
}

fn template_row(
    template: &TemplateDef,
    project_id: &str,
    project_name: &str,
    language: &str,
    repo: &str,
    build_system: &str,
) -> Row {
    let mut row = Row::new();
    row.insert("id".into(), json!(template.symbol_id));
    row.insert("name".into(), json!(template.name));
    row.insert("file_path".into(), json!(template.file_path));
    row.insert("start_line".into(), json!(template.start_line));
    row.insert("end_line".into(), json!(template.end_line));
    row.insert("code".into(), json!(template.code));
    with_common_fields(row, project_id, project_name, language, repo, build_system)
}

fn call_row(call: &CallEdge, project_id: &str) -> Row {
    let mut row = Row::new();
    row.insert("caller_id".into(), json!(call.caller_id));
    row.insert("caller_file".into(), json!(call.caller_file));
    row.insert("caller_scope".into(), json!(call.caller_scope));
    row.insert("call_line".into(), json!(call.call_line));
    row.insert("call_column".into(), json!(call.call_column));
    row.insert("call_start_byte".into(), json!(call.call_start_byte));
    row.insert("call_branch_kind".into(), json!(call.call_branch_kind));
    row.insert("call_loop_depth".into(), json!(call.call_loop_depth));
    row.insert("call_control_frames_json".into(), json!(call.call_control_frames_json));
    row.insert("call_type".into(), json!(call.call_type));
    row.insert("call_arity".into(), json!(call.call_arity));
    row.insert("callee_name".into(), json!(call.callee_name));
    row.insert("callee_id".into(), json!(call.callee_id));
    // Writer contract: call row requires project_id. Python reference chạy qua
    // journal-shadow env để nhận cùng giá trị từ metadata.
    row.insert("project_id".into(), json!(project_id));
    row
}
