//! Port phần graph của `tools/shell/shell_analyzer.py`: `build_graph_rows`,
//! `_verified_lineage_rows`, `_write_graph` (write_nodes_batch + typed rels).

use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

use serde_json::{json, Map, Value};

use cortex_graph_writer::language_writer::LanguageCodeWriter;
use cortex_graph_writer::store::GraphStore;

use crate::models::{ProgramMapping, ShellAnalysisResult};

pub type Row = Map<String, Value>;

fn program_node_id(project_id: &str, program_id: &str) -> String {
    format!("batch-program::{project_id}:{program_id}")
}

fn common_props(result: &ShellAnalysisResult, project_name: &str, repo: &str) -> Row {
    let mut row = Row::new();
    row.insert("project_id".into(), json!(result.project_id));
    row.insert("project_name".into(), json!(project_name));
    row.insert("language".into(), json!("shell"));
    row.insert("repo".into(), json!(repo));
    row.insert("build_system".into(), json!("shell"));
    row
}

pub struct GraphRows {
    pub scripts: Vec<Row>,
    pub functions: Vec<Row>,
    pub invocations: Vec<Row>,
    pub programs: Vec<Row>,
    pub files: Vec<Row>,
    pub relations: Vec<Row>,
}

/// `build_graph_rows` — thứ tự build khớp Python (per file: functions →
/// invocations → file.relations).
pub fn build_graph_rows(
    result: &ShellAnalysisResult,
    project_name: &str,
    repo: &str,
    program_mappings: &[ProgramMapping],
) -> GraphRows {
    let common = common_props(result, project_name, repo);
    let mut scripts: Vec<Row> = Vec::new();
    let mut functions: Vec<Row> = Vec::new();
    let mut invocations: Vec<Row> = Vec::new();
    let mut programs: BTreeMap<String, Row> = BTreeMap::new();
    let mut referenced_files: BTreeMap<String, Row> = BTreeMap::new();
    let mut relations: Vec<Row> = Vec::new();
    let mut implementation_keys: BTreeSet<(String, String)> = BTreeSet::new();
    let mappings: BTreeMap<&str, &ProgramMapping> = program_mappings
        .iter()
        .map(|mapping| (mapping.program_id.as_str(), mapping))
        .collect();

    for file in &result.files {
        scripts.push(merged_row(
            &[
                ("id", json!(file.file_path)),
                (
                    "name",
                    json!(Path::new(&file.file_path)
                        .file_name()
                        .map(|n| n.to_string_lossy().to_string())
                        .unwrap_or_default()),
                ),
                ("file_path", json!(file.file_path)),
                ("encoding", json!(file.encoding)),
            ],
            &common,
        ));
        for function in &file.functions {
            functions.push(merged_row(
                &[
                    ("id", json!(function.symbol_id)),
                    ("name", json!(function.name)),
                    ("file_path", json!(function.file_path)),
                    ("start_line", json!(function.start_line)),
                    ("end_line", json!(function.end_line)),
                    ("code", json!(function.code)),
                ],
                &common,
            ));
            relations.push(relation_row(
                &file.file_path,
                "ShellScript",
                &function.symbol_id,
                "ShellFunction",
                "CONTAINS",
                Some(&result.project_id),
                Row::new(),
            ));
        }
        for invocation in &file.invocations {
            let mapping = mappings.get(invocation.command_name.as_str());
            let program_id_node = mapping
                .map(|mapping| program_node_id(&result.project_id, &mapping.program_id))
                .unwrap_or_default();
            invocations.push(merged_row(
                &[
                    ("id", json!(invocation.symbol_id)),
                    (
                        "name",
                        json!(if invocation.command_name.is_empty() {
                            invocation.raw_command.clone()
                        } else {
                            invocation.command_name.clone()
                        }),
                    ),
                    ("command_name", json!(invocation.command_name)),
                    ("raw_command", json!(invocation.raw_command)),
                    ("file_path", json!(invocation.file_path)),
                    ("line", json!(invocation.line)),
                    ("ordinal", json!(invocation.ordinal)),
                    ("dynamic", json!(invocation.dynamic)),
                    (
                        "resolution_status",
                        json!(if mapping.is_some() {
                            "mapped_candidate"
                        } else {
                            "unresolved"
                        }),
                    ),
                ],
                &common,
            ));
            relations.push(relation_row(
                &invocation.source_id,
                &invocation.source_label,
                &invocation.symbol_id,
                "ShellInvocation",
                "HAS_INVOCATION",
                Some(&result.project_id),
                {
                    let mut props = Row::new();
                    props.insert("line".into(), json!(invocation.line));
                    props.insert("raw_command".into(), json!(invocation.raw_command));
                    props
                },
            ));
            let Some(mapping) = mapping else { continue };
            if !programs.contains_key(&program_id_node) {
                programs.insert(
                    program_id_node.clone(),
                    merged_row(
                        &[
                            ("id", json!(program_id_node)),
                            ("name", json!(mapping.program_id)),
                            ("program_id", json!(mapping.program_id)),
                            ("source_path", json!(mapping.source_path)),
                            ("evidence_hash", json!(mapping.evidence_hash)),
                        ],
                        &common,
                    ),
                );
            }
            relations.push(relation_row(
                &invocation.symbol_id,
                "ShellInvocation",
                &program_id_node,
                "BatchProgram",
                "RESOLVES_TO",
                Some(&result.project_id),
                {
                    let mut props = Row::new();
                    props.insert("evidence_hash".into(), json!(mapping.evidence_hash));
                    props
                },
            ));
            let implementation_key = (program_id_node.clone(), mapping.source_path.clone());
            if implementation_keys.insert(implementation_key) {
                relations.push(relation_row(
                    &program_id_node,
                    "BatchProgram",
                    &mapping.source_path,
                    "File",
                    "IMPLEMENTED_BY",
                    Some(&result.project_id),
                    {
                        let mut props = Row::new();
                        props.insert("evidence_hash".into(), json!(mapping.evidence_hash));
                        props
                    },
                ));
            }
        }
        for relation in &file.relations {
            relations.push(relation_row(
                &relation.source_id,
                &relation.source_label,
                &relation.target_id,
                &relation.target_label,
                &relation.rel_type,
                Some(&result.project_id),
                {
                    let mut props = Row::new();
                    props.insert("line".into(), json!(relation.line));
                    props.insert("raw_target".into(), json!(relation.raw_target));
                    props.insert("resolved".into(), json!(relation.resolved));
                    props
                },
            ));
            if relation.target_label == "File" && relation.resolved {
                referenced_files.entry(relation.target_id.clone()).or_insert_with(|| {
                    merged_row(
                        &[
                            ("id", json!(relation.target_id)),
                            (
                                "name",
                                json!(Path::new(&relation.target_id)
                                    .file_name()
                                    .map(|n| n.to_string_lossy().to_string())
                                    .unwrap_or_default()),
                            ),
                            ("file_path", json!(relation.target_id)),
                        ],
                        &common,
                    )
                });
            }
        }
    }

    GraphRows {
        scripts,
        functions,
        invocations,
        programs: programs.into_values().collect(),
        files: referenced_files.into_values().collect(),
        relations,
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

#[allow(clippy::too_many_arguments)]
fn relation_row(
    source_id: &str,
    source_label: &str,
    target_id: &str,
    target_label: &str,
    rel_type: &str,
    project_id: Option<&str>,
    properties: Row,
) -> Row {
    let mut row = Row::new();
    row.insert("source_id".into(), json!(source_id));
    row.insert("source_label".into(), json!(source_label));
    row.insert("target_id".into(), json!(target_id));
    row.insert("target_label".into(), json!(target_label));
    row.insert("rel_type".into(), json!(rel_type));
    if let Some(project_id) = project_id {
        row.insert("project_id".into(), json!(project_id));
    }
    row.insert("properties".into(), Value::Object(properties));
    row
}

/// `_verified_lineage_rows` — verify File đích IMPLEMENTED_BY tồn tại trong
/// graph trước khi ghi lineage; cập nhật resolution_status invocations.
pub fn verified_lineage_rows(
    store: &mut dyn GraphStore,
    mut rows: GraphRows,
    project_id: &str,
) -> Result<(GraphRows, usize), String> {
    let implementation_relations: Vec<&Row> = rows
        .relations
        .iter()
        .filter(|relation| relation.get("rel_type").and_then(Value::as_str) == Some("IMPLEMENTED_BY"))
        .collect();
    let file_ids: Vec<String> = implementation_relations
        .iter()
        .filter_map(|relation| relation.get("target_id").and_then(Value::as_str))
        .map(str::to_string)
        .collect();
    let materialized = materialized_file_ids(store, project_id, &file_ids)?;

    let verified_programs: BTreeSet<String> = implementation_relations
        .iter()
        .filter_map(|relation| {
            let target = relation.get("target_id").and_then(Value::as_str)?;
            if materialized.contains(target) {
                relation.get("source_id").and_then(Value::as_str).map(str::to_string)
            } else {
                None
            }
        })
        .collect();

    let mut filtered_relations: Vec<Row> = Vec::new();
    let mut skipped = 0usize;
    for relation in rows.relations {
        let rel_type = relation.get("rel_type").and_then(Value::as_str).unwrap_or("");
        let source = relation.get("source_id").and_then(Value::as_str).unwrap_or("");
        let target = relation.get("target_id").and_then(Value::as_str).unwrap_or("");
        if rel_type == "IMPLEMENTED_BY" && !verified_programs.contains(source) {
            skipped += 1;
            continue;
        }
        if rel_type == "RESOLVES_TO" && !verified_programs.contains(target) {
            skipped += 1;
            continue;
        }
        filtered_relations.push(relation);
    }
    let verified_invocation_ids: BTreeSet<String> = filtered_relations
        .iter()
        .filter(|relation| relation.get("rel_type").and_then(Value::as_str) == Some("RESOLVES_TO"))
        .filter_map(|relation| relation.get("source_id").and_then(Value::as_str))
        .map(str::to_string)
        .collect();

    for invocation in &mut rows.invocations {
        let status = invocation.get("resolution_status").and_then(Value::as_str);
        if status == Some("mapped_candidate") {
            let new_status = if verified_invocation_ids.contains(
                invocation.get("id").and_then(Value::as_str).unwrap_or(""),
            ) {
                "verified"
            } else {
                "mapped_source_missing"
            };
            invocation.insert("resolution_status".into(), json!(new_status));
        }
    }
    rows.programs
        .retain(|program| verified_programs.contains(program.get("id").and_then(Value::as_str).unwrap_or("")));
    rows.relations = filtered_relations;
    Ok((rows, skipped))
}

/// `_materialized_file_ids` — File ids có thật trong graph (theo project scope).
fn materialized_file_ids(
    store: &mut dyn GraphStore,
    project_id: &str,
    file_ids: &[String],
) -> Result<BTreeSet<String>, String> {
    let mut candidates: BTreeSet<String> = file_ids.iter().cloned().collect();
    if candidates.is_empty() {
        return Ok(BTreeSet::new());
    }
    let query = "MATCH (f:File) \
         WHERE f.project_id_normalized = $project_id_normalized \
         AND f.id IN $file_ids \
         RETURN f.id AS id";
    let mut params = BTreeMap::new();
    params.insert("project_id".to_string(), json!(project_id));
    params.insert(
        "project_id_normalized".to_string(),
        json!(cortex_graph_writer::project_scope::project_id_lookup_key(Some(project_id))),
    );
    params.insert(
        "file_ids".to_string(),
        json!(candidates.iter().cloned().collect::<Vec<_>>()),
    );
    let records = store.execute_query(query, &params, None).map_err(|e| e.to_string())?;
    let _ = &mut candidates;
    Ok(records
        .iter()
        .filter_map(|record| record.get("id").and_then(Value::as_str))
        .map(str::to_string)
        .collect())
}

#[allow(dead_code)]
pub struct WriteCounts {
    pub labels: BTreeMap<String, usize>,
    pub relations: usize,
    pub unresolved_relations: usize,
}

/// `_write_graph` body sau khi driver mở: rows → lineage → cleanup → writes.
pub fn write_graph(
    writer: &mut LanguageCodeWriter,
    result: &ShellAnalysisResult,
    program_mappings: &[ProgramMapping],
    project_name: &str,
    repo: &str,
    incremental: bool,
) -> Result<WriteCounts, String> {
    writer.ensure_schema().map_err(|e| e.to_string())?;
    let rows = build_graph_rows(result, project_name, repo, program_mappings);
    let (rows, missing_lineage_count) = {
        let store = writer.store.as_mut();
        verified_lineage_rows(store, rows, &result.project_id)?
    };
    let cleanup_paths: BTreeSet<String> = result
        .changed_paths
        .iter()
        .cloned()
        .chain(result.deleted_paths.iter().cloned())
        .collect();
    if incremental && !cleanup_paths.is_empty() {
        let sorted: Vec<String> = cleanup_paths.into_iter().collect();
        let store = writer.store.as_mut();
        cortex_analyzer_framework::cleanup::cleanup_graph_files(store, &result.project_id, &sorted)
            .map_err(|e| e.to_string())?;
    }
    let script_query = "UNWIND $rows AS row MERGE (n:ShellScript {id: row.id}) SET n += row";
    let function_query = "UNWIND $rows AS row MERGE (n:ShellFunction {id: row.id}) SET n += row";
    let invocation_query =
        "UNWIND $rows AS row MERGE (n:ShellInvocation {id: row.id}) SET n += row";
    let program_query = "UNWIND $rows AS row MERGE (n:BatchProgram {id: row.id}) SET n += row";
    let file_query = "UNWIND $rows AS row MERGE (n:File {id: row.id}) SET n += row";
    let mut labels: BTreeMap<String, usize> = BTreeMap::new();
    labels.insert(
        "ShellScript".into(),
        writer.write_nodes_batch("shell:scripts", script_query, &rows.scripts)
            .map_err(|e| e.to_string())?,
    );
    labels.insert(
        "ShellFunction".into(),
        writer.write_nodes_batch("shell:functions", function_query, &rows.functions)
            .map_err(|e| e.to_string())?,
    );
    labels.insert(
        "ShellInvocation".into(),
        writer.write_nodes_batch("shell:invocations", invocation_query, &rows.invocations)
            .map_err(|e| e.to_string())?,
    );
    labels.insert(
        "BatchProgram".into(),
        writer.write_nodes_batch("shell:programs", program_query, &rows.programs)
            .map_err(|e| e.to_string())?,
    );
    labels.insert(
        "File".into(),
        writer.write_nodes_batch("shell:referenced-files", file_query, &rows.files)
            .map_err(|e| e.to_string())?,
    );

    let required_relations: Vec<Row> = rows
        .relations
        .iter()
        .filter(|relation| {
            relation
                .get("properties")
                .and_then(Value::as_object)
                .and_then(|props| props.get("resolved"))
                .and_then(Value::as_bool)
                != Some(false)
        })
        .cloned()
        .collect();
    let unresolved_count =
        rows.relations.len() - required_relations.len() + missing_lineage_count;
    let relations = writer
        .write_relations_typed(&required_relations, Some(&result.project_id))
        .map_err(|e| e.to_string())?;
    Ok(WriteCounts {
        labels,
        relations,
        unresolved_relations: unresolved_count,
    })
}
