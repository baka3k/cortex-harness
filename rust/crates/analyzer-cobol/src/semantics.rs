//! Normalize resolved COBOL facts into the graph contract — port
//! `semantics.py` (build_semantic_facts). Thứ tự add_node/add_edge giữ nguyên
//! để `len(edges)` trong edge id byte-exact.

use std::collections::{BTreeMap, BTreeSet};

use fancy_regex::Regex;
use serde_json::{Map, Value, json};

use crate::cfg::build_cfg;
use crate::models::{Diagnostic, SemanticEdge, SemanticNode, SourceEvidence, stable_id};
use crate::resolver::ResolvedProject;

pub struct FactsContext {
    pub project_id: String,
    nodes: Vec<SemanticNode>,
    edges: Vec<SemanticEdge>,
    diagnostics: Vec<Diagnostic>,
    node_ids: BTreeSet<String>,
    owner_ids: BTreeMap<String, String>,
    data_ids: BTreeMap<(String, String), String>,
    file_binding_ids: BTreeMap<(String, String), String>,
}

pub fn build_semantic_facts(
    project: &ResolvedProject,
    project_id: &str,
) -> (Vec<SemanticNode>, Vec<SemanticEdge>, Vec<Diagnostic>) {
    let mut context = FactsContext {
        project_id: project_id.to_string(),
        nodes: Vec::new(),
        edges: Vec::new(),
        diagnostics: project.diagnostics.clone(),
        node_ids: BTreeSet::new(),
        owner_ids: BTreeMap::new(),
        data_ids: BTreeMap::new(),
        file_binding_ids: BTreeMap::new(),
    };

    // Reserve mọi cross-file owner identity trước khi emit relationships.
    for source in &project.files {
        let owner_id = if source.is_copybook {
            stable_id(project_id, "CobolCopybook", &[&source.path])
        } else {
            stable_id(project_id, "CobolProgram", &[&source.path, &source.program_name])
        };
        context.owner_ids.insert(source.path.clone(), owner_id);
    }

    for source_index in 0..project.files.len() {
        let source = &project.files[source_index];
        let first_evidence = if let Some(paragraph) = source.paragraphs.first() {
            paragraph.evidence.clone()
        } else if let Some(item) = source.data_items.first() {
            item.evidence.clone()
        } else if let Some(copy) = source.copies.first() {
            copy.evidence.clone()
        } else {
            SourceEvidence::new(source.path.clone(), 1)
        };
        let file_name = std::path::Path::new(&source.path)
            .file_name()
            .map(|n| n.to_string_lossy().to_string())
            .unwrap_or_default();
        let file_id = context.add_node(
            "File",
            &file_name,
            &source.path,
            &first_evidence,
            &[&source.path],
            |properties| {
                properties.insert("source_format".into(), Value::String(source.source_format.clone()));
                properties.insert("dialect".into(), Value::String(source.dialect.clone()));
                properties.insert("encoding".into(), Value::String(source.encoding.clone()));
            },
        );
        let (owner_name, owner_id) = if source.is_copybook {
            let owner_name = std::path::Path::new(&source.path)
                .file_stem()
                .map(|n| n.to_string_lossy().to_uppercase())
                .unwrap_or_default();
            let owner_id = context.add_node(
                "CobolCopybook",
                &owner_name,
                &source.path,
                &first_evidence,
                &[&source.path],
                |properties| {
                    properties.insert("qualified_name".into(), Value::String(owner_name.clone()));
                },
            );
            (owner_name, owner_id)
        } else {
            let owner_name = source.program_name.clone();
            let owner_id = context.add_node(
                "CobolProgram",
                &owner_name,
                &source.path,
                &first_evidence,
                &[&source.path, &owner_name],
                |properties| {
                    properties.insert("qualified_name".into(), Value::String(owner_name.clone()));
                },
            );
            (owner_name, owner_id)
        };
        if context.owner_ids.get(&source.path).map(String::as_str) != Some(owner_id.as_str()) {
            panic!("reserved COBOL owner identity drifted during normalization");
        }
        context.add_edge(&file_id, &owner_id, "DEFINES", &first_evidence, 1.0, false, Map::new());

        let mut section_ids: BTreeMap<String, String> = BTreeMap::new();
        for name in &source.sections {
            let evidence = source
                .paragraphs
                .iter()
                .find(|paragraph| &paragraph.section == name)
                .map(|paragraph| paragraph.evidence.clone())
                .unwrap_or_else(|| first_evidence.clone());
            let qualified_name = format!("{owner_name}.{name}");
            let section_id = context.add_node(
                "CobolSection",
                name,
                &source.path,
                &evidence,
                &[&source.path, &owner_name, name],
                |properties| {
                    properties.insert("qualified_name".into(), Value::String(qualified_name.clone()));
                },
            );
            section_ids.insert(name.clone(), section_id.clone());
            context.add_edge(&owner_id, &section_id, "DEFINES", &evidence, 1.0, false, Map::new());
        }

        let mut paragraph_ids: BTreeMap<String, String> = BTreeMap::new();
        for paragraph in &source.paragraphs {
            let qualified_name = format!("{owner_name}.{}", paragraph.name);
            let code = paragraph
                .statements
                .iter()
                .map(|statement| statement.text.clone())
                .collect::<Vec<_>>()
                .join("\n");
            let paragraph_id = context.add_node(
                "CobolParagraph",
                &paragraph.name,
                &source.path,
                &paragraph.evidence,
                &[&source.path, &owner_name, &paragraph.name],
                |properties| {
                    properties.insert("qualified_name".into(), Value::String(qualified_name.clone()));
                    properties.insert("ordinal".into(), Value::Number(paragraph.ordinal.into()));
                    properties.insert("section".into(), Value::String(paragraph.section.clone()));
                    properties.insert("code".into(), Value::String(code.clone()));
                },
            );
            paragraph_ids.insert(paragraph.name.clone(), paragraph_id.clone());
            let from = section_ids
                .get(&paragraph.section)
                .cloned()
                .unwrap_or_else(|| owner_id.clone());
            context.add_edge(&from, &paragraph_id, "DEFINES", &paragraph.evidence, 1.0, false, Map::new());
        }

        for item in &source.data_items {
            let qualified_name = format!("{owner_name}.{}.{}", item.storage, item.name);
            let item_id = context.add_node(
                "CobolDataItem",
                &item.name,
                &source.path,
                &item.evidence,
                &[&source.path, &item.storage, &item.level, &item.name, &item.evidence.start_line],
                |properties| {
                    properties.insert("qualified_name".into(), Value::String(qualified_name.clone()));
                    properties.insert("level".into(), Value::Number(item.level.into()));
                    properties.insert("storage".into(), Value::String(item.storage.clone()));
                    properties.insert("picture".into(), Value::String(item.picture.clone()));
                    properties.insert("usage".into(), Value::String(item.usage.clone()));
                    properties.insert("value".into(), Value::String(item.value.clone()));
                    properties.insert("redefines".into(), Value::String(item.redefines.clone()));
                    properties.insert("occurs".into(), Value::String(item.occurs.clone()));
                },
            );
            context
                .data_ids
                .insert((source.path.clone(), item.name.clone()), item_id.clone());
            context.add_edge(&owner_id, &item_id, "DEFINES", &item.evidence, 1.0, false, Map::new());
        }

        for binding in &source.file_bindings {
            let qualified_name = format!("{owner_name}.{}", binding.name);
            let binding_id = context.add_node(
                "CobolFile",
                &binding.name,
                &source.path,
                &binding.evidence,
                &[&source.path, &binding.name],
                |properties| {
                    properties.insert("qualified_name".into(), Value::String(qualified_name.clone()));
                    properties.insert("assignment".into(), Value::String(binding.assignment.clone()));
                    properties.insert("has_description".into(), Value::Bool(binding.has_description));
                },
            );
            context
                .file_binding_ids
                .insert((source.path.clone(), binding.name.clone()), binding_id.clone());
            context.add_edge(&owner_id, &binding_id, "DEFINES", &binding.evidence, 1.0, false, Map::new());
        }

        let (cfg_edges, cfg_diagnostics) =
            build_cfg(source, project_id, &owner_id, &paragraph_ids);
        context.edges.extend(cfg_edges);
        context.diagnostics.extend(cfg_diagnostics);

        // symbol_candidates: name → [ids] (thứ tự data_items).
        let mut symbol_candidates: BTreeMap<String, Vec<String>> = BTreeMap::new();
        for item in &source.data_items {
            symbol_candidates.entry(item.name.clone()).or_default().push(stable_id(
                project_id,
                "CobolDataItem",
                &[
                    &source.path,
                    &item.storage,
                    &item.level,
                    &item.name,
                    &item.evidence.start_line,
                ],
            ));
        }
        if let Some(closure) = project.include_closure.get(&source.path) {
            for imported_path in closure {
                let Some(imported) = project.file_by_path(imported_path) else {
                    continue;
                };
                for item in &imported.data_items {
                    let target_id = stable_id(
                        project_id,
                        "CobolDataItem",
                        &[
                            &imported.path,
                            &item.storage,
                            &item.level,
                            &item.name,
                            &item.evidence.start_line,
                        ],
                    );
                    symbol_candidates
                        .entry(item.name.clone())
                        .or_default()
                        .push(target_id.clone());
                    let mut props = Map::new();
                    props.insert("imported".into(), Value::Bool(true));
                    props.insert("copybook_path".into(), Value::String(imported.path.clone()));
                    context.add_edge(
                        &owner_id,
                        &target_id,
                        "REFERENCES",
                        &item.evidence,
                        1.0,
                        false,
                        props,
                    );
                }
            }
        }

        for paragraph in &source.paragraphs {
            let Some(paragraph_id) = paragraph_ids.get(&paragraph.name).cloned() else {
                continue;
            };
            for statement in &paragraph.statements {
                match statement.kind.as_str() {
                    "call" => {
                        let target = statement
                            .properties
                            .get("target")
                            .and_then(Value::as_str)
                            .unwrap_or_default()
                            .to_uppercase();
                        let literal = statement
                            .properties
                            .get("literal")
                            .and_then(Value::as_bool)
                            .unwrap_or(false);
                        let resolved = if literal {
                            project.programs.get(&target).copied()
                        } else {
                            None
                        };
                        match resolved {
                            Some(program_index) => {
                                let owner_path = project.files[program_index].path.clone();
                                let target_owner = context.owner_ids.get(&owner_path).cloned();
                                if let Some(target_owner) = target_owner {
                                    let mut props = Map::new();
                                    props.insert("target_name".into(), Value::String(target.clone()));
                                    context.add_edge(
                                        &paragraph_id,
                                        &target_owner,
                                        "CALLS",
                                        &statement.evidence,
                                        statement.confidence,
                                        false,
                                        props,
                                    );
                                }
                            }
                            None => {
                                let code = if !literal {
                                    "COBOL_DYNAMIC_CALL"
                                } else {
                                    "COBOL_CALL_TARGET_UNRESOLVED"
                                };
                                let message = if !literal {
                                    format!("call target {target} is dynamic")
                                } else {
                                    format!("call target {target} is not defined in this project")
                                };
                                context.diagnostics.push(
                                    Diagnostic::new(code, message, "warning")
                                        .with_evidence(statement.evidence.clone())
                                        .with_details(json!({"target": target})),
                                );
                            }
                        }
                    }
                    "io" => {
                        let target = statement
                            .properties
                            .get("target")
                            .and_then(Value::as_str)
                            .unwrap_or_default()
                            .to_uppercase();
                        let operation = statement
                            .properties
                            .get("operation")
                            .and_then(Value::as_str)
                            .unwrap_or_default()
                            .to_string();
                        let mode = statement
                            .properties
                            .get("mode")
                            .and_then(Value::as_str)
                            .unwrap_or_default()
                            .to_string();
                        let mut target_id = context
                            .file_binding_ids
                            .get(&(source.path.clone(), target.clone()))
                            .cloned();
                        if target_id.is_none() && matches!(operation.as_str(), "WRITE" | "REWRITE") {
                            let file_records: BTreeSet<String> = source
                                .data_items
                                .iter()
                                .filter(|item| item.storage == "FILE")
                                .map(|item| item.name.clone())
                                .collect();
                            let bindings: Vec<String> = context
                                .file_binding_ids
                                .iter()
                                .filter(|((path, _), _)| path == &source.path)
                                .map(|(_, value)| value.clone())
                                .collect();
                            if file_records.contains(&target) && bindings.len() == 1 {
                                target_id = bindings.into_iter().next();
                            }
                        }
                        match target_id {
                            Some(target_id) => {
                                let relationships: Vec<&str> = if operation == "OPEN"
                                    && matches!(mode.as_str(), "OUTPUT" | "EXTEND")
                                {
                                    vec!["WRITES"]
                                } else if operation == "OPEN" && mode == "I-O" {
                                    vec!["READS", "WRITES"]
                                } else if matches!(operation.as_str(), "OPEN" | "READ" | "START" | "CLOSE") {
                                    vec!["READS"]
                                } else {
                                    vec!["WRITES"]
                                };
                                for relationship in relationships {
                                    let mut props = Map::new();
                                    props.insert("operation".into(), Value::String(operation.clone()));
                                    props.insert("mode".into(), Value::String(mode.clone()));
                                    context.add_edge(
                                        &paragraph_id,
                                        &target_id,
                                        relationship,
                                        &statement.evidence,
                                        1.0,
                                        false,
                                        props,
                                    );
                                }
                            }
                            None => context.diagnostics.push(
                                Diagnostic::new(
                                    "COBOL_FILE_TARGET_UNRESOLVED",
                                    format!("file target {target} was not found"),
                                    "warning",
                                )
                                .with_evidence(statement.evidence.clone()),
                            ),
                        }
                    }
                    "sql" | "cics" => {
                        let label = if statement.kind == "sql" {
                            "CobolSqlStatement"
                        } else {
                            "CobolCicsCommand"
                        };
                        let default_name = statement.kind.to_uppercase();
                        let name = statement
                            .properties
                            .get("operation")
                            .and_then(Value::as_str)
                            .filter(|value| !value.is_empty())
                            .unwrap_or(default_name.as_str())
                            .to_string();
                        let statement_id = context.add_node(
                            label,
                            &name,
                            &source.path,
                            &statement.evidence,
                            &[&source.path, &statement.kind, &statement.evidence.start_line, &statement.text],
                            |properties| {
                                properties.insert("operation".into(), Value::String(name.clone()));
                                properties.insert("raw_text".into(), Value::String(statement.text.clone()));
                                let targets = statement
                                    .properties
                                    .get("targets")
                                    .or_else(|| statement.properties.get("resources"))
                                    .cloned()
                                    .unwrap_or_else(|| Value::Array(vec![]));
                                properties.insert("targets".into(), targets);
                                let hosts = statement
                                    .properties
                                    .get("host_variables")
                                    .cloned()
                                    .unwrap_or_else(|| Value::Array(vec![]));
                                properties.insert("host_variables".into(), hosts);
                            },
                        );
                        context.add_edge(
                            &paragraph_id,
                            &statement_id,
                            "DEFINES",
                            &statement.evidence,
                            statement.confidence,
                            false,
                            Map::new(),
                        );
                    }
                    _ => {}
                }

                // Words → REFERENCES edges (chạy cho MỌI statement kind).
                let words: Vec<String> = match statement.properties.get("words").and_then(Value::as_array) {
                    Some(array) if !array.is_empty() => array
                        .iter()
                        .map(|value| value.as_str().unwrap_or_default().to_string())
                        .collect(),
                    _ => findall_name_words(&statement.text.to_uppercase()),
                };
                let mut deduped: Vec<String> = Vec::new();
                for word in words {
                    if !deduped.contains(&word) {
                        deduped.push(word);
                    }
                }
                for name in deduped {
                    let candidates: Vec<String> = symbol_candidates
                        .get(&name)
                        .map(|items| {
                            let mut unique: Vec<String> = Vec::new();
                            for item in items {
                                if !unique.contains(item) {
                                    unique.push(item.clone());
                                }
                            }
                            unique
                        })
                        .unwrap_or_default();
                    if candidates.len() == 1 {
                        let mut props = Map::new();
                        props.insert("statement_kind".into(), Value::String(statement.kind.clone()));
                        props.insert(
                            "access".into(),
                            Value::String(data_access_text(&statement.text, &name)),
                        );
                        context.add_edge(
                            &paragraph_id,
                            &candidates[0],
                            "REFERENCES",
                            &statement.evidence,
                            statement.confidence,
                            false,
                            props,
                        );
                    } else if candidates.len() > 1 {
                        context.diagnostics.push(
                            Diagnostic::new(
                                "COBOL_SYMBOL_AMBIGUOUS",
                                format!(
                                    "unqualified data reference {name} matches {} definitions",
                                    candidates.len()
                                ),
                                "warning",
                            )
                            .with_evidence(statement.evidence.clone())
                            .with_details(json!({"name": name, "candidate_ids": candidates})),
                        );
                    }
                }
            }
        }
    }

    // INCLUDES edges — project.include_graph đã sort theo owner.
    for (owner_path, target_paths) in &project.include_graph {
        let Some(owner_id) = context.owner_ids.get(owner_path).cloned() else {
            continue;
        };
        let Some(owner_file) = project.file_by_path(owner_path) else {
            continue;
        };
        let mut include_by_target: BTreeMap<String, crate::models::ParsedCopy> = BTreeMap::new();
        for include in &owner_file.copies {
            for copybook_index in project.copybooks.values() {
                let target = &project.files[*copybook_index];
                if stem_upper(&target.path) == include.name.to_uppercase() {
                    include_by_target.insert(target.path.clone(), include.clone());
                }
            }
        }
        for target_path in target_paths {
            let target_id = context.owner_ids.get(target_path).cloned();
            let include = include_by_target.get(target_path).cloned();
            if let (Some(target_id), Some(include)) = (target_id, include) {
                let mut props = Map::new();
                props.insert("replacing".into(), Value::String(include.replacing.clone()));
                context.add_edge(
                    &owner_id,
                    &target_id,
                    "INCLUDES",
                    &include.evidence,
                    1.0,
                    false,
                    props,
                );
            }
        }
    }

    (context.nodes, context.edges, context.diagnostics)
}

fn stem_upper(path: &str) -> String {
    std::path::Path::new(path)
        .file_stem()
        .map(|n| n.to_string_lossy().to_uppercase())
        .unwrap_or_default()
}

/// `re.findall(r"[A-Z0-9][A-Z0-9-]*", text)`.
fn findall_name_words(text: &str) -> Vec<String> {
    static WORDS: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    let regex = WORDS.get_or_init(|| Regex::new(r"[A-Z0-9][A-Z0-9-]*").expect("regex"));
    regex
        .captures_iter(text)
        .flatten()
        .filter_map(|captures| captures.get(0).map(|m| m.as_str().to_string()))
        .collect()
}

/// `_data_access` — regex được compile inline (data_access giữ nguyên logic).
fn data_access_text(statement_text: &str, name: &str) -> String {
    let upper = statement_text.to_uppercase().split_whitespace().collect::<Vec<_>>().join(" ");
    let escaped = fancy_regex::escape(name);
    // escaped thay đổi theo name ⇒ compile mỗi lần (fixtures nhỏ, giữ parity).
    let read_write = Regex::new(&format!(
        r"\b(?:ADD|SUBTRACT|MULTIPLY|DIVIDE)\b.*\b(?:TO|FROM|GIVING)\s+{escaped}\b"
    ))
    .expect("regex");
    if read_write.is_match(&upper).unwrap_or(false) {
        return "read_write".to_string();
    }
    let write = Regex::new(&format!(
        r"\b(?:MOVE\b.*\bTO|COMPUTE|ACCEPT|INITIALIZE|SET|STRING\b.*\bINTO|UNSTRING\b.*\bINTO)\s+{escaped}\b"
    ))
    .expect("regex");
    if write.is_match(&upper).unwrap_or(false) {
        return "write".to_string();
    }
    "read".to_string()
}

impl FactsContext {
    #[allow(clippy::too_many_arguments)]
    fn add_node(
        &mut self,
        label: &str,
        name: &str,
        file_path: &str,
        evidence: &SourceEvidence,
        identity: &[&dyn std::fmt::Display],
        decorate: impl FnOnce(&mut Map<String, Value>),
    ) -> String {
        let node_id = stable_id(&self.project_id, label, identity);
        if !self.node_ids.contains(&node_id) {
            let mut properties = Map::new();
            properties.insert(
                "project_id".into(),
                Value::String(self.project_id.clone()),
            );
            properties.insert("language".into(), Value::String("cobol".to_string()));
            properties.insert("name".into(), Value::String(name.to_string()));
            properties.insert("qualified_name".into(), Value::String(name.to_string()));
            properties.insert("file_path".into(), Value::String(file_path.to_string()));
            properties.insert("path".into(), Value::String(file_path.to_string()));
            properties.insert("start_line".into(), Value::Number(evidence.start_line.into()));
            properties.insert("end_line".into(), Value::Number(evidence.end_line.into()));
            decorate(&mut properties);
            self.nodes.push(SemanticNode {
                id: node_id.clone(),
                label: label.to_string(),
                name: name.to_string(),
                file_path: file_path.to_string(),
                evidence: evidence.clone(),
                properties: Value::Object(properties),
                confidence: 1.0,
            });
            self.node_ids.insert(node_id.clone());
        }
        node_id
    }

    #[allow(clippy::too_many_arguments)]
    fn add_edge(
        &mut self,
        source_id: &str,
        target_id: &str,
        relationship: &str,
        evidence: &SourceEvidence,
        confidence: f64,
        dynamic: bool,
        properties: Map<String, Value>,
    ) {
        let id = stable_id(
            &self.project_id,
            "edge",
            &[
                &relationship,
                &source_id,
                &target_id,
                &evidence.file,
                &evidence.start_line,
                &self.edges.len(),
            ],
        );
        let mut props = Map::new();
        props.insert(
            "project_id".into(),
            Value::String(self.project_id.clone()),
        );
        props.extend(properties);
        self.edges.push(SemanticEdge {
            id,
            source_id: source_id.to_string(),
            target_id: target_id.to_string(),
            relationship: relationship.to_string(),
            evidence: evidence.clone(),
            properties: Value::Object(props),
            confidence,
            dynamic,
        });
    }
}
