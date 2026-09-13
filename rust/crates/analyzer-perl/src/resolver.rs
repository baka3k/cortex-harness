//! Port của `tools/perl/resolver.py` — conservative project-local reference
//! và dependency resolution.

use std::collections::{BTreeMap, BTreeSet, VecDeque};

use crate::models::{DependencyIndex, Diagnostic, ImportRecord, ParsedFile, ReferenceRecord, SymbolRecord};

const PERL_BUILTINS: [&str; 36] = [
    "bless",
    "caller",
    "chdir",
    "close",
    "defined",
    "delete",
    "die",
    "do",
    "each",
    "eval",
    "exists",
    "grep",
    "join",
    "keys",
    "map",
    "open",
    "pop",
    "print",
    "printf",
    "push",
    "ref",
    "require",
    "return",
    "scalar",
    "shift",
    "sort",
    "split",
    "sprintf",
    "substr",
    "undef",
    "unshift",
    "values",
    "warn",
    "__FILE__", "__LINE__", "__PACKAGE__",
];

#[derive(Clone, Debug)]
pub struct ResolutionResult {
    pub parsed_files: Vec<ParsedFile>,
    pub dependency_index: DependencyIndex,
    pub diagnostics: Vec<Diagnostic>,
}

/// `_module_path_candidates` — "App::Util" → "App/Util.pm", match exact hoặc
/// suffix "/App/Util.pm"; sorted.
fn module_path_candidates(module: &str, file_paths: &BTreeSet<String>) -> Vec<String> {
    let suffix = format!("{}.pm", module.replace("::", "/"));
    let mut candidates: Vec<String> = file_paths
        .iter()
        .filter(|path| *path == &suffix || path.ends_with(&format!("/{suffix}")))
        .cloned()
        .collect();
    candidates.sort();
    candidates
}

/// `_current_package` — package của owner symbol, hoặc suy từ source_name.
fn current_package(
    reference: &ReferenceRecord,
    symbols_by_id: &BTreeMap<String, SymbolRecord>,
) -> String {
    if let Some(owner) = symbols_by_id.get(&reference.source_symbol_id) {
        return owner.package.clone();
    }
    if reference.source_name.contains("::") {
        return reference
            .source_name
            .rsplit_once("::")
            .map(|(prefix, _)| prefix.to_string())
            .unwrap_or_else(|| reference.source_name.clone());
    }
    if reference.source_name.is_empty() {
        "main".to_string()
    } else {
        reference.source_name.clone()
    }
}

/// Python `{value!r}` trên str — repr với nháy đơn (escape tối thiểu; tên
/// module/target thực tế là identifier ASCII).
fn py_repr(value: &str) -> String {
    let mut out = String::with_capacity(value.len() + 2);
    out.push('\'');
    for c in value.chars() {
        match c {
            '\'' => out.push_str("\\'"),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 || c == '\x7f' => {
                out.push_str(&format!("\\x{:02x}", c as u32));
            }
            c => out.push(c),
        }
    }
    out.push('\'');
    out
}

fn diagnostic(code: &str, message: &str, reference: &ReferenceRecord) -> Diagnostic {
    Diagnostic {
        code: code.to_string(),
        severity: "info".to_string(),
        message: message.to_string(),
        file_path: reference.file_path.clone(),
        span: Some(reference.span),
        details: vec![("target".to_string(), reference.target_name.clone())],
    }
}

/// `resolve_project` — chỉ resolve exact, unambiguous project-local refs.
pub fn resolve_project(parsed_files: &[ParsedFile]) -> ResolutionResult {
    let mut ordered: Vec<&ParsedFile> = parsed_files.iter().collect();
    ordered.sort_by(|a, b| a.file.file_path.cmp(&b.file.file_path));
    let file_paths: BTreeSet<String> = ordered
        .iter()
        .map(|item| item.file.file_path.clone())
        .collect();
    let mut symbols_by_id: BTreeMap<String, SymbolRecord> = BTreeMap::new();
    let mut subroutines_by_fq: BTreeMap<String, Vec<SymbolRecord>> = BTreeMap::new();
    let mut package_files: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    for item in &ordered {
        for symbol in &item.symbols {
            symbols_by_id.insert(symbol.symbol_id.clone(), symbol.clone());
            if symbol.kind == "subroutine" {
                subroutines_by_fq
                    .entry(symbol.fq_name.clone())
                    .or_default()
                    .push(symbol.clone());
            } else if symbol.kind == "package" {
                package_files
                    .entry(symbol.fq_name.clone())
                    .or_default()
                    .insert(item.file.file_path.clone());
            }
        }
    }

    let mut forward: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    let mut diagnostics: Vec<Diagnostic> = Vec::new();
    let mut updated_files: Vec<ParsedFile> = Vec::new();

    for parsed in ordered {
        let mut resolved_imports: Vec<ImportRecord> = Vec::new();
        let mut imported_modules: Vec<String> = Vec::new();
        for item in &parsed.imports {
            if item.is_dynamic || item.module.is_empty() {
                resolved_imports.push(item.clone());
                continue;
            }
            let mut candidates: BTreeSet<String> = package_files
                .get(&item.module)
                .cloned()
                .unwrap_or_default();
            for candidate in module_path_candidates(&item.module, &file_paths) {
                candidates.insert(candidate);
            }
            if candidates.len() == 1 {
                let target_path = candidates.iter().next().unwrap().clone();
                forward.entry(item.file_path.clone()).or_default().insert(target_path.clone());
                imported_modules.push(item.module.clone());
                let mut resolved = item.clone();
                resolved.resolved_path = target_path;
                resolved_imports.push(resolved);
            } else {
                let code = if !candidates.is_empty() {
                    "perl.import.ambiguous"
                } else {
                    "perl.import.missing"
                };
                let message = if !candidates.is_empty() {
                    format!(
                        "Static module {} has {} project-local candidates.",
                        py_repr(&item.module),
                        candidates.len()
                    )
                } else {
                    format!(
                        "Static module {} has no project-local file.",
                        py_repr(&item.module)
                    )
                };
                diagnostics.push(Diagnostic {
                    code: code.to_string(),
                    severity: "info".to_string(),
                    message,
                    file_path: item.file_path.clone(),
                    span: Some(item.span),
                    details: Vec::new(),
                });
                resolved_imports.push(item.clone());
            }
        }

        let mut updated_references: Vec<ReferenceRecord> = Vec::new();
        for reference in &parsed.references {
            if reference.kind != "direct" && reference.kind != "qualified" {
                updated_references.push(reference.clone());
                continue;
            }
            let target = reference.target_name.trim_start_matches('&').to_string();
            if PERL_BUILTINS.contains(&target.as_str()) {
                let mut updated = reference.clone();
                updated.resolution_status = "builtin".to_string();
                updated.confidence = 1.0;
                updated.reason = "recognized Perl builtin".to_string();
                updated_references.push(updated);
                continue;
            }
            if target.starts_with("SUPER::") {
                let mut updated = reference.clone();
                updated.reason = "SUPER dispatch requires runtime inheritance state".to_string();
                updated_references.push(updated);
                continue;
            }

            let package = current_package(reference, &symbols_by_id);
            let mut candidate_names: Vec<String> = Vec::new();
            if target.contains("::") {
                candidate_names.push(target.clone());
            } else {
                candidate_names.push(format!("{package}::{target}"));
                for module in &imported_modules {
                    candidate_names.push(format!("{module}::{target}"));
                }
            }

            let mut candidates: Vec<SymbolRecord> = Vec::new();
            let mut seen_ids: BTreeSet<String> = BTreeSet::new();
            for (position, name) in candidate_names.iter().enumerate() {
                if let Some(symbols) = subroutines_by_fq.get(name) {
                    for symbol in symbols {
                        if seen_ids.insert(symbol.symbol_id.clone()) {
                            candidates.push(symbol.clone());
                        }
                    }
                }
                if !candidates.is_empty() && position == 0 {
                    break;
                }
            }

            if candidates.len() == 1 {
                let destination = &candidates[0];
                forward
                    .entry(reference.file_path.clone())
                    .or_default()
                    .insert(destination.file_path.clone());
                let mut updated = reference.clone();
                updated.resolution_status = "resolved".to_string();
                updated.target_symbol_id = destination.symbol_id.clone();
                updated.confidence = if reference.kind == "qualified" { 1.0 } else { 0.95 };
                updated.reason = "unique project-local subroutine".to_string();
                updated_references.push(updated);
            } else if candidates.len() > 1 {
                let mut updated = reference.clone();
                updated.resolution_status = "ambiguous".to_string();
                updated.confidence = reference.confidence.min(0.4);
                updated.reason = format!("{} project-local candidates", candidates.len());
                updated_references.push(updated);
                diagnostics.push(diagnostic(
                    "perl.reference.ambiguous",
                    &format!(
                        "Reference {} has multiple project-local candidates.",
                        py_repr(&reference.target_name)
                    ),
                    reference,
                ));
            } else {
                updated_references.push(reference.clone());
                diagnostics.push(diagnostic(
                    "perl.reference.unresolved",
                    &format!(
                        "Reference {} was left unresolved.",
                        py_repr(&reference.target_name)
                    ),
                    reference,
                ));
            }
        }

        let mut updated = parsed.clone();
        resolved_imports.sort();
        updated.imports = resolved_imports;
        updated_references.sort();
        updated.references = updated_references;
        updated_files.push(updated);
    }

    for path in &file_paths {
        forward.entry(path.clone()).or_default();
    }
    let mut reverse: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    for (source_path, targets) in &forward {
        for target_path in targets {
            reverse.entry(target_path.clone()).or_default().insert(source_path.clone());
        }
    }
    for path in &file_paths {
        reverse.entry(path.clone()).or_default();
    }
    diagnostics.sort();
    diagnostics.dedup();
    ResolutionResult {
        parsed_files: updated_files,
        dependency_index: DependencyIndex::from_mappings(&forward, &reverse),
        diagnostics,
    }
}

/// `affected_file_closure` — mở rộng changed qua reverse dependencies.
pub fn affected_file_closure(
    changed_paths: &[String],
    dependency_index: &DependencyIndex,
) -> Vec<String> {
    let reverse = dependency_index
        .reverse
        .iter()
        .map(|(key, values)| (key.clone(), values.clone()))
        .collect::<BTreeMap<String, Vec<String>>>();
    let mut seen: BTreeSet<String> = changed_paths
        .iter()
        .filter(|path| !path.is_empty())
        .map(|path| path.replace('\\', "/"))
        .collect();
    let mut queue: VecDeque<String> = seen.iter().cloned().collect();
    while let Some(current) = queue.pop_front() {
        if let Some(dependents) = reverse.get(&current) {
            for dependent in dependents {
                if !seen.contains(dependent) {
                    seen.insert(dependent.clone());
                    queue.push_back(dependent.clone());
                }
            }
        }
    }
    seen.into_iter().collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn module_path_candidates_matches_suffix() {
        let paths: BTreeSet<String> = ["lib/App/Util.pm", "App/Util.pm", "other.pm"]
            .iter()
            .map(|s| s.to_string())
            .collect();
        let candidates = module_path_candidates("App::Util", &paths);
        assert_eq!(candidates, vec!["App/Util.pm", "lib/App/Util.pm"]);
    }

    #[test]
    fn closure_walks_reverse_edges() {
        let forward: BTreeMap<String, BTreeSet<String>> = BTreeMap::from([
            ("a.pm".to_string(), BTreeSet::from(["b.pm".to_string()])),
            ("b.pm".to_string(), BTreeSet::new()),
        ]);
        let index = DependencyIndex::from_mappings(&forward, &{
            let mut reverse: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
            reverse.insert("b.pm".to_string(), BTreeSet::from(["a.pm".to_string()]));
            reverse.insert("a.pm".to_string(), BTreeSet::new());
            reverse
        });
        let closure = affected_file_closure(&["b.pm".to_string()], &index);
        assert_eq!(closure, vec!["a.pm".to_string(), "b.pm".to_string()]);
    }
}
