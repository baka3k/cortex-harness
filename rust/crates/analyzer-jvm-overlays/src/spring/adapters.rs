//! Port `tools/spring/adapters.py` — Java/Kotlin language source facts.

use regex::Regex;
use super::models::LanguageSourceFact;
use crate::pyutil::read_limited;
use std::path::Path;

fn package_re() -> &'static Regex {
    static RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?m)^\s*package\s+([A-Za-z_][\w.]*)(?:\s*;)?").unwrap())
}

fn java_decl_re() -> &'static Regex {
    static RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| Regex::new(r"\b(?:class|interface|enum|record)\s+([A-Za-z_]\w*)").unwrap())
}

fn kotlin_decl_re() -> &'static Regex {
    static RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| Regex::new(r"\b(?:class|interface|object|data\s+class|enum\s+class)\s+([A-Za-z_]\w*)").unwrap())
}

fn annotation_re() -> &'static Regex {
    static RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| Regex::new(r"@([A-Za-z_][\w.]*)").unwrap())
}

struct LanguageAdapter {
    language: &'static str,
    extensions: &'static [&'static str],
    declaration_pattern: &'static Regex,
}

fn adapters_for_languages(languages: &[&str]) -> Vec<LanguageAdapter> {
    let mut adapters: Vec<LanguageAdapter> = Vec::new();
    if languages.contains(&"java") {
        adapters.push(LanguageAdapter {
            language: "java",
            extensions: &[".java"],
            declaration_pattern: java_decl_re(),
        });
    }
    if languages.contains(&"kotlin") {
        adapters.push(LanguageAdapter {
            language: "kotlin",
            extensions: &[".kt", ".kts"],
            declaration_pattern: kotlin_decl_re(),
        });
    }
    adapters
}

pub fn collect_language_facts(root: &Path, languages: &[&str], rel_paths: &[String]) -> Vec<LanguageSourceFact> {
    let mut facts: Vec<LanguageSourceFact> = Vec::new();
    for adapter in adapters_for_languages(languages) {
        let mut sorted: Vec<&String> = rel_paths.iter().collect();
        sorted.sort();
        sorted.dedup();
        for rel_path in sorted {
            let lower = rel_path.to_lowercase();
            if !adapter.extensions.iter().any(|ext| lower.ends_with(ext)) {
                continue;
            }
            let text = read_limited(&root.join(rel_path), 512 * 1024);
            let package = package_re()
                .captures(&text)
                .and_then(|c| c.get(1))
                .map(|m| m.as_str().to_string())
                .unwrap_or_default();
            let declarations: Vec<String> = adapter
                .declaration_pattern
                .captures_iter(&text)
                .filter_map(|c| c.get(1))
                .map(|m| m.as_str().to_string())
                .collect();
            let mut annotations: Vec<String> = annotation_re()
                .captures_iter(&text)
                .filter_map(|c| c.get(1))
                .map(|m| m.as_str().to_string())
                .collect();
            annotations.sort();
            annotations.dedup();
            facts.push(LanguageSourceFact {
                language: adapter.language.to_string(),
                file_path: rel_path.clone(),
                source_symbol_id: format!("{}::file::{}", adapter.language, rel_path),
                package_name: package,
                declarations,
                annotations,
                parser_status: "base_adapter_pending".to_string(),
            });
        }
    }
    facts
}
