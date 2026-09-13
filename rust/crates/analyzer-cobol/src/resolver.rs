//! Copybook, program, paragraph, and data-symbol resolution — port
//! `resolver.py` (resolve_project + DependencyIndex).

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

use serde_json::json;

use crate::models::{Diagnostic, ParsedFile};

pub const DEPENDENCY_SCHEMA_VERSION: i64 = 1;

#[derive(Debug, Clone)]
pub struct ResolvedProject {
    pub files: Vec<ParsedFile>,
    pub programs: BTreeMap<String, usize>,
    pub copybooks: BTreeMap<String, usize>,
    pub include_graph: BTreeMap<String, Vec<String>>,
    pub include_closure: BTreeMap<String, Vec<String>>,
    pub diagnostics: Vec<Diagnostic>,
}

impl ResolvedProject {
    pub fn file_by_path(&self, path: &str) -> Option<&ParsedFile> {
        self.files.iter().find(|file| file.path == path)
    }

    pub fn file_by_index(&self, index: usize) -> &ParsedFile {
        &self.files[index]
    }
}

pub struct DependencyIndex {
    pub dependencies: BTreeMap<String, Vec<String>>,
}

impl DependencyIndex {
    pub fn from_resolved(project: &ResolvedProject) -> Self {
        Self {
            dependencies: project
                .include_graph
                .iter()
                .map(|(owner, targets)| {
                    (owner.clone(), targets.iter().cloned().collect::<BTreeSet<_>>().into_iter().collect())
                })
                .collect(),
        }
    }

    /// `impacted_files` — BFS trên reverse dependency graph.
    pub fn impacted_files(&self, changed: &[String], deleted: &[String]) -> BTreeSet<String> {
        let seeds: std::collections::VecDeque<String> = changed
            .iter()
            .chain(deleted.iter())
            .map(|path| path.replace('\\', "/"))
            .collect();
        let mut reverse: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
        for (owner, targets) in &self.dependencies {
            for target in targets {
                reverse.entry(target.clone()).or_default().insert(owner.clone());
            }
        }
        let mut impacted: BTreeSet<String> = seeds.iter().cloned().collect();
        let mut queue: std::collections::VecDeque<String> = seeds.iter().cloned().collect();
        // Python: queue = list(sorted(seeds)); BFS pop(0) — thứ tự chỉ ảnh
        // hưởng thứ tự thêm vào set (set kết quả như nhau).
        while let Some(target) = queue.pop_front() {
            for owner in reverse.get(&target).into_iter().flatten() {
                if impacted.insert(owner.clone()) {
                    queue.push_back(owner.clone());
                }
            }
        }
        impacted
    }

    pub fn save(&self, path: &Path) -> Result<(), String> {
        let payload = json!({
            "schema_version": DEPENDENCY_SCHEMA_VERSION,
            "dependencies": self.dependencies,
        });
        // Python json.dumps(payload, indent=2, sort_keys=True) + "\n".
        let mut text = serde_json::to_string_pretty(&payload).map_err(|e| e.to_string())?;
        if !text.ends_with('\n') {
            text.push('\n');
        }
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
        }
        std::fs::write(path, text).map_err(|e| e.to_string())
    }

    pub fn load(path: &Path) -> Result<Self, String> {
        let text = std::fs::read_to_string(path).map_err(|e| e.to_string())?;
        let payload: serde_json::Value = serde_json::from_str(&text).map_err(|e| e.to_string())?;
        if payload.get("schema_version").and_then(serde_json::Value::as_i64) != Some(DEPENDENCY_SCHEMA_VERSION) {
            return Err("unsupported COBOL dependency index schema".to_string());
        }
        let mut dependencies = BTreeMap::new();
        if let Some(values) = payload.get("dependencies").and_then(serde_json::Value::as_object) {
            for (owner, targets) in values {
                let list: Vec<String> = targets
                    .as_array()
                    .map(|items| {
                        items
                            .iter()
                            .filter_map(|item| item.as_str().map(str::to_string))
                            .collect::<BTreeSet<_>>()
                            .into_iter()
                            .collect()
                    })
                    .unwrap_or_default();
                dependencies.insert(owner.clone(), list);
            }
        }
        Ok(Self { dependencies })
    }
}

fn extension_order(copybook_extensions: &[String]) -> BTreeMap<String, usize> {
    copybook_extensions
        .iter()
        .enumerate()
        .map(|(index, value)| {
            let normalized = if let Some(stripped) = value.strip_prefix('.') {
                format!(".{}", stripped.to_lowercase())
            } else {
                format!(".{value}").to_lowercase()
            };
            (normalized, index)
        })
        .collect()
}

fn suffix_of(path: &str) -> String {
    PathBuf::from(path)
        .extension()
        .map(|ext| format!(".{}", ext.to_string_lossy().to_lowercase()))
        .unwrap_or_default()
}

/// `resolve_project` — files sắp theo path, map program/copybook (alias gồm
/// stem và full name), include graph và closure với cycle diagnostic, và
/// file-binding partial diagnostics.
pub fn resolve_project(
    files: Vec<ParsedFile>,
    copybook_extensions: &[String],
) -> ResolvedProject {
    let mut ordered = files;
    ordered.sort_by(|a, b| a.path.cmp(&b.path));

    let mut programs: BTreeMap<String, usize> = BTreeMap::new();
    let mut copybook_candidates: BTreeMap<String, Vec<usize>> = BTreeMap::new();
    let mut diagnostics: Vec<Diagnostic> = Vec::new();

    for (index, source) in ordered.iter().enumerate() {
        let mut seen_paragraphs: BTreeSet<String> = BTreeSet::new();
        for paragraph in &source.paragraphs {
            if !seen_paragraphs.insert(paragraph.name.clone()) {
                diagnostics.push(
                    Diagnostic::new(
                        "COBOL_DUPLICATE_PARAGRAPH",
                        format!(
                            "paragraph {} is defined more than once in {}",
                            paragraph.name, source.path
                        ),
                        "warning",
                    )
                    .with_evidence(paragraph.evidence.clone()),
                );
            }
        }
        if !source.program_name.is_empty() {
            if let Some(existing) = programs.get(&source.program_name) {
                let evidence = ordered[*existing]
                    .paragraphs
                    .first()
                    .map(|p| p.evidence.clone());
                diagnostics.push(
                    Diagnostic::new(
                        "COBOL_DUPLICATE_PROGRAM",
                        format!(
                            "program {} is also defined in {}",
                            source.program_name, ordered[*existing].path
                        ),
                        "warning",
                    )
                    .with_evidence_opt(evidence),
                );
            } else {
                programs.insert(source.program_name.clone(), index);
            }
        }
        if source.is_copybook {
            let stem = PathBuf::from(&source.path)
                .file_stem()
                .map(|s| s.to_string_lossy().to_uppercase())
                .unwrap_or_default();
            let full_name = PathBuf::from(&source.path)
                .file_name()
                .map(|s| s.to_string_lossy().to_uppercase())
                .unwrap_or_default();
            for alias in [stem, full_name] {
                copybook_candidates.entry(alias).or_default().push(index);
            }
        }
    }

    let extension_rank = extension_order(copybook_extensions);
    let mut copybooks: BTreeMap<String, usize> = BTreeMap::new();
    for (alias, candidates) in &copybook_candidates {
        let mut ranked = candidates.clone();
        ranked.sort_by(|a, b| {
            let key = |index: &usize| {
                (
                    extension_rank.get(&suffix_of(&ordered[*index].path)).copied().unwrap_or(extension_rank.len()),
                    &ordered[*index].path,
                )
            };
            key(a).cmp(&key(b))
        });
        copybooks.insert(alias.clone(), ranked[0]);
        if ranked.len() > 1 && !alias.contains('.') {
            diagnostics.push(
                Diagnostic::new(
                    "COBOL_COPYBOOK_AMBIGUOUS",
                    format!(
                        "copybook {alias} matched multiple files; selected {}",
                        ordered[ranked[0]].path
                    ),
                    "warning",
                )
                .with_details(json!({
                    "candidates": ranked.iter().map(|index| ordered[*index].path.clone()).collect::<Vec<_>>()
                })),
            );
        }
    }

    let mut include_graph: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for source in &ordered {
        let mut targets: Vec<String> = Vec::new();
        for include in &source.copies {
            match copybooks.get(&include.name.to_uppercase()) {
                Some(target_index) => targets.push(ordered[*target_index].path.clone()),
                None => diagnostics.push(
                    Diagnostic::new(
                        "COBOL_COPYBOOK_NOT_FOUND",
                        format!("copybook {} was not found", include.name),
                        "warning",
                    )
                    .with_evidence(include.evidence.clone())
                    .with_details(json!({"copybook": include.name})),
                ),
            }
        }
        include_graph.insert(source.path.clone(), dedupe(targets));
    }

    // Closure DFS với cycle detection (Python closure/visit đệ quy).
    let mut closure: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for source in &ordered {
        let mut discovered: Vec<String> = Vec::new();
        visit(
            &include_graph,
            &ordered,
            &source.path,
            vec![source.path.clone()],
            &mut discovered,
            &mut diagnostics,
        );
        closure.insert(source.path.clone(), discovered);
    }

    for source in &ordered {
        for binding in &source.file_bindings {
            if binding.assignment.is_empty() || !binding.has_description {
                diagnostics.push(
                    Diagnostic::new(
                        "COBOL_FILE_BINDING_PARTIAL",
                        format!(
                            "file {} is missing {}",
                            binding.name,
                            if binding.assignment.is_empty() { "assignment" } else { "FD/SD description" }
                        ),
                        "warning",
                    )
                    .with_evidence(binding.evidence.clone()),
                );
            }
        }
    }

    ResolvedProject {
        files: ordered,
        programs,
        copybooks,
        include_graph,
        include_closure: closure,
        diagnostics,
    }
}

fn dedupe(values: Vec<String>) -> Vec<String> {
    let mut seen = BTreeSet::new();
    values
        .into_iter()
        .filter(|value| seen.insert(value.clone()))
        .collect()
}

fn visit(
    include_graph: &BTreeMap<String, Vec<String>>,
    ordered: &[ParsedFile],
    owner: &str,
    chain: Vec<String>,
    discovered: &mut Vec<String>,
    diagnostics: &mut Vec<Diagnostic>,
) {
    for target in include_graph.get(owner).into_iter().flatten() {
        if chain.contains(target) {
            let include_evidence = ordered
                .iter()
                .find(|item| item.path == owner)
                .and_then(|item| {
                    item.copies
                        .iter()
                        .find(|include| stem_upper(&target.clone()) == include.name.to_uppercase())
                        .map(|include| include.evidence.clone())
                });
            let mut full_chain = chain.clone();
            full_chain.push(target.clone());
            diagnostics.push(
                Diagnostic::new(
                    "COBOL_COPYBOOK_CYCLE",
                    format!("copybook cycle: {}", full_chain.join(" -> ")),
                    "warning",
                )
                .with_evidence_opt(include_evidence)
                .with_details(json!({ "chain": full_chain })),
            );
            continue;
        }
        if !discovered.contains(target) {
            discovered.push(target.clone());
            let mut next_chain = chain.clone();
            next_chain.push(target.clone());
            visit(include_graph, ordered, target, next_chain, discovered, diagnostics);
        }
    }
}

fn stem_upper(path: &str) -> String {
    PathBuf::from(path)
        .file_stem()
        .map(|s| s.to_string_lossy().to_uppercase())
        .unwrap_or_default()
}
