//! analyzer-dart — Rust port của `code-tiny/tools/flutter/flutter_analyzer.py`
//! (phase 02, plan 260915-analyzer-layer-rust-cutover).
//!
//! Pipeline: mode gating (dart/flutter) → pubspec detect → scan *.dart →
//! tree-sitter parse (dart-grammar-vendored) → facts (nodes/edges/
//! diagnostics/summary) → dependency-cache impact expansion (incremental) →
//! artifact JSON (`.cortex/flutter/{mode}-facts.json`) → graph write qua
//! `LanguageCodeWriter::write_all` + incremental cleanup keep-ids →
//! `[SCAN_RESULT]` line byte-compatible với bản Python.
//!
//! Đường có chủ đích (theo plan):
//! * `--mode all` → exit 2, hướng dẫn chạy `dart` và `flutter` riêng
//!   (orchestrator chỉ gọi từng mode).
//! * Vector/embedding KHÔNG port (dart ∈ EMITTING_VECTOR_CLI_PARSERS nhưng
//!   embedding là plane orchestrator-level, phase-06) — flags nhận và bỏ qua.
//! * Message scan là plane Python-side — flag nhận, skip có kiểm soát.

mod cache;
mod dartparse;
mod detector;
mod models;
mod normalizer;

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

use clap::Parser;
use serde_json::{json, Value};

use cache::{select_incremental_facts, DependencyIndex};
use cortex_analyzer_framework::cli::{parse_falkordb_uri, validate_falkordb_uri};
use cortex_analyzer_framework::embedding_artifact::{self, EmbeddingEmission};
use cortex_graph_writer::language_writer::{FilesVariant, LanguageCodeWriter, WriteAllPayload};
use cortex_graph_writer::store::{FalkorDbStore, GraphStore, LadybugStore};
use models::AnalysisFacts;

/// Đối số CLI — contract từng chữ với `flutter_analyzer.py::parse_args`.
#[derive(Debug, Parser)]
#[command(no_binary_name = true, version = cortex_analyzer_framework::BUILD_COMMIT)]
pub struct DartArgs {
    #[arg(long, required = true)]
    pub root: String,

    /// `dart` | `flutter` | `all` (`all` → error; env FLUTTER_ANALYZER_MODE).
    #[arg(long)]
    pub mode: Option<String>,

    #[arg(long)]
    pub project_id: Option<String>,
    #[arg(long = "project_id", hide = true)]
    pub project_id_alt: Option<String>,

    #[arg(long)]
    pub project_name: Option<String>,
    #[arg(long = "project_name", hide = true)]
    pub project_name_alt: Option<String>,

    #[arg(long)]
    pub language: Option<String>,
    #[arg(long)]
    pub repo: Option<String>,
    #[arg(long)]
    pub build_system: Option<String>,
    #[arg(long = "build_system", hide = true)]
    pub build_system_alt: Option<String>,

    #[arg(long, default_value = "")]
    pub commit_sha_before: String,
    #[arg(long, default_value = "")]
    pub commit_sha_after: String,

    #[arg(long)]
    pub incremental: bool,
    #[arg(long)]
    pub changed_files_manifest: Option<String>,
    #[arg(long)]
    pub deleted_files_manifest: Option<String>,

    /// Dependency-cache dir (functional — khác các analyzer khác).
    #[arg(long)]
    pub cache_dir: Option<String>,
    #[arg(long)]
    pub ignore_cache: bool,

    // ── Vector plane: nhận và bỏ qua (embedding tách orchestrator phase-06).
    #[arg(long, hide = true)]
    pub qdrant_url: Option<String>,
    #[arg(long, hide = true)]
    pub qdrant_collection: Option<String>,
    #[arg(long, hide = true)]
    pub qdrant_batch_size: Option<i64>,
    #[arg(long, hide = true)]
    pub qdrant_timeout: Option<f64>,
    #[arg(long, hide = true)]
    pub qdrant_retries: Option<i64>,
    #[arg(long, hide = true)]
    pub qdrant_retry_sleep: Option<f64>,
    #[arg(long, hide = true)]
    pub embed_model: Option<String>,
    #[arg(long, hide = true)]
    pub device: Option<String>,
    #[arg(long, hide = true)]
    pub batch_size: Option<i64>,
    #[arg(long, hide = true)]
    pub max_embed_chars: Option<i64>,

    /// Phase-02 (R12): emit EmbeddingInputArtifact cho orchestrator.
    #[arg(long, hide = true)]
    pub embedding_input_output: Option<String>,

    // ── Neo4j legacy flags: nhận và bỏ qua (graph qua provider falkordb/
    // ladybug; chỉ --neo4j-db được writer Python dùng làm database).
    #[arg(long, hide = true)]
    pub neo4j_uri: Option<String>,
    #[arg(long, hide = true)]
    pub neo4j_user: Option<String>,
    #[arg(long, hide = true)]
    pub neo4j_password: Option<String>, // sensitive-guard:allow
    #[arg(long, hide = true)]
    pub neo4j_db: Option<String>,
    #[arg(long, hide = true)]
    pub neo4j_batch_size: Option<i64>,

    // ── Graph provider args (add_graph_provider_args).
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
    pub falkordb_password: Option<String>, // sensitive-guard:allow
    #[arg(long)]
    pub falkordb_ssl: bool,
    #[arg(long)]
    pub falkordb_graph: Option<String>,

    #[arg(long)]
    pub enable_message_scan: bool,
    #[arg(long)]
    pub disable_message_scan: bool,
    #[arg(long, hide = true)]
    pub message_output_dir: Option<String>,
    #[arg(long, hide = true)]
    pub message_qdrant_collection: Option<String>,

    #[arg(long)]
    pub facts_output: Option<String>,
    #[arg(long)]
    pub preflight: bool,
    #[arg(long)]
    pub dry_run: bool,
    #[arg(long)]
    pub verbose: bool,
}

impl DartArgs {
    fn parse_from(argv: &[String]) -> Self {
        // Message-scan normalize như framework: `--disable` thắng.
        let mut argv: Vec<String> = argv.to_vec();
        argv.retain(|flag| flag != "--enable-message-scan");
        if !argv.iter().any(|flag| flag == "--disable-message-scan") {
            argv.push("--enable-message-scan".to_string());
        }
        let mut args = <Self as clap::Parser>::parse_from(argv.iter().map(String::as_str));
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

    fn mode(&self) -> String {
        self.mode
            .clone()
            .or_else(|| std::env::var("FLUTTER_ANALYZER_MODE").ok())
            .unwrap_or_else(|| "dart".to_string())
    }
}

fn expanduser(path: &str) -> PathBuf {
    if let Some(rest) = path.strip_prefix("~/")
        && let Ok(home) = std::env::var("HOME")
    {
        return PathBuf::from(home).join(rest);
    }
    if path == "~"
        && let Ok(home) = std::env::var("HOME")
    {
        return PathBuf::from(home);
    }
    PathBuf::from(path)
}

fn resolved_root(root: &str) -> PathBuf {
    // Path(args.root).expanduser().resolve() — resolve symlink như Python.
    let path = expanduser(root);
    path.canonicalize().unwrap_or(path)
}

fn graph_writes_disabled() -> bool {
    matches!(
        std::env::var("CORTEX_DISABLE_GRAPH")
            .unwrap_or_default()
            .trim()
            .to_lowercase()
            .as_str(),
        "1" | "true" | "yes" | "on"
    )
}

fn open_store(args: &DartArgs) -> Result<Box<dyn GraphStore>, String> {
    let provider = args
        .graph_provider
        .clone()
        .filter(|value| !value.is_empty())
        .or_else(|| {
            std::env::var("CORTEX_GRAPH_PROVIDER")
                .ok()
                .filter(|value| !value.is_empty())
        })
        .unwrap_or_else(|| "neo4j".to_string());
    match provider.to_lowercase().as_str() {
        "ladybug" => {
            let path = args
                .ladybug_path
                .clone()
                .or_else(|| std::env::var("LADYBUG_PATH").ok().filter(|v| !v.is_empty()))
                .ok_or_else(|| "provider=ladybug cần --ladybug-path hoặc LADYBUG_PATH".to_string())?;
            let graph = args
                .ladybug_graph
                .clone()
                .or_else(|| std::env::var("LADYBUG_GRAPH").ok().filter(|v| !v.is_empty()))
                .unwrap_or_else(|| "hyper_graph".to_string());
            LadybugStore::open(Path::new(&path), &graph)
                .map(|store| Box::new(store) as Box<dyn GraphStore>)
                .map_err(|e| e.to_string())
        }
        "falkordb" | "falkor" => {
            if args
                .falkordb_path
                .clone()
                .or_else(|| std::env::var("FALKORDB_PATH").ok().filter(|v| !v.is_empty()))
                .is_some()
            {
                return Err(
                    "--falkordb-path (embedded FalkorDBLite) chỉ chạy phía Python; dùng \
                     --falkordb-uri cho remote hoặc --graph-provider ladybug"
                        .to_string(),
                );
            }
            let uri = args
                .falkordb_uri
                .clone()
                .or_else(|| std::env::var("FALKORDB_URI").ok().filter(|v| !v.is_empty()))
                .unwrap_or_else(|| "127.0.0.1:6379".to_string());
            validate_falkordb_uri(&uri).map_err(|e| e.to_string())?;
            let (host, port) = parse_falkordb_uri(&uri);
            let client = cortex_falkordb::client::FalkorDbClient::connect_verified(&host, port)
                .map_err(|e| e.to_string())?;
            let graph = args
                .falkordb_graph
                .clone()
                .filter(|value| !value.is_empty())
                .or_else(|| std::env::var("FALKORDB_GRAPH").ok().filter(|v| !v.is_empty()))
                .unwrap_or_else(|| "hyper_graph".to_string());
            Ok(Box::new(FalkorDbStore::new(client, graph)))
        }
        other => Err(format!("unsupported --graph-provider: {other}")),
    }
}

/// `_manifest_paths` của flutter_analyzer.py — manifest phải tồn tại, JSON
/// object (`files`/`paths`) hoặc JSON array hoặc txt lines; path tuyệt đối
/// phải nằm dưới root; kết quả sorted set rel-posix.
fn manifest_paths(path: &str, root: &Path) -> Result<BTreeSet<String>, String> {
    if path.is_empty() {
        return Ok(BTreeSet::new());
    }
    let manifest = PathBuf::from(path);
    if !manifest.is_file() {
        return Err(format!("manifest not found: {}", manifest.display()));
    }
    let text = std::fs::read_to_string(&manifest).map_err(|e| e.to_string())?;
    let items: Vec<String> = match serde_json::from_str::<Value>(&text) {
        Ok(Value::Object(map)) => match map.get("files").or_else(|| map.get("paths")) {
            Some(Value::Array(values)) => values
                .iter()
                .filter_map(|item| item.as_str().map(str::to_string))
                .collect(),
            _ => Vec::new(),
        },
        Ok(Value::Array(values)) => values
            .iter()
            .filter_map(|item| item.as_str().map(str::to_string))
            .collect(),
        _ => text
            .lines()
            .map(|line| line.trim().to_string())
            .filter(|line| !line.is_empty())
            .collect(),
    };
    let mut normalized = BTreeSet::new();
    for raw in items {
        let candidate = PathBuf::from(&raw);
        let relative = if candidate.is_absolute() {
            let resolved = candidate.canonicalize().map_err(|_| {
                format!("manifest path is outside the project root: {raw}")
            })?;
            resolved
                .strip_prefix(root)
                .map(|rel| rel.to_string_lossy().replace('\\', "/"))
                .map_err(|_| format!("manifest path is outside the project root: {raw}"))?
        } else {
            raw.clone()
        };
        normalized.insert(relative);
    }
    Ok(normalized)
}

/// Dry-run count của Python main: rglob *.dart qua `has_excluded_parent`
/// (COMMON_SCAN_EXCLUDE + extra ignore — KHÔNG phải SKIPPED_DIRECTORIES).
fn count_dart_files(root: &Path) -> usize {
    let mut count = 0;
    count_walk(root, root, &mut count);
    count
}

fn count_walk(root: &Path, dir: &Path, count: &mut usize) {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() {
            let name = entry.file_name().to_string_lossy().to_string();
            if !is_excluded_dir(&name) {
                count_walk(root, &path, count);
            }
        } else {
            let name = entry.file_name().to_string_lossy().to_string();
            if name.ends_with(".dart") && !has_excluded_parent(&path, root) {
                *count += 1;
            }
        }
    }
}

fn has_excluded_parent(candidate: &Path, root: &Path) -> bool {
    let Ok(relative) = candidate.strip_prefix(root) else {
        return false;
    };
    let parents = relative.components().count();
    if parents <= 1 {
        return false;
    }
    relative
        .components()
        .take(parents - 1)
        .map(|component| component.as_os_str().to_string_lossy().to_string())
        .any(|part| is_excluded_dir(&part))
}

fn is_excluded_dir(name: &str) -> bool {
    cortex_analyzer_framework::scan::COMMON_SCAN_EXCLUDE.contains(&name)
        || cortex_analyzer_framework::scan::matches_extra_ignore(name)
}

/// `write_fact_artifact` — JSON indent 2 sort_keys + newline.
fn write_fact_artifact(path: &Path, facts: &AnalysisFacts) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    let value = json!({
        "header": serde_json::to_value(&facts.header).map_err(|e| e.to_string())?,
        "nodes": facts
            .nodes
            .iter()
            .map(|node| serde_json::to_value(node).map_err(|e| e.to_string()))
            .collect::<Result<Vec<Value>, String>>()?,
        "edges": facts
            .edges
            .iter()
            .map(|edge| serde_json::to_value(edge).map_err(|e| e.to_string()))
            .collect::<Result<Vec<Value>, String>>()?,
        "diagnostics": facts
            .diagnostics
            .iter()
            .map(|item| serde_json::to_value(item).map_err(|e| e.to_string()))
            .collect::<Result<Vec<Value>, String>>()?,
        "summary": serde_json::to_value(&facts.summary).map_err(|e| e.to_string())?,
    });
    let mut text = serde_json::to_string_pretty(&cache::sorted_value(&value))
        .map_err(|e| e.to_string())?;
    text.push('\n');
    std::fs::write(path, text).map_err(|e| e.to_string())
}

/// `write_graph` — normalize + write_all + flutter incremental cleanup.
/// Trả (counts, embedding_categories) — counts = graph totals; embedding_categories
/// = rows sẵn cho phase-06 orchestrator (plan `260916-1432-legacy-17-vector-emit` R12).
fn write_graph(
    args: &DartArgs,
    facts: &AnalysisFacts,
    root: &Path,
    cleanup_paths: &[String],
) -> Result<(BTreeMap<String, i64>, Vec<(String, Vec<Value>)>), String> {
    let store = open_store(args)?;
    let mut writer = LanguageCodeWriter::new(store, args.neo4j_db.clone(), 1000, args.verbose);
    let repo = args
        .repo
        .clone()
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| root.to_string_lossy().to_string());
    let build_system = args
        .build_system
        .clone()
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| "flutter".to_string());
    let batch = normalizer::normalize_facts(
        facts,
        args.project_name.as_deref(),
        &repo,
        &build_system,
    )?;
    let payload = WriteAllPayload {
        projects: &[],
        packages: &[],
        namespaces: &[],
        files: &batch.files,
        classes: &batch.classes,
        types: &batch.types,
        function_types: &[],
        functions: &batch.functions,
        fields: &batch.fields,
        aliases: &[],
        templates: &[],
        relations: &batch.relations,
        calls: &[],
        calls_with_site: &[],
        properties: &[],
        events: &[],
        interfaces: &[],
        enums: &[],
        constants: &[],
        variables: &[],
        navigators: &[],
        has_routes: &[],
        param_lists: &[],
        workflows: &[],
        workflow_steps: &[],
        call_evidence_sites: &[],
        call_evidence_observations: &[],
        build_configurations: &[],
        semantic_coverage: &[],
        proc_function_joins: &[],
        proc_host_declarations: &[],
        use_full_writers: true,
        files_variant: FilesVariant::WithImports,
    };
    // Phase-02 / R12: capture embedding categories BEFORE write_all consumes.
    let embedding_categories = payload.embedding_categories();
    let counts = writer
        .write_all(&payload)
        .map_err(|e| e.to_string())?;

    if !cleanup_paths.is_empty() {
        // flutter incremental cleanup: xoá node trong cleanup_paths KHÔNG nằm
        // trong keep_ids (node vừa ghi lại trong batch này). Query + params
        // mirror `flutter_analyzer.py::write_graph`.
        writer
            .ensure_schema()
            .map_err(|e| e.to_string())?;
        let keep_ids: Vec<Value> = normalizer::symbol_keep_ids(&batch)
            .into_iter()
            .map(Value::String)
            .collect();
        let mut parameters = BTreeMap::new();
        parameters.insert("project_id".to_string(), json!(facts.header.project_id));
        parameters.insert("paths".to_string(), json!(cleanup_paths));
        parameters.insert("keep_ids".to_string(), Value::Array(keep_ids));
        let query = "MATCH (n {project_id: $project_id}) \
                     WHERE (n.file_path IN $paths OR n.path IN $paths) \
                     AND NOT n.id IN $keep_ids \
                     DETACH DELETE n RETURN count(n) AS count";
        writer
            .store
            .execute_query(query, &parameters, args.neo4j_db.as_deref())
            .map_err(|e| e.to_string())?;
    }
    Ok((counts, embedding_categories))
}

fn run(args: &DartArgs) -> i32 {
    let mode = args.mode();
    if mode == "all" {
        // Entry criterion 3 — quyết định tường minh (phase-02).
        eprintln!(
            "[flutter] ERROR: --mode all is not supported by analyzer-dart; \
             run --mode dart and --mode flutter separately"
        );
        return 2;
    }
    if mode != "dart" && mode != "flutter" {
        eprintln!(
            "[flutter] ERROR: invalid --mode {mode:?}; expected dart or flutter"
        );
        return 2;
    }
    let root = resolved_root(&args.root);
    if !root.is_dir() {
        eprintln!("Root not found: {}", root.display());
        return 2;
    }
    let project_id = args
        .project_id
        .clone()
        .or_else(|| std::env::var("PROJECT_ID").ok().filter(|v| !v.is_empty()))
        .unwrap_or_else(|| {
            root.file_name()
                .map(|name| name.to_string_lossy().to_string())
                .unwrap_or_else(|| root.to_string_lossy().to_string())
        });

    if mode == "flutter" {
        match detector::detect_flutter_project(&root) {
            Ok(Some(project)) => {
                if args.verbose {
                    println!("[flutter] detected project={} evidence={:?}", project.package_name, project.evidence);
                }
            }
            Ok(None) => {
                println!("[flutter] skipped: {} is not a Flutter project", root.display());
                return 0;
            }
            Err(error) => {
                eprintln!("[flutter] ERROR: {error}");
                return 2;
            }
        }
    }

    if args.dry_run {
        let dart_files = count_dart_files(&root);
        println!("Dry run: mode={mode} dart_files={dart_files} root={}", root.display());
        return 0;
    }

    if args.preflight {
        if let Err(error) = dartparse::create_parser() {
            eprintln!("[flutter] ERROR: {error}");
            return 2;
        }
        println!(
            "[flutter] preflight ok runtime=rust parser=tree-sitter-dart/{}",
            dartparse::parser_version()
        );
        return 0;
    }

    // ── Manifests (incremental) ─────────────────────────────────────────
    let changed = if args.incremental {
        match args
            .changed_files_manifest
            .as_deref()
            .map(|path| manifest_paths(path, &root))
            .unwrap_or_else(|| Ok(BTreeSet::new()))
        {
            Ok(paths) => paths,
            Err(error) => {
                eprintln!("[flutter] ERROR: {error}");
                return 2;
            }
        }
    } else {
        BTreeSet::new()
    };
    let deleted = if args.incremental {
        match args
            .deleted_files_manifest
            .as_deref()
            .map(|path| manifest_paths(path, &root))
            .unwrap_or_else(|| Ok(BTreeSet::new()))
        {
            Ok(paths) => paths,
            Err(error) => {
                eprintln!("[flutter] ERROR: {error}");
                return 2;
            }
        }
    } else {
        BTreeSet::new()
    };

    // ── Analysis ────────────────────────────────────────────────────────
    let package_name = match detector::project_package_name(&root) {
        Ok(name) => Some(name),
        Err(error) => {
            eprintln!("[flutter] ERROR: {error}");
            return 2;
        }
    };
    let complete_facts = match dartparse::analyze_project(&root, &project_id, package_name.as_deref(), &mode) {
        Ok(facts) => facts,
        Err(error) => {
            eprintln!("[flutter] ERROR: {error}");
            return 2;
        }
    };

    // ── Dependency cache + impact expansion ─────────────────────────────
    let cache_root = args
        .cache_dir
        .as_ref()
        .map(|dir| expanduser(dir))
        .unwrap_or_else(|| root.join(".cortex").join("flutter"));
    let dependency_cache = cache_root.join(format!("{project_id}-dart-dependencies.json"));
    let mut impacted: BTreeSet<String> = BTreeSet::new();
    if args.incremental {
        let all_files: BTreeSet<String> = dartparse::discover(&root)
            .iter()
            .map(|path| path.to_string_lossy().replace('\\', "/"))
            .collect();
        let dependency_index = if dependency_cache.is_file() && !args.ignore_cache {
            match DependencyIndex::load(&dependency_cache) {
                Ok(index) => Some(index),
                Err(error) => {
                    if args.verbose {
                        eprintln!(
                            "[flutter] WARNING: ignoring dependency cache {}: {error}",
                            dependency_cache.display()
                        );
                    }
                    None
                }
            }
        } else {
            None
        };
        impacted = match dependency_index {
            Some(index) => index.impacted_files(&changed, &deleted),
            None => {
                let mut all = all_files;
                all.extend(deleted.iter().cloned());
                all
            }
        };
    }

    let dependency_index = DependencyIndex::from_facts(&complete_facts);
    let facts = if args.incremental {
        select_incremental_facts(&complete_facts, &impacted)
    } else {
        complete_facts
    };

    // ── Artifact ────────────────────────────────────────────────────────
    let output = args
        .facts_output
        .as_ref()
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            root.join(".cortex")
                .join("flutter")
                .join(format!("{mode}-facts.json"))
        });
    if let Err(error) = write_fact_artifact(&output, &facts) {
        eprintln!("[flutter] ERROR: {error}");
        return 1;
    }

    // ── Graph ───────────────────────────────────────────────────────────
    let cleanup_paths: Vec<String> = if args.incremental {
        impacted.union(&deleted).cloned().collect()
    } else {
        Vec::new()
    };
    let (counts, embedding_categories) = if graph_writes_disabled() {
        (BTreeMap::new(), Vec::new())
    } else {
        match write_graph(args, &facts, &root, &cleanup_paths) {
            Ok(value) => value,
            Err(error) => {
                eprintln!("[flutter] ERROR: graph write failed after staged analysis: {error}");
                return 3;
            }
        }
    };

    // ── Vector plane — orchestrator-level (phase-06); Rust không embed ──
    // Phase-02 / R12: emit EmbeddingInputArtifact (nếu orchestrator yêu cầu).
    let mut vector_count = 0i64;
    let mut vector_status = "disabled";
    if let Some(output) = args.embedding_input_output.as_deref() {
        let repo = args
            .repo
            .clone()
            .filter(|value| !value.is_empty())
            .unwrap_or_else(|| root.to_string_lossy().to_string());
        match embedding_artifact::maybe_emit_embedding_artifact(
            Some(output),
            EmbeddingEmission {
                parser: "dart",
                project_id: &project_id,
                root_scope: &repo,
                full_replace: !args.incremental,
                scanned_directory: true,
                files_selected: changed.iter().cloned().collect(),
                files_deleted: deleted.iter().cloned().collect(),
                categories: embedding_categories,
            },
        ) {
            Ok(Some(path)) => {
                vector_status = "success";
                // Sum documents across categories for the SCAN_RESULT tally.
                vector_count = 0; // orchestrator owns actual qdrant count
                if args.verbose {
                    println!("[embedding] dart artifact written {}", path.display());
                }
            }
            Ok(None) => {
                vector_status = "disabled";
            }
            Err(error) => {
                eprintln!("dart embedding-input artifact failed: {error}");
                return 1;
            }
        }
    }
    if args.message_scan_enabled_by_absence() && args.verbose {
        println!("[message] message scan là plane Python; Rust backend skip (phase 02)");
    }

    // ── Dependency cache update — từ COMPLETE facts (trước selection),
    // đúng thứ tự Python (`DependencyIndex.from_facts(complete_facts)`) ──
    if let Err(error) = dependency_index.save(&dependency_cache) {
        eprintln!("[flutter] WARNING: dependency cache was not updated: {error}");
    }

    let graph_total: i64 = counts.values().sum();
    println!(
        "[SCAN_RESULT] parser={mode} files={} nodes={} edges={} diagnostics={} graph={graph_total} \
         vectors={vector_count} vector_status={vector_status} artifact={}",
        facts.summary.processed_files,
        facts.nodes.len(),
        facts.edges.len(),
        facts.diagnostics.len(),
        output.display(),
    );
    0
}

impl DartArgs {
    /// Message scan "bật" khi không có --disable (như Python default); Rust
    /// chỉ in skip line ở verbose.
    fn message_scan_enabled_by_absence(&self) -> bool {
        !self.disable_message_scan
    }
}

fn main() {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let args = DartArgs::parse_from(&argv);
    std::process::exit(run(&args));
}
