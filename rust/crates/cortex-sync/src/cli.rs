//! CLI surface port of `incremental_sync.parse_args` (line ~3783).
//!
//! Every flag keeps the Python name, env default, and validation. Values are
//! parsed as `Option<_>` first, then resolved from the environment exactly
//! like the Python `default=os.environ.get(...)` arguments.

use clap::Parser;

fn env_get(name: &str) -> Option<String> {
    std::env::var(name).ok().filter(|v| !v.trim().is_empty())
}

#[allow(dead_code)]
fn env_or(name: &str, fallback: Option<&str>) -> Option<String> {
    env_get(name).or_else(|| fallback.map(str::to_string))
}

fn env_truthy(name: &str) -> bool {
    matches!(
        env_get(name).unwrap_or_default().to_lowercase().as_str(),
        "1" | "true" | "yes" | "on"
    )
}

fn env_not_falsy(name: &str) -> bool {
    let raw = env_get(name).unwrap_or_default().to_lowercase();
    !(raw == "0" || raw == "false" || raw == "no" || raw == "off")
}

/// Platform default graph provider: falkordb (non-Windows) / ladybug (Windows).
fn default_graph_provider() -> String {
    env_get("CODE_GRAPH_PROVIDER")
        .or_else(|| env_get("GRAPH_PROVIDER"))
        .unwrap_or_else(|| {
            if cfg!(windows) {
                "ladybug".to_string()
            } else {
                "falkordb".to_string()
            }
        })
}

fn default_python_bin() -> String {
    // Python uses sys.executable (the interpreter running the orchestrator).
    // The Rust orchestrator is not a Python process, so resolve in the same
    // order the harness uses: explicit env, the venv python next to the
    // checkout, then `python3`.
    env_get("CORTEX_SYNC_PYTHON_BIN")
        .or_else(|| {
            let exe = std::env::current_exe().ok()?;
            let repo = exe.parent()?.parent()?.parent()?.to_path_buf();
            let candidate = repo.join(".venv/bin/python");
            candidate.is_file().then(|| candidate.to_string_lossy().to_string())
        })
        .unwrap_or_else(|| "python3".to_string())
}

fn cpu_default_parse_quality_workers() -> i64 {
    let cpus = std::thread::available_parallelism()
        .map(|n| n.get())
        .unwrap_or(2);
    ((cpus as i64) / 2).clamp(1, 4)
}

#[derive(Parser, Debug, Clone)]
#[command(
    name = "cortex-sync",
    about = "Reliable incremental Neo4j/Qdrant sync using Git candidates plus SHA-256 inventory"
)]
pub struct RawArgs {
    #[arg(long)]
    pub root: Option<String>,
    #[arg(long)]
    pub config: Option<String>,
    #[arg(long)]
    pub project_id: Option<String>,
    #[arg(long)]
    pub project_name: Option<String>,
    #[arg(long)]
    pub project_code: Option<String>,
    #[arg(long)]
    pub before_sha: Option<String>,
    #[arg(long)]
    pub after_sha: Option<String>,
    #[arg(long, default_value = "auto")]
    pub parsers: String,
    #[arg(long)]
    pub python_bin: Option<String>,
    #[arg(long)]
    pub cache_dir: Option<String>,
    #[arg(long)]
    pub change_detection: Option<String>,
    #[arg(long)]
    pub lock_timeout_seconds: Option<f64>,
    #[arg(long)]
    pub reconcile: bool,
    #[arg(long)]
    pub submodules: Option<String>,
    #[arg(long)]
    pub ignore_cache: bool,
    #[arg(long)]
    pub strict: bool,
    #[arg(long)]
    pub summary_path: Option<String>,
    #[arg(long)]
    pub reliability_mode: Option<String>,
    #[arg(long)]
    pub allow_full_fallback: bool,
    #[arg(long)]
    pub neo4j_uri: Option<String>,
    #[arg(long)]
    pub neo4j_user: Option<String>,
    #[arg(long)]
    pub neo4j_password: Option<String>, // sensitive-guard:allow (flag name, khong phai secret)
    #[arg(long)]
    pub neo4j_db: Option<String>,
    #[arg(long)]
    pub graph_provider: Option<String>,
    #[arg(long)]
    pub ladybug_path: Option<String>,
    #[arg(long)]
    pub ladybug_graph: Option<String>,
    #[arg(long)]
    pub falkordb_path: Option<String>,
    #[arg(long)]
    pub falkordb_uri: Option<String>,
    #[arg(long)]
    pub falkordb_password: Option<String>, // sensitive-guard:allow (flag name, khong phai secret)
    #[arg(long)]
    pub falkordb_ssl: bool,
    #[arg(long)]
    pub falkordb_graph: Option<String>,
    #[arg(long)]
    pub no_graph: bool,
    #[arg(long)]
    pub qdrant_url: Option<String>,
    #[arg(long)]
    pub embed_model: Option<String>,
    #[arg(long)]
    pub embed_device: Option<String>,
    #[arg(long)]
    pub embed_batch_size: Option<i64>,
    #[arg(long)]
    pub max_embed_chars: Option<i64>,
    #[arg(long)]
    pub sync_messages: Option<bool>,
    #[arg(long)]
    pub no_sync_messages: bool,
    #[arg(long)]
    pub message_output_dir: Option<String>,
    #[arg(long)]
    pub message_qdrant_collection: Option<String>,
    #[arg(long)]
    pub full_scan: bool,
    #[arg(long)]
    pub sync_mode: Option<String>,
    #[arg(long)]
    pub parse_quality: Option<String>,
    #[arg(long)]
    pub parse_quality_max_files: Option<i64>,
    #[arg(long)]
    pub parse_quality_wall_seconds: Option<i64>,
    #[arg(long)]
    pub parse_quality_workers: Option<i64>,
    #[arg(long)]
    pub parse_quality_max_records: Option<i64>,
    #[arg(long)]
    pub parse_quality_max_bytes: Option<i64>,
    #[arg(long)]
    pub verbose: bool,
}

#[derive(Debug, Clone)]
#[allow(dead_code)]
pub struct Args {
    pub root: String,
    pub config: Option<String>,
    pub project_id: Option<String>,
    pub project_name: Option<String>,
    pub project_code: Option<String>,
    pub before_sha: Option<String>,
    pub after_sha: String,
    pub parsers: String,
    pub python_bin: String,
    pub cache_dir: Option<String>,
    pub change_detection: String,
    pub lock_timeout_seconds: f64,
    pub reconcile: bool,
    pub submodules: String,
    pub ignore_cache: bool,
    pub strict: bool,
    pub summary_path: Option<String>,
    pub reliability_mode: String,
    pub allow_full_fallback: bool,
    pub neo4j_uri: Option<String>,
    pub neo4j_user: Option<String>,
    pub neo4j_password: Option<String>, // sensitive-guard:allow (flag name, khong phai secret)
    pub neo4j_db: Option<String>,
    pub graph_provider: String,
    pub ladybug_path: Option<String>,
    pub ladybug_graph: Option<String>,
    pub falkordb_path: Option<String>,
    pub falkordb_uri: Option<String>,
    pub falkordb_password: Option<String>, // sensitive-guard:allow (flag name, khong phai secret)
    pub falkordb_ssl: bool,
    pub falkordb_graph: Option<String>,
    /// `Some("path"|"uri")` when one of --falkordb-path/--falkordb-uri was
    /// passed explicitly (mirrors `_explicit_falkordb_target`).
    pub explicit_falkordb_target: Option<&'static str>,
    pub no_graph: bool,
    pub qdrant_url: Option<String>,
    pub embed_model: Option<String>,
    pub embed_device: String,
    pub embed_batch_size: i64,
    pub max_embed_chars: i64,
    pub sync_messages: bool,
    pub message_output_dir: Option<String>,
    pub message_qdrant_collection: Option<String>,
    pub full_scan: bool,
    pub sync_mode: String,
    pub parse_quality: String,
    pub parse_quality_max_files: i64,
    pub parse_quality_wall_seconds: i64,
    pub parse_quality_workers: i64,
    pub parse_quality_max_records: i64,
    pub parse_quality_max_bytes: i64,
    pub verbose: bool,
}

fn die(message: &str) -> ! {
    eprintln!("error: {message}");
    std::process::exit(2);
}

impl Args {
    pub fn parse() -> Args {
        let raw = RawArgs::parse();
        let root = raw.root.clone().unwrap_or_else(|| die("--root is required"));
        let change_detection = raw
            .change_detection
            .clone()
            .or_else(|| env_get("INCREMENTAL_CHANGE_DETECTION"))
            .unwrap_or_else(|| "hybrid".to_string());
        if !matches!(change_detection.as_str(), "hybrid" | "committed" | "hash") {
            die(format!("argument --change-detection: invalid choice: '{change_detection}' (choose from 'hybrid', 'committed', 'hash')").as_str());
        }
        let submodules = raw
            .submodules
            .clone()
            .or_else(|| env_get("INCREMENTAL_SUBMODULES"))
            .unwrap_or_else(|| "recursive".to_string());
        if !matches!(submodules.as_str(), "recursive" | "ignore" | "off") {
            die(format!("argument --submodules: invalid choice: '{submodules}' (choose from 'recursive', 'ignore', 'off')").as_str());
        }
        let reliability_mode = raw
            .reliability_mode
            .clone()
            .or_else(|| env_get("CORTEX_RELIABILITY_MODE"))
            .unwrap_or_else(|| "observe".to_string());
        if !matches!(reliability_mode.as_str(), "observe" | "required") {
            die(format!("argument --reliability-mode: invalid choice: '{reliability_mode}' (choose from 'observe', 'required')").as_str());
        }
        let sync_mode = raw.sync_mode.clone().unwrap_or_else(|| "both".to_string());
        if !matches!(sync_mode.as_str(), "both" | "graph" | "embedding") {
            die(format!("argument --sync-mode: invalid choice: '{sync_mode}' (choose from 'both', 'graph', 'embedding')").as_str());
        }
        let parse_quality = raw
            .parse_quality
            .clone()
            .or_else(|| env_get("PARSE_QUALITY_POLICY"))
            .unwrap_or_else(|| "report".to_string());
        if !matches!(parse_quality.as_str(), "off" | "report" | "repair") {
            die(format!("argument --parse-quality: invalid choice: '{parse_quality}' (choose from 'off', 'report', 'repair')").as_str());
        }
        let embed_batch_size = raw
            .embed_batch_size
            .or_else(|| env_get("EMBED_BATCH_SIZE").and_then(|v| v.parse().ok()))
            .unwrap_or(8);
        let max_embed_chars = raw
            .max_embed_chars
            .or_else(|| env_get("MAX_EMBED_CHARS").and_then(|v| v.parse().ok()))
            .unwrap_or(4000);
        let args = Args {
            root,
            config: raw.config.clone(),
            project_id: raw.project_id.clone().or_else(|| env_get("PROJECT_ID")),
            project_name: raw.project_name.clone().or_else(|| env_get("PROJECT_NAME")),
            project_code: raw.project_code.clone().or_else(|| env_get("PROJECT_CODE")),
            before_sha: raw.before_sha.clone().or_else(|| env_get("GIT_COMMIT_SHA_BEFORE")),
            after_sha: raw
                .after_sha
                .clone()
                .or_else(|| env_get("GIT_COMMIT_SHA_AFTER"))
                .unwrap_or_else(|| "HEAD".to_string()),
            parsers: raw.parsers.clone(),
            python_bin: raw
                .python_bin
                .clone()
                .unwrap_or_else(default_python_bin),
            cache_dir: raw.cache_dir.clone().or_else(|| env_get("QDRANT_CACHE_DIR")),
            change_detection,
            lock_timeout_seconds: raw
                .lock_timeout_seconds
                .or_else(|| env_get("INCREMENTAL_LOCK_TIMEOUT_SECONDS").and_then(|v| v.parse().ok()))
                .unwrap_or(10.0),
            reconcile: raw.reconcile,
            submodules,
            ignore_cache: raw.ignore_cache,
            strict: raw.strict || env_truthy("INCREMENTAL_STRICT"),
            summary_path: raw.summary_path.clone().or_else(|| env_get("INCREMENTAL_SUMMARY_PATH")),
            reliability_mode,
            allow_full_fallback: raw.allow_full_fallback,
            neo4j_uri: raw.neo4j_uri.clone().or_else(|| env_get("NEO4J_URI")),
            neo4j_user: raw.neo4j_user.clone().or_else(|| env_get("NEO4J_USER")),
            neo4j_password: raw.neo4j_password.clone().or_else(|| env_get("NEO4J_PASS")), // sensitive-guard:allow (flag name, khong phai secret)
            neo4j_db: raw.neo4j_db.clone().or_else(|| env_get("NEO4J_DB")),
            graph_provider: raw
                .graph_provider
                .clone()
                .unwrap_or_else(default_graph_provider),
            ladybug_path: raw.ladybug_path.clone().or_else(|| env_get("LADYBUG_PATH")),
            // Raw --ladybug-graph ONLY (no env merge): the H1 precedence
            // chain is arg > project_id > LADYBUG_GRAPH env > "hyper_graph"
            // and is resolved in graphops::prepare_graph_args. Merging env
            // here would let it outrank project_id.
            ladybug_graph: raw.ladybug_graph.clone(),
            falkordb_path: raw.falkordb_path.clone().or_else(|| env_get("FALKORDB_PATH")),
            falkordb_uri: raw.falkordb_uri.clone().or_else(|| env_get("FALKORDB_URI")),
            falkordb_password: raw // sensitive-guard:allow (flag name, khong phai secret)
                .falkordb_password // sensitive-guard:allow (flag name, khong phai secret)
                .clone()
                .or_else(|| env_get("FALKORDB_PASSWORD")), // sensitive-guard:allow (flag name, khong phai secret)
            falkordb_ssl: raw.falkordb_ssl || env_truthy("FALKORDB_SSL"),
            falkordb_graph: raw
                .falkordb_graph
                .clone()
                .or_else(|| env_get("FALKORDB_GRAPH"))
                .or_else(|| env_get("FALKORDB_DATABASE")),
            explicit_falkordb_target: if raw.falkordb_path.is_some() {
                Some("path")
            } else if raw.falkordb_uri.is_some() {
                Some("uri")
            } else {
                None
            },
            no_graph: raw.no_graph,
            qdrant_url: raw.qdrant_url.clone().or_else(|| env_get("QDRANT_CODE_PATH")),
            embed_model: raw
                .embed_model
                .clone()
                .or_else(|| env_get("CODE_EMBEDDING_MODEL"))
                .or_else(|| env_get("EMBED_MODEL")),
            embed_device: raw
                .embed_device
                .clone()
                .or_else(|| env_get("EMBED_DEVICE"))
                .unwrap_or_else(|| "cpu".to_string()),
            embed_batch_size,
            max_embed_chars,
            sync_messages: if raw.no_sync_messages {
                false
            } else {
                raw.sync_messages.unwrap_or_else(|| env_not_falsy("SYNC_MESSAGES"))
            },
            message_output_dir: raw
                .message_output_dir
                .clone()
                .or_else(|| env_get("MESSAGE_OUTPUT_DIR")),
            message_qdrant_collection: raw
                .message_qdrant_collection
                .clone()
                .or_else(|| env_get("MESSAGE_QDRANT_COLLECTION")),
            full_scan: raw.full_scan,
            sync_mode,
            parse_quality,
            parse_quality_max_files: raw.parse_quality_max_files.unwrap_or(500),
            parse_quality_wall_seconds: raw.parse_quality_wall_seconds.unwrap_or(900),
            parse_quality_workers: raw
                .parse_quality_workers
                .unwrap_or_else(cpu_default_parse_quality_workers),
            parse_quality_max_records: raw.parse_quality_max_records.unwrap_or(10_000),
            parse_quality_max_bytes: raw.parse_quality_max_bytes.unwrap_or(8 * 1024 * 1024),
            verbose: raw.verbose,
        };
        // Post-parse validations mirroring parser.error checks.
        if args
            .parse_quality_max_files
            .min(args.parse_quality_wall_seconds)
            .min(args.parse_quality_workers)
            .min(args.parse_quality_max_records)
            .min(args.parse_quality_max_bytes)
            <= 0
        {
            die("parse-quality limits must be positive");
        }
        if args.sync_mode != "both" && !args.full_scan {
            die("--sync-mode graph/embedding requires --full-scan");
        }
        if args.sync_mode == "graph" && args.no_graph {
            die("--sync-mode graph cannot be combined with --no-graph");
        }
        if args.sync_mode == "embedding" && args.qdrant_url.is_none() {
            die("--sync-mode embedding requires configured Qdrant storage");
        }
        if !matches!(args.graph_provider.as_str(), "neo4j" | "falkordb" | "ladybug") {
            die(format!(
                "argument --graph-provider: invalid choice: '{}' (choose from 'neo4j', 'falkordb', 'ladybug')",
                args.graph_provider
            ).as_str());
        }
        args
    }

    #[allow(dead_code)]
    pub fn env_bool_flag(name: &str) -> bool {
        env_truthy(name)
    }

    #[allow(dead_code)]
    pub fn env_not_falsy_flag(name: &str) -> bool {
        env_not_falsy(name)
    }

    #[allow(dead_code)]
    pub fn env_or_env(primary: &str, secondary: &str) -> Option<String> {
        env_or(primary, env_get(secondary).as_deref())
    }
}

/// Resolve the analyzer python_bin from raw CLI args (delegation path).
pub fn resolve_python_bin(raw: &[String]) -> String {
    let mut index = 0;
    while index < raw.len() {
        if raw[index] == "--python-bin" && index + 1 < raw.len() {
            return raw[index + 1].clone();
        }
        if let Some(value) = raw[index].strip_prefix("--python-bin=") {
            return value.to_string();
        }
        index += 1;
    }
    env_get("CORTEX_SYNC_PYTHON_BIN")
        .or_else(|| {
            let exe = std::env::current_exe().ok()?;
            let repo = exe.parent()?.parent()?.parent()?.to_path_buf();
            let candidate = repo.join(".venv/bin/python");
            candidate.is_file().then(|| candidate.to_string_lossy().to_string())
        })
        .unwrap_or_else(|| "python3".to_string())
}
