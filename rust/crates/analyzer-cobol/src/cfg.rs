//! Paragraph-level COBOL control-flow graph construction — port `cfg.py`.

use std::collections::BTreeMap;

use serde_json::{Map, Value};

use crate::models::{Diagnostic, ParsedFile, ParsedStatement, SemanticEdge, SourceEvidence, stable_id};

/// `build_cfg` — PERFORMS/PERFORMS_THRU + RETURNS, GOES_TO(_DYNAMIC), ALTERS,
/// CONDITIONAL, EXITS, FALLS_THROUGH. Edge id dùng chiều dài LOCAL edges list
/// (khác add_edge toàn cục của semantics.py — đúng như Python).
pub fn build_cfg(
    source: &ParsedFile,
    project_id: &str,
    program_id: &str,
    paragraph_ids: &BTreeMap<String, String>,
) -> (Vec<SemanticEdge>, Vec<Diagnostic>) {
    let mut edges: Vec<SemanticEdge> = Vec::new();
    let mut diagnostics: Vec<Diagnostic> = Vec::new();
    let paragraphs = &source.paragraphs;

    // `add` closure Python với statement có .evidence/.confidence và **props;
    // FALLS_THROUGH dùng pseudo statement (evidence/confidence 1.0).
    let add = |edges: &mut Vec<SemanticEdge>,
                   source_id: &str,
                   target_id: &str,
                   relationship: &str,
                   evidence: &SourceEvidence,
                   confidence: f64,
                   dynamic: bool,
                   properties: Map<String, Value>| {
        let id = stable_id(
            project_id,
            "edge",
            &[
                &relationship,
                &source_id,
                &target_id,
                &evidence.start_line,
                &edges.len(),
            ],
        );
        let mut props = Map::new();
        props.insert("project_id".into(), Value::String(project_id.to_string()));
        props.extend(properties);
        edges.push(SemanticEdge {
            id,
            source_id: source_id.to_string(),
            target_id: target_id.to_string(),
            relationship: relationship.to_string(),
            evidence: evidence.clone(),
            properties: Value::Object(props),
            confidence: confidence.min(if dynamic { 0.7 } else { 1.0 }),
            dynamic,
        });
    };

    for (index, paragraph) in paragraphs.iter().enumerate() {
        let Some(source_id) = paragraph_ids.get(&paragraph.name) else {
            continue;
        };
        let source_id = source_id.clone();
        let mut terminated = false;
        for statement in &paragraph.statements {
            match statement.kind.as_str() {
                "perform" => {
                    let target = statement
                        .properties
                        .get("target")
                        .and_then(Value::as_str)
                        .unwrap_or_default()
                        .to_string();
                    let through = statement
                        .properties
                        .get("through")
                        .and_then(Value::as_str)
                        .unwrap_or_default()
                        .to_string();
                    let target_id = paragraph_ids.get(&target).cloned();
                    if let Some(target_id) = target_id {
                        let mut props = Map::new();
                        props.insert("through".into(), Value::String(through.clone()));
                        props.insert(
                            "loop".into(),
                            statement
                                .properties
                                .get("loop")
                                .cloned()
                                .unwrap_or_else(|| Value::String(String::new())),
                        );
                        let relationship = if !through.is_empty() {
                            "PERFORMS_THRU"
                        } else {
                            "PERFORMS"
                        };
                        add(
                            &mut edges,
                            &source_id,
                            &target_id,
                            relationship,
                            &statement.evidence,
                            statement.confidence,
                            false,
                            props,
                        );
                        if !through.is_empty() {
                            let end_id = paragraph_ids.get(&through).cloned();
                            match end_id {
                                Some(end_id) => {
                                    let mut props = Map::new();
                                    props.insert("caller".into(), Value::String(paragraph.name.clone()));
                                    props.insert(
                                        "continuation_line".into(),
                                        Value::Number(statement.evidence.end_line.into()),
                                    );
                                    add(
                                        &mut edges,
                                        &end_id,
                                        &source_id,
                                        "RETURNS",
                                        &statement.evidence,
                                        statement.confidence,
                                        false,
                                        props,
                                    );
                                }
                                None => diagnostics.push(
                                    Diagnostic::new(
                                        "COBOL_PERFORM_THRU_UNRESOLVED",
                                        format!("PERFORM THRU end {through} was not found"),
                                        "warning",
                                    )
                                    .with_evidence(statement.evidence.clone()),
                                ),
                            }
                        } else {
                            let mut props = Map::new();
                            props.insert("caller".into(), Value::String(paragraph.name.clone()));
                            props.insert(
                                "continuation_line".into(),
                                Value::Number(statement.evidence.end_line.into()),
                            );
                            add(
                                &mut edges,
                                &target_id,
                                &source_id,
                                "RETURNS",
                                &statement.evidence,
                                statement.confidence,
                                false,
                                props,
                            );
                        }
                    } else {
                        diagnostics.push(
                            Diagnostic::new(
                                "COBOL_PERFORM_TARGET_UNRESOLVED",
                                format!("PERFORM target {target} was not found"),
                                "warning",
                            )
                            .with_evidence(statement.evidence.clone()),
                        );
                    }
                }
                "goto" => {
                    let dynamic = statement
                        .properties
                        .get("dynamic")
                        .and_then(Value::as_bool)
                        .unwrap_or(false);
                    if let Some(targets) = statement.properties.get("targets").and_then(Value::as_array) {
                        for target in targets {
                            let target = target.as_str().unwrap_or_default();
                            let target_id = paragraph_ids.get(target).cloned();
                            match target_id {
                                Some(target_id) => {
                                    let mut props = Map::new();
                                    props.insert("dynamic".into(), Value::Bool(dynamic));
                                    props.insert(
                                        "selector".into(),
                                        statement
                                            .properties
                                            .get("selector")
                                            .cloned()
                                            .unwrap_or_else(|| Value::String(String::new())),
                                    );
                                    add(
                                        &mut edges,
                                        &source_id,
                                        &target_id,
                                        if dynamic { "GOES_TO_DYNAMIC" } else { "GOES_TO" },
                                        &statement.evidence,
                                        statement.confidence,
                                        dynamic,
                                        props,
                                    );
                                }
                                None => diagnostics.push(
                                    Diagnostic::new(
                                        "COBOL_GOTO_TARGET_UNRESOLVED",
                                        format!("GO TO target {target} was not found"),
                                        "warning",
                                    )
                                    .with_evidence(statement.evidence.clone()),
                                ),
                            }
                        }
                    }
                    terminated = !dynamic;
                }
                "alter" => {
                    let target = statement
                        .properties
                        .get("target")
                        .and_then(Value::as_str)
                        .unwrap_or_default()
                        .to_string();
                    let target_id = paragraph_ids.get(&target).cloned();
                    match target_id {
                        Some(target_id) => {
                            let mut props = Map::new();
                            props.insert("dynamic".into(), Value::Bool(true));
                            props.insert(
                                "altered_source".into(),
                                statement
                                    .properties
                                    .get("source")
                                    .cloned()
                                    .unwrap_or_else(|| Value::String(String::new())),
                            );
                            add(
                                &mut edges,
                                &source_id,
                                &target_id,
                                "ALTERS",
                                &statement.evidence,
                                statement.confidence,
                                true,
                                props,
                            );
                        }
                        None => diagnostics.push(
                            Diagnostic::new(
                                "COBOL_ALTER_TARGET_UNRESOLVED",
                                format!("ALTER target {target} was not found"),
                                "warning",
                            )
                            .with_evidence(statement.evidence.clone()),
                        ),
                    }
                }
                "conditional" => {
                    if index + 1 < paragraphs.len() {
                        let next_name = paragraphs[index + 1].name.clone();
                        if let Some(target_id) = paragraph_ids.get(&next_name) {
                            let branch = statement
                                .text
                                .split_whitespace()
                                .next()
                                .unwrap_or_default()
                                .to_uppercase();
                            let mut props = Map::new();
                            props.insert("branch".into(), Value::String(branch));
                            add(
                                &mut edges,
                                &source_id,
                                target_id,
                                "CONDITIONAL",
                                &statement.evidence,
                                statement.confidence,
                                false,
                                props,
                            );
                        }
                    }
                }
                "exit" => {
                    let terminal = statement
                        .properties
                        .get("terminal")
                        .and_then(Value::as_bool)
                        .unwrap_or(false);
                    let mut props = Map::new();
                    props.insert("terminal".into(), Value::Bool(terminal));
                    add(
                        &mut edges,
                        &source_id,
                        program_id,
                        "EXITS",
                        &statement.evidence,
                        statement.confidence,
                        false,
                        props,
                    );
                    terminated = terminal;
                }
                _ => {}
            }
        }
        if index + 1 < paragraphs.len() && !terminated {
            // Python pseudo statement: evidence = statements[-1] hoặc paragraph,
            // confidence = 1.0.
            let evidence = paragraphs[index]
                .statements
                .last()
                .map(|statement: &ParsedStatement| statement.evidence.clone())
                .unwrap_or_else(|| paragraphs[index].evidence.clone());
            let next_name = paragraphs[index + 1].name.clone();
            if let Some(target_id) = paragraph_ids.get(&next_name) {
                add(
                    &mut edges,
                    &source_id,
                    target_id,
                    "FALLS_THROUGH",
                    &evidence,
                    1.0,
                    false,
                    Map::new(),
                );
            }
        }
    }
    (edges, diagnostics)
}
