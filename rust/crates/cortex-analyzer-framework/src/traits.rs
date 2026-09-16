//! `Analyzer` trait + context — khung mọi language analyzer điền vào.

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

use cortex_graph_writer::store::GraphStore;

use crate::cli::AnalyzerArgs;
use crate::embedding_artifact::{self, EmbeddingEmission};
use crate::manifest::load_manifest_paths;
use crate::scan::scan_files;

/// Kết quả run của 1 analyzer — feed vào summary JSON + `[SCAN_RESULT]` line.
#[derive(Debug, Clone, Default)]
pub struct AnalyzerResult {
    pub files_scanned: usize,
    pub functions: usize,
    pub classes: usize,
    pub relations: usize,
    pub calls_resolved: usize,
    /// Counts theo label từ writer (projects/files/functions/...).
    pub written: BTreeMapCounts,
    pub duration_seconds: f64,
    /// Summary JSON đã build (được main ghi ra `--summary-path` khi có).
    pub summary_json: Option<serde_json::Value>,
    /// Phase-04 (plan `260916-1520-legacy17-graphless-emit`): Trait-based
    /// analyzers (python/php/ts) populate this with
    /// `WriteAllPayload::embedding_categories()`. The framework-side
    /// `emit_embedding_artifact` helper reads it AFTER `run()` returns —
    /// works in BOTH graph + graphless modes (orchestrator's
    /// `finish_native_embedding_pass` needs the file even when store is None).
    pub embedding_categories: Vec<EmbeddingCategory>,
}

/// `EmbeddingCategory` alias — re-exported from `embedding_artifact` để tránh
/// duplicate type definitions.
pub type EmbeddingCategory = embedding_artifact::EmbeddingCategory;

/// BTreeMap alias để tránh generic noise trong AnalyzerResult.
pub type BTreeMapCounts = std::collections::BTreeMap<String, i64>;

/// Context truyền vào `Analyzer::run` — store, args, manifests, project scope.
/// `store: None` khi `CORTEX_DISABLE_GRAPH` (parse-only, khớp Python).
pub struct AnalyzerContext<'a> {
    pub args: &'a AnalyzerArgs,
    pub store: Option<Box<dyn GraphStore>>,
    pub root: PathBuf,
    pub project_id: String,
    pub project_name: String,
    pub language: String,
    pub repo: String,
    pub build_system: String,
    pub commit_sha: String,
    pub commit_sha_before: String,
    pub changed_files: BTreeSet<String>,
    pub deleted_files: BTreeSet<String>,
    pub verbose: bool,
}

impl<'a> AnalyzerContext<'a> {
    /// Dựng context từ args: mở store (trừ khi graphless), load manifests
    /// (khi incremental) — in `[diff]` line như Python main.
    pub fn from_args(args: &'a AnalyzerArgs) -> Result<Self, String> {
        let store = if args.graph_writes_disabled() {
            None
        } else {
            Some(args.open_store().map_err(|e| e.to_string())?)
        };
        let root = crate::cli::abs_root(&args.root);
        let mut changed_files = BTreeSet::new();
        let mut deleted_files = BTreeSet::new();
        if args.incremental {
            if let Some(manifest) = &args.changed_files_manifest {
                changed_files = load_manifest_paths(manifest, &root);
            }
            if let Some(manifest) = &args.deleted_files_manifest {
                deleted_files = load_manifest_paths(manifest, &root);
            }
            if args.verbose {
                println!(
                    "[diff] incremental manifests changed={} deleted={}",
                    changed_files.len(),
                    deleted_files.len()
                );
            }
        }
        Ok(Self {
            args,
            store,
            root,
            project_id: args.project_id_or_root(),
            project_name: args.project_name(),
            language: args.language_or_default(),
            repo: args.repo_or_root(),
            build_system: args.build_system(),
            commit_sha: args.commit_sha_after.clone(),
            commit_sha_before: args.commit_sha_before.clone(),
            changed_files,
            deleted_files,
            verbose: args.verbose,
        })
    }

    /// Scan file theo extension (posix-sorted như `_scan_python_files`).
    pub fn scan_files(&self, extensions: &[&str]) -> Vec<PathBuf> {
        scan_files(&self.root, extensions)
    }

    /// Đường dẫn relative-posix của 1 file dưới root.
    pub fn rel_posix(&self, path: &Path) -> String {
        crate::scan::rel_posix(&self.root, path)
    }

    /// Phase-04 emission seam (plan `260916-1520-legacy17-graphless-emit`):
    /// emit EmbeddingInputArtifact từ caller-supplied categories nếu
    /// orchestrator yêu cầu. Helper dùng cho Trait-based analyzers
    /// (python/php/ts) — emission lives ở framework, analyzer chỉ populate
    /// `result.embedding_categories` rồi framework gọi helper này sau
    /// khi `run()` returns.
    pub fn emit_embedding_artifact(
        &self,
        parser: &str,
        result_embedding_categories: Vec<EmbeddingCategory>,
    ) -> Result<(), String> {
        let Some(output) = self.args.embedding_input_output() else {
            return Ok(());
        };
        embedding_artifact::maybe_emit_embedding_artifact(
            Some(output),
            EmbeddingEmission {
                parser,
                project_id: &self.project_id,
                root_scope: &self.repo,
                full_replace: !self.args.incremental,
                scanned_directory: true,
                files_selected: self.changed_files.iter().cloned().collect(),
                files_deleted: self.deleted_files.iter().cloned().collect(),
                categories: result_embedding_categories,
            },
        )
        .map(|_| ())
    }
}

/// Trait analyzer — Wave D điền per-language vào khung này.
pub trait Analyzer {
    /// Tên language ("python", "java", ...).
    fn language(&self) -> &'static str;

    /// Detect nhanh xem root có sources của language này không.
    fn detect(&self, root: &Path) -> bool;

    /// Chạy analyzer: parse + enrich + resolve + write qua `ctx.store`.
    /// Populate `result.embedding_categories` for Phase-04 graphless emit
    /// (framework-side helper reads it via `AnalyzerContext::emit_embedding_artifact`).
    fn run(&self, ctx: &mut AnalyzerContext<'_>) -> Result<AnalyzerResult, String>;
}
