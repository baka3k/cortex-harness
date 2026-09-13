//! CLI contract — giữ nguyên từng chữ với analyzers Python.
//!
//! Orchestrator (`incremental_sync.py::_build_analyzer_cmd`) gọi analyzer bằng
//! đúng bộ cờ này nên 2 backend đổi cho nhau thay thế được:
//! `--root --project-id --project-name --commit-sha-before --commit-sha-after
//!  --graph-provider --falkordb-uri --falkordb-graph [--ladybug-path --ladybug-graph]
//!  --incremental --changed-files-manifest --deleted-files-manifest
//!  --disable-message-scan --verbose [--embed-model] [--sync-mode] ...`

use std::path::PathBuf;

use clap::Parser;
use cortex_graph_writer::store::{GraphStore, StoreError};
use cortex_graph_writer::store::{FalkorDbStore, LadybugStore};
use cortex_falkordb::client::FalkorDbClient;

/// Đối số CLI chung của mọi analyzer (subset port theo phase-04 contract).
#[derive(Debug, Clone, Parser)]
#[command(no_binary_name = true)]
pub struct AnalyzerArgs {
    /// Root folder chứa sources.
    #[arg(long)]
    pub root: String,

    #[arg(long)]
    pub project_id: Option<String>,
    /// Alias `--project_id` (orchestrator dùng 2 dạng).
    #[arg(long = "project_id", hide = true)]
    pub project_id_alt: Option<String>,

    #[arg(long)]
    pub project_name: Option<String>,
    #[arg(long = "project_name", hide = true)]
    pub project_name_alt: Option<String>,

    /// Git commit SHA trước thay đổi (incremental metadata).
    #[arg(long, default_value = "")]
    pub commit_sha_before: String,

    /// Git commit SHA sau thay đổi.
    #[arg(long, default_value = "")]
    pub commit_sha_after: String,

    /// Graph provider: `falkordb` | `ladybug` (neo4j chỉ nhận, không hỗ trợ).
    #[arg(long, default_value = "falkordb")]
    pub graph_provider: String,

    /// Remote FalkorDB URI (`scheme://host:port` hoặc `host:port`).
    #[arg(long)]
    pub falkordb_uri: Option<String>,
    /// FalkorDBLite .rdb path — embedded backend chạy phía Python; Rust chỉ
    /// nhận cờ để contract tương thích (không spawn embedded).
    #[arg(long)]
    pub falkordb_path: Option<String>,
    #[arg(long)]
    pub falkordb_password: Option<String>, // sensitive-guard:allow
    #[arg(long)]
    pub falkordb_graph: Option<String>,

    #[arg(long)]
    pub ladybug_path: Option<String>,
    #[arg(long)]
    pub ladybug_graph: Option<String>,

    #[arg(long)]
    pub language: Option<String>,
    #[arg(long)]
    pub repo: Option<String>,
    #[arg(long)]
    pub build_system: Option<String>,
    #[arg(long = "build_system", hide = true)]
    pub build_system_alt: Option<String>,

    /// Bật incremental ingestion mode.
    #[arg(long)]
    pub incremental: bool,
    /// Manifest JSON/TXT các file changed+impacted (relative to --root).
    #[arg(long)]
    pub changed_files_manifest: Option<String>,
    /// Manifest JSON/TXT các file đã xoá (relative to --root).
    #[arg(long)]
    pub deleted_files_manifest: Option<String>,

    /// Message scan là plane Python-side; Rust nhận cờ (mặc định bật như
    /// Python) và skip có kiểm soát.
    #[arg(long)]
    pub enable_message_scan: bool,
    #[arg(long)]
    pub disable_message_scan: bool,

    /// Embedding model — giữ contract; embedding tách khỏi analyzer Rust.
    #[arg(long)]
    pub embed_model: Option<String>,
    /// Sync mode orchestrator truyền xuống — giữ contract.
    #[arg(long)]
    pub sync_mode: Option<String>,

    // ── Flags orchestrator truyền cho lane vector/message/cache — nhận và
    // bỏ qua: embedding tách khỏi analyzer Rust, message scan là plane
    // Python, parse/resume cache không áp dụng cho backend này.
    /// Qdrant endpoint — backend Rust không embed (key decision #3).
    #[arg(long, hide = true)]
    pub qdrant_url: Option<String>,
    #[arg(long, hide = true)]
    pub qdrant_collection: Option<String>,
    #[arg(long, hide = true)]
    pub device: Option<String>,
    #[arg(long, hide = true)]
    pub batch_size: Option<i64>,
    #[arg(long, hide = true)]
    pub max_embed_chars: Option<i64>,
    #[arg(long, hide = true)]
    pub chunk_embed: bool,
    #[arg(long, hide = true)]
    pub cache_dir: Option<String>,
    #[arg(long, hide = true)]
    pub keep_cache: bool,
    #[arg(long, hide = true)]
    pub disable_parse_cache: bool,
    #[arg(long, hide = true)]
    pub ignore_cache: bool,
    #[arg(long, hide = true)]
    pub message_output_dir: Option<String>,
    #[arg(long, hide = true)]
    pub message_qdrant_collection: Option<String>,
    #[arg(long, hide = true)]
    pub neo4j_db: Option<String>,

    /// Harness dev.json config — Python-side convenience; Rust orchestrator
    /// truyền explicit args nên nhận và bỏ qua (khớp contract).
    #[arg(long, hide = true)]
    pub config: Option<String>,

    /// Structured JSON summary output path.
    #[arg(long)]
    pub summary_path: Option<String>,

    #[arg(long)]
    pub dry_run: bool,
    #[arg(long)]
    pub verbose: bool,
}

impl AnalyzerArgs {
    pub fn parse_from(env: &[String]) -> Self {
        let mut argv: Vec<String> = env.to_vec();
        // Message-scan flags: normalize — dedupe cờ bật, giữ cờ tắt (Python
        // `set_defaults(enable_message_scan=True)`; `--disable` thắng).
        argv.retain(|a| a != "--enable-message-scan");
        if !argv.iter().any(|a| a == "--disable-message-scan") {
            argv.push("--enable-message-scan".to_string());
        }
        let strs: Vec<&str> = argv.iter().map(String::as_str).collect();
        let mut args = <Self as clap::Parser>::parse_from(strs);
        if args.project_id.is_none() {
            args.project_id = args.project_id_alt.take();
        }
        if args.project_name.is_none() {
            args.project_name = args.project_name_alt.take();
        }
        if args.build_system.is_none() {
            args.build_system = args.build_system_alt.take();
        }
        args
    }

    pub fn project_id_or_root(&self) -> String {
        self.project_id
            .clone()
            .unwrap_or_else(|| root_basename(&self.root))
    }

    pub fn project_name(&self) -> String {
        self.project_name
            .clone()
            .unwrap_or_else(|| self.project_id_or_root())
    }

    pub fn language_or_default(&self) -> String {
        self.language.clone().unwrap_or_default()
    }

    pub fn repo_or_root(&self) -> String {
        // os.path.abspath: normalize mà KHÔNG resolve symlink.
        self.repo
            .clone()
            .unwrap_or_else(|| abs_root(&self.root).to_string_lossy().to_string())
    }

    pub fn build_system(&self) -> String {
        self.build_system.clone().unwrap_or_default()
    }

    /// Message scan bật khi không có `--disable-message-scan` (như Python).
    pub fn message_scan_enabled(&self) -> bool {
        !self.disable_message_scan
    }

    /// `graph_writes_disabled` — CORTEX_DISABLE_GRAPH=1|true|yes|on ⇒ chạy
    /// parse-only, không mở store (khớp `prepare_graph_args` Python).
    pub fn graph_writes_disabled(&self) -> bool {
        matches!(
            std::env::var("CORTEX_DISABLE_GRAPH")
                .unwrap_or_default()
                .trim()
                .to_lowercase()
                .as_str(),
            "1" | "true" | "yes" | "on"
        )
    }

    /// Mở GraphStore theo provider đã chọn — tương đương
    /// `GraphDriverFactory` + `prepare_graph_args` phía Python.
    pub fn open_store(&self) -> Result<Box<dyn GraphStore>, StoreError> {
        let provider = self.graph_provider.to_lowercase();
        match provider.as_str() {
            // Graph-name precedence khớp `prepare_graph_args` Python:
            // flag → env (argparse default) → project_id → "hyper_graph".
            "ladybug" => {
                let path = self
                    .ladybug_path
                    .clone()
                    .or_else(|| std::env::var("LADYBUG_PATH").ok().filter(|v| !v.is_empty()))
                    .ok_or_else(|| {
                        StoreError::Invalid(
                            "provider=ladybug cần --ladybug-path hoặc LADYBUG_PATH".into(),
                        )
                    })?;
                let graph = self
                    .ladybug_graph
                    .clone()
                    .or_else(|| std::env::var("LADYBUG_GRAPH").ok().filter(|v| !v.is_empty()))
                    .unwrap_or_else(|| self.project_id_or_root());
                Ok(Box::new(LadybugStore::open(
                    std::path::Path::new(&path),
                    &graph,
                )?))
            }
            "falkordb" | "falkor" => {
                let graph = self
                    .falkordb_graph
                    .clone()
                    .or_else(|| std::env::var("FALKORDB_GRAPH").ok().filter(|v| !v.is_empty()))
                    .unwrap_or_else(|| self.project_id_or_root());
                if let Some(path) = self
                    .falkordb_path
                    .clone()
                    .or_else(|| std::env::var("FALKORDB_PATH").ok().filter(|v| !v.is_empty()))
                {
                    // Explicit local embedded target — Rust không spawn embedded
                    // backend; fail-closed để không ghi nhầm vào remote.
                    let _ = path;
                    return Err(StoreError::Invalid(
                        "--falkordb-path (embedded FalkorDBLite) chỉ chạy phía Python; \
                         dùng --falkordb-uri cho remote hoặc --graph-provider ladybug"
                            .into(),
                    ));
                }
                let uri = self
                    .falkordb_uri
                    .clone()
                    .or_else(|| std::env::var("FALKORDB_URI").ok().filter(|v| !v.is_empty()))
                    .unwrap_or_else(|| "127.0.0.1:6379".to_string());
                validate_falkordb_uri(&uri)?;
                if self.falkordb_password.is_some() {
                    return Err(StoreError::Invalid(
                        // sensitive-guard:allow (tên flag, không phải secret)
                        "xác thực remote (falkordb_password): backend Rust chưa hỗ trợ — \
                         chạy provider này phía Python"
                            .into(),
                    ));
                }
                let (host, port) = parse_falkordb_uri(&uri);
                let client = FalkorDbClient::connect_verified(&host, port)?;
                Ok(Box::new(FalkorDbStore::new(client, graph)))
            }
            other => Err(StoreError::Invalid(format!(
                "unsupported --graph-provider: {other}"
            ))),
        }
    }
}

fn root_basename(root: &str) -> String {
    // os.path.basename(os.path.abspath(root)) — abspath trước basename.
    abs_root(root)
        .file_name()
        .map(|n| n.to_string_lossy().to_string())
        .unwrap_or_else(|| root.to_string())
}

/// Validate scheme như Python driver: chỉ nhận
/// `falkor|falkors|redis|rediss|unix://` (fail-loud thay vì treat TLS URI
/// dạng plaintext).
pub fn validate_falkordb_uri(uri: &str) -> Result<(), StoreError> {
    if let Some((scheme, _)) = uri.split_once("://")
        && !matches!(scheme, "falkor" | "falkors" | "redis" | "rediss" | "unix") {
            return Err(StoreError::Invalid(format!(
                "FalkorDB URI phải dùng falkor://, falkors://, redis://, rediss://, \
                 hoặc unix://; nhận {scheme:?}"
            )));
        }
    Ok(())
}

/// Parse URI như Python driver: `scheme://host:port` hoặc `host:port` hoặc
/// `host` (default port 6379, host default `127.0.0.1`).
pub fn parse_falkordb_uri(uri: &str) -> (String, u16) {
    let cleaned = uri
        .split("://")
        .last()
        .unwrap_or(uri)
        .trim_end_matches('/')
        .to_string();
    match cleaned.rsplit_once(':') {
        Some((host, port)) if port.chars().all(|c| c.is_ascii_digit()) && !port.is_empty() => {
            (host.to_string(), port.parse().unwrap_or(6379))
        }
        _ => (cleaned, 6379),
    }
}

/// Đường dẫn root tuyệt đối hoá (không resolve symlink — khớp `abspath`).
pub fn abs_root(root: &str) -> PathBuf {
    let path = PathBuf::from(root);
    if path.is_absolute() {
        normalize_absolute(&path)
    } else {
        let cwd = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
        normalize_absolute(&cwd.join(path))
    }
}

/// Chuẩn hoá `a/./b` và `a/c/../b` như `os.path.abspath` (không đụng symlink).
fn normalize_absolute(path: &std::path::Path) -> PathBuf {
    let mut out = PathBuf::from("/");
    for component in path.components() {
        match component {
            std::path::Component::RootDir => {}
            std::path::Component::CurDir => {}
            std::path::Component::ParentDir => {
                out.pop();
            }
            other => out.push(other.as_os_str()),
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Contract test: dòng lệnh giống `_build_analyzer_cmd` của orchestrator
    /// (toàn bộ cờ lane vector/message/cache) phải parse được.
    #[test]
    fn parses_orchestrator_command_line() {
        let argv: Vec<String> = [
            "--root",
            "/tmp/proj",
            "--project-id",
            "proj",
            "--project-name",
            "proj",
            "--commit-sha-before",
            "aaa",
            "--commit-sha-after",
            "bbb",
            "--graph-provider",
            "falkordb",
            "--falkordb-uri",
            "127.0.0.1:6379",
            "--falkordb-graph",
            "proj",
            "--embed-model",
            "jinaai/jina-embeddings-v3",
            "--device",
            "mps",
            "--batch-size",
            "8",
            "--max-embed-chars",
            "4000",
            "--qdrant-collection",
            "proj",
            "--incremental",
            "--changed-files-manifest",
            "/tmp/changed.json",
            "--deleted-files-manifest",
            "/tmp/deleted.json",
            "--ignore-cache",
            "--enable-message-scan",
            "--message-output-dir",
            "/tmp/msg",
            "--message-qdrant-collection",
            "proj_msg",
            "--verbose",
        ]
        .iter()
        .map(|s| s.to_string())
        .collect();
        let args = AnalyzerArgs::parse_from(&argv);
        assert_eq!(args.root, "/tmp/proj");
        assert_eq!(args.project_id_or_root(), "proj");
        assert!(args.incremental);
        assert!(args.message_scan_enabled());
        assert!(args.verbose);

        // `--disable-message-scan` phải thắng `--enable-message-scan`.
        let mut off = argv.clone();
        off.retain(|a| a != "--enable-message-scan");
        off.push("--disable-message-scan".to_string());
        let args_off = AnalyzerArgs::parse_from(&off);
        assert!(!args_off.message_scan_enabled());
    }

    #[test]
    fn project_id_uses_abspath_basename() {
        let argv: Vec<String> = ["--root", "."]
            .iter()
            .map(|s| s.to_string())
            .collect();
        let args = AnalyzerArgs::parse_from(&argv);
        let project_id = args.project_id_or_root();
        assert!(!project_id.is_empty() && project_id != ".");
    }

    #[test]
    fn falkordb_uri_validation() {
        assert!(validate_falkordb_uri("127.0.0.1:6379").is_ok());
        assert!(validate_falkordb_uri("redis://127.0.0.1:6379").is_ok());
        assert!(validate_falkordb_uri("https://127.0.0.1:6379").is_err());
    }
}
