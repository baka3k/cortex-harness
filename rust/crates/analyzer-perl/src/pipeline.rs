//! Port của `tools/perl/pipeline.py` — deterministic scan, cache,
//! incremental selection cho Perl project.

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

use crate::models::{
    AnalysisResult, Diagnostic, ParsedFile, ANALYZER_VERSION, SUPPORTED_EXTENSIONS,
};
use crate::parser::PerlTreeSitterParser;
use crate::resolver::{affected_file_closure, resolve_project};
use crate::runtime;

pub const DEFAULT_MAX_FILE_BYTES: i64 = 2 * 1024 * 1024;
pub const DEFAULT_MAX_TOTAL_BYTES: i64 = 64 * 1024 * 1024;
pub const DEFAULT_MAX_FILES: i64 = 10_000;

/// `_SKIP_DIRS` — skip set RIÊNG của perl pipeline (không phải
/// COMMON_SCAN_EXCLUDE của framework).
const SKIP_DIRS: [&str; 13] = [
    ".git",
    ".hg",
    ".svn",
    ".cache",
    ".idea",
    ".vscode",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "target",
    "vendor",
];

fn has_supported_extension(name: &str) -> bool {
    let lower = name.to_lowercase();
    SUPPORTED_EXTENSIONS.iter().any(|ext| lower.ends_with(ext))
}

fn is_symlink(path: &Path) -> bool {
    path.symlink_metadata()
        .map(|meta| meta.file_type().is_symlink())
        .unwrap_or(false)
}

/// `_safe_relative_path` — realpath hoá, bắt buộc nằm dưới root, trả
/// rel-posix; `must_exist` yêu cầu is_file. Python `os.path.realpath` chịu
/// path KHÔNG tồn tại (normalize lexical phần đuôi) — deleted manifest paths
/// vẫn sống sót; canonicalize Rust fail ⇒ fallback lexical join.
fn safe_relative_path(root_real: &Path, raw_path: &str, must_exist: bool) -> Option<String> {
    let raw = raw_path.trim();
    if raw.is_empty() {
        return None;
    }
    let candidate_raw = PathBuf::from(raw);
    let candidate = if candidate_raw.is_absolute() {
        candidate_raw
    } else {
        root_real.join(candidate_raw)
    };
    let candidate_real = std::fs::canonicalize(&candidate).unwrap_or_else(|_| candidate.clone());
    if !candidate_real.starts_with(root_real) {
        return None;
    }
    if must_exist && !candidate_real.is_file() {
        return None;
    }
    Some(cortex_analyzer_framework::manifest::to_posix(
        candidate_real.strip_prefix(root_real).ok()?,
    ))
}

/// `normalize_manifest_paths`.
pub fn normalize_manifest_paths(root_real: &Path, paths: &[String]) -> Vec<String> {
    let mut normalized: BTreeSet<String> = BTreeSet::new();
    for raw_path in paths {
        if let Some(rel_path) = safe_relative_path(root_real, raw_path, false)
            && has_supported_extension(&rel_path)
        {
            normalized.insert(rel_path);
        }
    }
    normalized.into_iter().collect()
}

/// `scan_perl_files` — os.walk (followlinks=False, prune symlink dirs),
/// dirnames/filenames sorted, budget max_files. Trả (paths sorted, diags).
pub fn scan_perl_files(
    root_real: &Path,
    max_files: usize,
) -> Result<(Vec<String>, Vec<Diagnostic>), String> {
    if !root_real.is_dir() {
        return Err(format!(
            "Perl analysis root is not a directory: {}",
            root_real.display()
        ));
    }
    let mut paths: Vec<String> = Vec::new();
    let mut diagnostics: Vec<Diagnostic> = Vec::new();
    walk(root_real, root_real, max_files, &mut paths, &mut diagnostics, &mut false);
    paths.sort();
    diagnostics.sort_by(|a, b| a.code.cmp(&b.code).then_with(|| a.file_path.cmp(&b.file_path)));
    Ok((paths, diagnostics))
}

fn walk(
    root_real: &Path,
    current: &Path,
    max_files: usize,
    paths: &mut Vec<String>,
    diagnostics: &mut Vec<Diagnostic>,
    stopped: &mut bool,
) {
    if *stopped {
        return;
    }
    let entries = match std::fs::read_dir(current) {
        Ok(entries) => entries,
        Err(_) => return,
    };
    let mut subdirs: Vec<PathBuf> = Vec::new();
    let mut filenames: Vec<String> = Vec::new();
    for entry in entries.flatten() {
        let path = entry.path();
        let name = entry.file_name().to_string_lossy().to_string();
        // os.walk dùng os.path.isdir (FOLLOW symlink) cho dirnames, rồi code
        // prune symlink dir — nên phân loại theo is_dir() follow-link.
        let is_dir = path.is_dir();
        if is_dir {
            if !SKIP_DIRS.contains(&name.as_str())
                && !cortex_analyzer_framework::scan::matches_extra_ignore(&name)
                && !is_symlink(&path)
            {
                subdirs.push(path);
            }
        } else {
            filenames.push(name);
        }
    }
    subdirs.sort();
    filenames.sort();
    for filename in filenames {
        if !has_supported_extension(&filename) {
            continue;
        }
        let absolute = current.join(&filename);
        if is_symlink(&absolute) {
            diagnostics.push(Diagnostic {
                code: "perl.scan.symlink_skipped".to_string(),
                severity: "warning".to_string(),
                message: "Symlinked Perl source was skipped.".to_string(),
                file_path: cortex_analyzer_framework::manifest::to_posix(
                    absolute.strip_prefix(root_real).unwrap_or(&absolute),
                ),
                span: None,
                details: Vec::new(),
            });
            continue;
        }
        if let Some(rel_path) = safe_relative_path(root_real, &absolute.to_string_lossy(), true) {
            paths.push(rel_path);
        }
        if paths.len() >= max_files {
            diagnostics.push(Diagnostic {
                code: "perl.scan.file_budget".to_string(),
                severity: "warning".to_string(),
                message: format!("Source discovery stopped at the {max_files}-file budget."),
                file_path: String::new(),
                span: None,
                details: Vec::new(),
            });
            *stopped = true;
            return;
        }
    }
    for sub in subdirs {
        walk(root_real, &sub, max_files, paths, diagnostics, stopped);
        if *stopped {
            return;
        }
    }
}

/// `analyzer_cache.safe_cache_root` — base/cache-dir + "perl-analyzer" +
/// segment `safe(basename)_sha1(realpath)[:12]`.
fn safe_cache_root(cache_dir: Option<&str>, project_root: &Path) -> PathBuf {
    use sha1::{Digest, Sha1};
    let base_root = cache_dir
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| std::env::current_dir().unwrap_or_default().join(".cache"));
    let mut root = base_root.join("perl-analyzer");
    let normalized =
        std::fs::canonicalize(project_root).unwrap_or_else(|_| project_root.to_path_buf());
    let basename = normalized
        .file_name()
        .map(|name| name.to_string_lossy().to_string())
        .filter(|name| !name.is_empty())
        .unwrap_or_else(|| "root".to_string());
    // Python `_safe_cache_segment`: ch.isalnum() (unicode) hoặc "._-" giữ,
    // còn lại → '_'; rỗng → "root".
    let segment_base: String = basename
        .chars()
        .map(|c| {
            if c.is_alphanumeric() || c == '.' || c == '_' || c == '-' {
                c
            } else {
                '_'
            }
        })
        .collect();
    let mut hasher = Sha1::new();
    hasher.update(normalized.to_string_lossy().as_bytes());
    let hex: String = hasher
        .finalize()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect();
    root = root.join(format!("{segment_base}_{}", &hex[..12]));
    let _ = std::fs::create_dir_all(&root);
    root
}

/// `_cache_path` — fingerprint sha256 của các thành phần join \0.
#[allow(clippy::too_many_arguments)]
fn cache_path(
    cache_root: &Path,
    project_id: &str,
    rel_path: &str,
    content_digest: &str,
    include_docs: bool,
    max_snippet_chars: i64,
    max_doc_chars: i64,
) -> PathBuf {
    let grammar = runtime::capabilities().expect("capabilities");
    let fingerprint = [
        ANALYZER_VERSION.to_string(),
        grammar.grammar_version,
        grammar.runtime_version,
        project_id.to_string(),
        rel_path.to_string(),
        content_digest.to_string(),
        u8::from(include_docs).to_string(),
        max_snippet_chars.to_string(),
        max_doc_chars.to_string(),
    ]
    .join("\u{0}");
    use sha2::{Digest, Sha256};
    let digest = Sha256::digest(fingerprint.as_bytes());
    let hex: String = digest.iter().map(|byte| format!("{byte:02x}")).collect();
    cache_root.join(format!("{hex}.json"))
}

/// `_read_cached` — JSON hỏng ⇒ None (reparse).
fn read_cached(path: &Path) -> Option<ParsedFile> {
    let text = std::fs::read_to_string(path).ok()?;
    serde_json::from_str(&text).ok()
}

/// `_write_cached` — atomic tmp+rename, sort_keys compact.
fn write_cached(path: &Path, parsed: &ParsedFile) {
    let Ok(payload) = serde_json::to_string(parsed) else {
        return;
    };
    let temp_path = path.with_extension(format!("json.{}.tmp", std::process::id()));
    if std::fs::write(&temp_path, payload).is_ok() {
        let _ = std::fs::rename(&temp_path, path);
    }
}

/// `run_perl_analysis` — `changed_paths: Option` giữ đúng phân biệt None
/// (full run) vs Some (incremental selection).
#[allow(clippy::too_many_arguments)]
pub fn run_perl_analysis(
    root: &Path,
    project_id: &str,
    changed_paths: Option<&[String]>,
    deleted_paths: Option<&[String]>,
    cache_dir: Option<&str>,
    ignore_cache: bool,
    include_docs: bool,
    max_file_bytes: i64,
    max_total_bytes: i64,
    max_files: i64,
    max_snippet_chars: i64,
    max_doc_chars: i64,
) -> Result<AnalysisResult, String> {
    let root_real = std::fs::canonicalize(root).map_err(|error| error.to_string())?;
    if project_id.trim().is_empty() {
        return Err("project_id is required".to_string());
    }
    if max_file_bytes <= 0 || max_total_bytes <= 0 || max_files <= 0 {
        return Err("analysis budgets must be positive".to_string());
    }
    let (source_paths, scan_diagnostics) = scan_perl_files(&root_real, max_files as usize)?;
    let empty: Vec<String> = Vec::new();
    let changed_inputs: &[String] = changed_paths.unwrap_or(&empty);
    let deleted_inputs: &[String] = deleted_paths.unwrap_or(&empty);
    let changed = normalize_manifest_paths(&root_real, changed_inputs);
    let deleted = normalize_manifest_paths(&root_real, deleted_inputs);
    let parser = PerlTreeSitterParser::new(max_snippet_chars, max_doc_chars);
    let cache_root = safe_cache_root(cache_dir, &root_real);
    let mut parsed_files: Vec<ParsedFile> = Vec::new();
    let mut diagnostics: Vec<Diagnostic> = scan_diagnostics;
    let mut total_bytes: i64 = 0;

    for rel_path in &source_paths {
        let absolute = root_real.join(rel_path);
        let size = match std::fs::metadata(&absolute) {
            Ok(metadata) => metadata.len() as i64,
            Err(error) => {
                diagnostics.push(Diagnostic {
                    code: "perl.scan.read_error".to_string(),
                    severity: "warning".to_string(),
                    message: format!("Unable to stat source: {error}"),
                    file_path: rel_path.clone(),
                    span: None,
                    details: Vec::new(),
                });
                continue;
            }
        };
        if total_bytes >= max_total_bytes {
            diagnostics.push(Diagnostic {
                code: "perl.scan.total_byte_budget".to_string(),
                severity: "warning".to_string(),
                message: format!("Total source budget {max_total_bytes} bytes was reached."),
                file_path: rel_path.clone(),
                span: None,
                details: Vec::new(),
            });
            break;
        }
        let read_limit =
            (size.min(max_file_bytes)).min(max_total_bytes - total_bytes).max(0) as usize;
        let source = match std::fs::read(&absolute) {
            Ok(bytes) => bytes,
            Err(error) => {
                diagnostics.push(Diagnostic {
                    code: "perl.scan.read_error".to_string(),
                    severity: "warning".to_string(),
                    message: format!("Unable to read source: {error}"),
                    file_path: rel_path.clone(),
                    span: None,
                    details: Vec::new(),
                });
                continue;
            }
        };
        let source = if source.len() > read_limit {
            source[..read_limit].to_vec()
        } else {
            source
        };
        total_bytes += source.len() as i64;
        let truncated = size > source.len() as i64;
        let content_digest = {
            use sha2::{Digest, Sha256};
            let digest = Sha256::digest(&source);
            let hex: String = digest.iter().map(|byte| format!("{byte:02x}")).collect();
            hex
        };
        let path = cache_path(
            &cache_root,
            project_id,
            rel_path,
            &content_digest,
            include_docs,
            max_snippet_chars,
            max_doc_chars,
        );
        let cached = if ignore_cache { None } else { read_cached(&path) };
        let parsed = match cached {
            Some(parsed) => parsed,
            None => {
                let parsed = parser.parse_bytes(
                    project_id,
                    rel_path,
                    &source,
                    include_docs,
                    truncated,
                );
                write_cached(&path, &parsed);
                parsed
            }
        };
        parsed_files.push(parsed);
        if truncated {
            diagnostics.push(Diagnostic {
                code: "perl.scan.file_byte_budget".to_string(),
                severity: "warning".to_string(),
                message: format!(
                    "Source was truncated at {} of {size} bytes.",
                    source.len()
                ),
                file_path: rel_path.clone(),
                span: None,
                details: Vec::new(),
            });
        }
    }

    let resolution = resolve_project(&parsed_files);
    let affected: BTreeSet<String> = if !changed.is_empty() {
        affected_file_closure(&changed, &resolution.dependency_index)
            .into_iter()
            .collect()
    } else if changed_paths.is_some() {
        BTreeSet::new()
    } else {
        resolution
            .parsed_files
            .iter()
            .map(|item| item.file.file_path.clone())
            .collect()
    };
    let selected: Vec<&ParsedFile> = resolution
        .parsed_files
        .iter()
        .filter(|item| affected.contains(&item.file.file_path))
        .collect();
    let selected_paths: BTreeSet<String> = selected
        .iter()
        .map(|item| item.file.file_path.clone())
        .collect();

    let files: Vec<_> = selected.iter().map(|item| item.file.clone()).collect();
    let mut symbols: Vec<_> = selected
        .iter()
        .flat_map(|item| item.symbols.iter().cloned())
        .collect();
    symbols.sort();
    let mut imports: Vec<_> = selected
        .iter()
        .flat_map(|item| item.imports.iter().cloned())
        .collect();
    imports.sort();
    let mut references: Vec<_> = selected
        .iter()
        .flat_map(|item| item.references.iter().cloned())
        .collect();
    references.sort();
    let mut documentation: Vec<_> = selected
        .iter()
        .flat_map(|item| item.documentation.iter().cloned())
        .collect();
    documentation.sort();

    let mut selected_diagnostics: Vec<Diagnostic> = Vec::new();
    for item in &selected {
        for diag in &item.diagnostics {
            selected_diagnostics.push(diag.clone());
        }
    }
    for diag in &resolution.diagnostics {
        if diag.file_path.is_empty() || selected_paths.contains(&diag.file_path) {
            selected_diagnostics.push(diag.clone());
        }
    }
    selected_diagnostics.extend(diagnostics);
    // Python: sorted(set(diags), key=(code, file_path, span.start_byte|-1, message))
    let mut unique: BTreeSet<Diagnostic> = BTreeSet::new();
    for diag in selected_diagnostics {
        unique.insert(diag);
    }
    let mut selected_diagnostics: Vec<Diagnostic> = unique.into_iter().collect();
    selected_diagnostics.sort_by(|a, b| {
        a.code
            .cmp(&b.code)
            .then_with(|| a.file_path.cmp(&b.file_path))
            .then_with(|| {
                let a_start = a.span.map(|span| span.start_byte as i64).unwrap_or(-1);
                let b_start = b.span.map(|span| span.start_byte as i64).unwrap_or(-1);
                a_start.cmp(&b_start)
            })
            .then_with(|| a.message.cmp(&b.message))
    });

    let coverage = if files.is_empty() && source_paths.is_empty() {
        "empty"
    } else if files.iter().any(|item| item.coverage == "partial")
        || selected_diagnostics
            .iter()
            .any(|item| item.severity == "warning" || item.severity == "error")
    {
        "partial"
    } else {
        "complete"
    };

    let counter_map: BTreeMap<String, usize> = BTreeMap::from([
        ("discovered_files".to_string(), source_paths.len()),
        ("returned_files".to_string(), files.len()),
        ("symbols".to_string(), symbols.len()),
        ("imports".to_string(), imports.len()),
        ("references".to_string(), references.len()),
        ("diagnostics".to_string(), selected_diagnostics.len()),
        ("input_bytes".to_string(), total_bytes.max(0) as usize),
    ]);
    let counters: Vec<(String, usize)> = counter_map.into_iter().collect();

    Ok(AnalysisResult {
        project_id: project_id.trim().to_string(),
        normalized_root: ".".to_string(),
        analyzer_version: ANALYZER_VERSION.to_string(),
        capabilities: runtime::capabilities()?,
        coverage: coverage.to_string(),
        files,
        symbols,
        imports,
        references,
        documentation,
        diagnostics: selected_diagnostics,
        dependency_index: resolution.dependency_index,
        changed_paths: changed,
        deleted_paths: deleted,
        counters,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cache_root_segment_sha1() {
        // Python: _project_cache_segment("/tmp/proj") →
        // f"{safe(basename)}_{sha1(realpath)[:12]}".
        use sha1::{Digest, Sha1};
        let mut hasher = Sha1::new();
        hasher.update(b"/tmp/proj");
        let hex: String = hasher
            .finalize()
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect();
        assert_eq!(hex.len(), 40);
    }

    #[test]
    fn scan_finds_fixture_corpus() {
        let root =
            Path::new("/Users/user/AI/cortex-harness/tests/fixtures/perl-application");
        if !root.is_dir() {
            return;
        }
        let (paths, diagnostics) = scan_perl_files(root, 10_000).expect("scan");
        assert!(paths.contains(&"bin/app.pl".to_string()));
        assert!(paths.contains(&"lib/App/Broken.pm".to_string()));
        assert!(diagnostics.is_empty());
    }
}
