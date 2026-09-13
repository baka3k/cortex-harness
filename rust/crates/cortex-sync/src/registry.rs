//! Analyzer registry port: `ANALYZERS`, `FRAMEWORK_ANALYZERS`,
//! `PROJECT_TOPOLOGY_ANALYZER`, `_RUST_ANALYZER_BINARIES`/`_rust_analyzer_binary`,
//! `_build_analyzer_cmd`, collection naming, and `_selected_parsers`.

use std::collections::{BTreeMap, BTreeSet};
use std::path::PathBuf;

use crate::util;

/// Repo root (parent of `code-tiny`), derived from the executable location:
/// `<repo>/rust/target/release/cortex-sync`. Overridable via
/// `CORTEX_REPO_ROOT` for non-standard target dirs.
pub fn repo_root() -> PathBuf {
    if let Ok(from_env) = std::env::var("CORTEX_REPO_ROOT")
        && !from_env.trim().is_empty() {
            return PathBuf::from(from_env);
        }
    if let Ok(exe) = std::env::current_exe() {
        // exe = <repo>/rust/target/{release,debug}/cortex-sync
        if let Some(repo) = exe.parent().and_then(|p| p.parent()).and_then(|p| p.parent()).and_then(|p| p.parent())
            && repo.join("code-tiny/tools/sync/incremental_sync.py").is_file() {
                return repo.to_path_buf();
            }
    }
    PathBuf::from(".")
}

fn code_tiny_tools(name: &str) -> String {
    repo_root().join("code-tiny/tools").join(name).to_string_lossy().to_string()
}

#[derive(Debug, Clone)]
#[allow(dead_code)]
pub struct AnalyzerConfig {
    pub parser: String,
    pub script_path: String,
    pub incremental_supported: bool,
    pub extra_args: Vec<&'static str>,
    pub writes_vectors: bool,
    pub seeded_by: Vec<&'static str>,
}

impl AnalyzerConfig {
    fn new(parser: &'static str, script: String, incremental: bool) -> AnalyzerConfig {
        AnalyzerConfig {
            parser: parser.to_string(),
            script_path: script,
            incremental_supported: incremental,
            extra_args: vec![],
            writes_vectors: true,
            seeded_by: vec![],
        }
    }

    /// `_resolve_ts_analyzer`-style dynamic script resolution.
    pub fn with_script(parser: &str, script: &str) -> AnalyzerConfig {
        AnalyzerConfig {
            parser: parser.to_string(),
            script_path: script.to_string(),
            incremental_supported: true,
            extra_args: vec![],
            writes_vectors: true,
            seeded_by: vec![],
        }
    }
}

#[derive(Debug, Clone)]
#[allow(dead_code)]
pub struct FrameworkAnalyzerConfig {
    pub framework: &'static str,
    pub script_path: String,
    pub incremental_supported: bool,
    pub prerequisite_parsers: Vec<&'static str>,
    pub order: i64,
    pub writes_vectors: bool,
    pub extra_args: Vec<&'static str>,
}

pub fn analyzers() -> BTreeMap<&'static str, AnalyzerConfig> {
    // BTreeMap: iteration order is insertion order in Python; the parser loop
    // order below is pinned via `PARSER_ITERATION_ORDER`.
    let mut map: BTreeMap<&'static str, AnalyzerConfig> = BTreeMap::new();
    let entries: Vec<(&'static str, AnalyzerConfig)> = vec![
        ("cobol", AnalyzerConfig::new("cobol", code_tiny_tools("cobol/cobol_analyzer.py"), true)),
        (
            "dart",
            AnalyzerConfig {
                extra_args: vec!["--mode", "dart"],
                ..AnalyzerConfig::new("dart", code_tiny_tools("flutter/flutter_analyzer.py"), true)
            },
        ),
        ("cplus", AnalyzerConfig::new("cplus", code_tiny_tools("cplus/cplus_analyzer.py"), true)),
        ("delphi", AnalyzerConfig::new("delphi", code_tiny_tools("delphi/delphi_analyzer.py"), true)),
        ("java", AnalyzerConfig::new("java", code_tiny_tools("java/java_analyzer.py"), true)),
        ("kotlin", AnalyzerConfig::new("kotlin", code_tiny_tools("kotlin/kotlin_analyzer.py"), true)),
        ("android", AnalyzerConfig::new("android", code_tiny_tools("android/android_kotlin_analyzer.py"), true)),
        ("vbnet", AnalyzerConfig::new("vbnet", code_tiny_tools("vb/vbnet_analyzer.py"), true)),
        ("vb6", AnalyzerConfig::new("vb6", code_tiny_tools("vb/vb6_analyzer.py"), true)),
        ("vba", AnalyzerConfig::new("vba", code_tiny_tools("vb/vba_analyzer.py"), true)),
        ("vbscript", AnalyzerConfig::new("vbscript", code_tiny_tools("vb/vbscript_analyzer.py"), true)),
        ("python", AnalyzerConfig::new("python", code_tiny_tools("python/python_analyzer.py"), true)),
        ("go", AnalyzerConfig::new("go", code_tiny_tools("go/go_analyzer.py"), true)),
        ("perl", AnalyzerConfig::new("perl", code_tiny_tools("perl/perl_analyzer.py"), true)),
        ("shell", AnalyzerConfig::new("shell", code_tiny_tools("shell/shell_analyzer.py"), true)),
        ("jp1", AnalyzerConfig::new("jp1", code_tiny_tools("jp1/jp1_analyzer.py"), true)),
        ("rust", AnalyzerConfig::new("rust", code_tiny_tools("rust/rust_analyzer.py"), true)),
        ("swift", AnalyzerConfig::new("swift", code_tiny_tools("swift/swift_analyzer.py"), true)),
        ("js", AnalyzerConfig::new("js", code_tiny_tools("js/js_analyzer.py"), true)),
        ("ts", AnalyzerConfig::new("ts", code_tiny_tools("ts/ts_analyzer.py"), true)),
        ("php", AnalyzerConfig::new("php", code_tiny_tools("php/php_analyzer.py"), true)),
        ("csharp", AnalyzerConfig::new("csharp", code_tiny_tools("csharp/csharp_analyzer.py"), true)),
        ("sql", AnalyzerConfig::new("sql", code_tiny_tools("sql/sql_analyzer.py"), true)),
        ("plsql", AnalyzerConfig::new("plsql", code_tiny_tools("plsql/plsql_analyzer.py"), true)),
    ];
    for (name, config) in entries {
        map.insert(name, config);
    }
    map
}

/// Python dict insertion order of `ANALYZERS` — the parent iterates it in
/// this order when launching primary parsers.
pub const PARSER_ITERATION_ORDER: [&str; 24] = [
    "cobol", "dart", "cplus", "delphi", "java", "kotlin", "android", "vbnet", "vb6", "vba",
    "vbscript", "python", "go", "perl", "shell", "jp1", "rust", "swift", "js", "ts", "php",
    "csharp", "sql", "plsql",
];

pub fn project_topology_analyzer() -> AnalyzerConfig {
    AnalyzerConfig {
        writes_vectors: false,
        ..AnalyzerConfig::new("project_topology", code_tiny_tools("project_topology/topology_analyzer.py"), true)
    }
}

pub fn framework_analyzers() -> BTreeMap<&'static str, FrameworkAnalyzerConfig> {
    let mut map: BTreeMap<&'static str, FrameworkAnalyzerConfig> = BTreeMap::new();
    let entries = vec![
        FrameworkAnalyzerConfig {
            framework: "spring",
            script_path: code_tiny_tools("spring/spring_analyzer.py"),
            incremental_supported: true,
            prerequisite_parsers: vec!["java", "kotlin"],
            order: 10,
            writes_vectors: false,
            extra_args: vec![],
        },
        FrameworkAnalyzerConfig {
            framework: "servlet_jsp",
            script_path: code_tiny_tools("servlet_jsp/servlet_jsp_analyzer.py"),
            incremental_supported: true,
            prerequisite_parsers: vec!["java"],
            order: 20,
            writes_vectors: false,
            extra_args: vec![],
        },
        FrameworkAnalyzerConfig {
            framework: "mybatis",
            script_path: code_tiny_tools("mybatis/mybatis_analyzer.py"),
            incremental_supported: true,
            prerequisite_parsers: vec!["java", "kotlin"],
            order: 30,
            writes_vectors: false,
            extra_args: vec![],
        },
        FrameworkAnalyzerConfig {
            framework: "struts",
            script_path: code_tiny_tools("struts/struts_analyzer.py"),
            incremental_supported: true,
            prerequisite_parsers: vec!["java"],
            order: 40,
            writes_vectors: false,
            extra_args: vec![],
        },
        FrameworkAnalyzerConfig {
            framework: "flutter",
            script_path: code_tiny_tools("flutter/flutter_analyzer.py"),
            incremental_supported: true,
            prerequisite_parsers: vec!["dart"],
            order: 50,
            writes_vectors: false,
            extra_args: vec!["--mode", "flutter"],
        },
        FrameworkAnalyzerConfig {
            framework: "aspnet_framework",
            script_path: code_tiny_tools("aspnet_framework/aspnet_framework_analyzer.py"),
            incremental_supported: true,
            prerequisite_parsers: vec!["csharp"],
            order: 60,
            writes_vectors: false,
            extra_args: vec![],
        },
        FrameworkAnalyzerConfig {
            framework: "aspnet_core",
            script_path: code_tiny_tools("aspnet_core/aspnet_core_analyzer.py"),
            incremental_supported: true,
            prerequisite_parsers: vec!["csharp"],
            order: 70,
            writes_vectors: false,
            extra_args: vec![],
        },
        FrameworkAnalyzerConfig {
            framework: "fastapi_django",
            script_path: code_tiny_tools("web_framework/web_framework_analyzer.py"),
            incremental_supported: true,
            prerequisite_parsers: vec!["python"],
            order: 80,
            writes_vectors: false,
            extra_args: vec!["--framework", "fastapi_django"],
        },
        FrameworkAnalyzerConfig {
            framework: "express_js",
            script_path: code_tiny_tools("web_framework/web_framework_analyzer.py"),
            incremental_supported: true,
            prerequisite_parsers: vec!["js"],
            order: 81,
            writes_vectors: false,
            extra_args: vec!["--framework", "express_js"],
        },
        FrameworkAnalyzerConfig {
            framework: "laravel",
            script_path: code_tiny_tools("web_framework/web_framework_analyzer.py"),
            incremental_supported: true,
            prerequisite_parsers: vec!["php"],
            order: 82,
            writes_vectors: false,
            extra_args: vec!["--framework", "laravel"],
        },
        FrameworkAnalyzerConfig {
            framework: "database_sql",
            script_path: code_tiny_tools("database_schema/database_schema_analyzer.py"),
            incremental_supported: true,
            prerequisite_parsers: vec!["sql"],
            order: 90,
            writes_vectors: false,
            extra_args: vec!["--dialect", "sql"],
        },
        FrameworkAnalyzerConfig {
            framework: "database_plsql",
            script_path: code_tiny_tools("database_schema/database_schema_analyzer.py"),
            incremental_supported: true,
            prerequisite_parsers: vec!["plsql"],
            order: 91,
            writes_vectors: false,
            extra_args: vec!["--dialect", "plsql"],
        },
    ];
    for config in entries {
        map.insert(config.framework, config);
    }
    map
}

/// Parsers with a Rust analyzer port (phase 04–06) — mirrors the live
/// `_RUST_ANALYZER_BINARIES` map in incremental_sync.py.
pub fn rust_analyzer_binaries() -> BTreeMap<&'static str, &'static str> {
    BTreeMap::from([
        ("python", "analyzer-python"),
        ("shell", "analyzer-shell"),
        ("ts", "analyzer-ts"),
        ("js", "analyzer-js"),
        ("php", "analyzer-php"),
        ("perl", "analyzer-perl"),
        ("java", "analyzer-java"),
    ])
}

/// `_rust_analyzer_binary` — when `CORTEX_RUST_ANALYZER=rust` is set, resolve
/// the Rust binary for ported parsers under `rust/target/release` (or
/// `CORTEX_RUST_ANALYZER_BIN_DIR`). Missing binary ⇒ None (Python fallback).
pub fn rust_analyzer_binary(analyzer: &AnalyzerConfig) -> Option<String> {
    let mode = std::env::var("CORTEX_RUST_ANALYZER").unwrap_or_default();
    if mode.trim().to_lowercase() != "rust" {
        return None;
    }
    let binaries = rust_analyzer_binaries();
    let binary_name = binaries.get(analyzer.parser.as_str())?;
    let bin_dir = std::env::var("CORTEX_RUST_ANALYZER_BIN_DIR")
        .ok()
        .filter(|v| !v.trim().is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| repo_root().join("rust").join("target").join("release"));
    let candidate = bin_dir.join(binary_name);
    if candidate.is_file() {
        Some(candidate.to_string_lossy().to_string())
    } else {
        None
    }
}

pub const SHARED_VECTOR_CLI_PARSERS: [&str; 7] =
    ["dart", "go", "jp1", "perl", "rust", "shell", "swift"];

pub fn message_enabled_parsers() -> BTreeSet<&'static str> {
    BTreeSet::from([
        "cplus", "delphi", "java", "csharp", "kotlin", "android", "vbnet", "vb6", "vba",
        "vbscript", "python", "swift", "js", "ts", "php", "sql", "plsql",
    ])
}

pub fn selected_parsers(parsers_arg: &str) -> Result<(BTreeSet<String>, bool), String> {
    let text = parsers_arg.trim().to_lowercase();
    let all: BTreeSet<String> = analyzers()
        .keys()
        .map(|k| k.to_string())
        .chain(framework_analyzers().keys().map(|k| k.to_string()))
        .chain(std::iter::once("project_topology".to_string()))
        .collect();
    if text == "auto" {
        return Ok((all, true));
    }
    let mut values: BTreeSet<String> = text
        .split(',')
        .map(|item| item.trim().to_string())
        .filter(|item| !item.is_empty())
        .collect();
    let unsupported: Vec<String> = values.difference(&all).cloned().collect();
    if !unsupported.is_empty() {
        return Err(format!("Unsupported parser(s): {}", unsupported.join(", ")));
    }
    let frameworks = framework_analyzers();
    let framework_names: BTreeSet<String> = frameworks.keys().map(|k| k.to_string()).collect();
    let framework_hits: Vec<String> = values.intersection(&framework_names).cloned().collect();
    for framework in framework_hits {
        if let Some(config) = frameworks.get(framework.as_str()) {
            for prerequisite in &config.prerequisite_parsers {
                values.insert(prerequisite.to_string());
            }
        }
    }
    Ok((values, false))
}

const ROOT_HASH_LEN: usize = 10;

#[allow(dead_code)]
fn per_project_scheme_active() -> bool {
    std::env::var("HYPERPACK_COLLECTION_SCHEME")
        .unwrap_or_default()
        .trim()
        .eq_ignore_ascii_case("per_project")
}

fn root_hash(root: &std::path::Path) -> String {
    let real = util::realpath(&util::path_to_string(root));
    util::sha1_hex(util::path_to_string(&real).as_bytes())[..ROOT_HASH_LEN].to_string()
}

/// `_code_collection_name` — legacy scheme only in the native path
/// (`HYPERPACK_COLLECTION_SCHEME=per_project` delegates to the Python
/// orchestrator before this is reached).
pub fn code_collection_name(project_id: &str, root: &std::path::Path, parser: &str) -> String {
    let digest = root_hash(root);
    let parser_token = util::safe_segment(parser);
    let scope = format!("{}_{}", util::safe_segment(project_id), digest);
    let name = format!("{}__{}_functions", scope, parser_token);
    name.chars().take(255).collect()
}

/// `_message_collection_name` — legacy scheme.
pub fn message_collection_name(project_id: &str) -> String {
    let name = format!("{}_mess", util::safe_segment(project_id));
    name.chars().take(255).collect()
}

/// `_repository_name` — the preflight/analyzer-shared repository identity.
pub fn repository_name(project_name: &str, root: &std::path::Path) -> String {
    let base = root
        .file_name()
        .map(|n| n.to_string_lossy().to_string())
        .unwrap_or_else(|| util::path_to_string(root));
    format!("{project_name}/{base}")
}

#[allow(dead_code)]
pub struct BuiltCmd {
    pub program: String,
    pub args: Vec<String>,
}

/// `_build_analyzer_cmd` — identical flag contract for both backends; the
/// Rust backend swap keeps every flag byte-for-byte.
#[allow(clippy::too_many_arguments)]
pub fn build_analyzer_cmd(
    python_bin: &str,
    analyzer: &AnalyzerConfig,
    root: &str,
    project_id: &str,
    project_name: &str,
    before_sha: &str,
    after_sha: &str,
    changed_manifest: Option<&str>,
    deleted_manifest: Option<&str>,
    qdrant_collection: Option<&str>,
    message_scan_enabled: bool,
    message_output_dir: Option<&str>,
    message_qdrant_collection: Option<&str>,
    incremental: bool,
    verbose: bool,
    ignore_cache: bool,
    embed_model: Option<&str>,
    embed_device: Option<&str>,
    embed_batch_size: Option<i64>,
    max_embed_chars: Option<i64>,
    parse_quality: &str,
    parse_quality_report: Option<&str>,
    parse_quality_max_files: i64,
    parse_quality_wall_seconds: i64,
    parse_quality_workers: i64,
    parse_quality_max_records: i64,
    parse_quality_max_bytes: i64,
    graph_target_args: &[String],
) -> BuiltCmd {
    let rust_binary = rust_analyzer_binary(analyzer);
    let mut cmd: Vec<String> = Vec::new();
    let program = match &rust_binary {
        Some(binary) => binary.clone(),
        None => {
            cmd.push(analyzer.script_path.clone());
            python_bin.to_string()
        }
    };
    cmd.extend([
        "--root".to_string(),
        root.to_string(),
        "--project-id".to_string(),
        project_id.to_string(),
        "--project-name".to_string(),
        project_name.to_string(),
        "--commit-sha-before".to_string(),
        before_sha.to_string(),
        "--commit-sha-after".to_string(),
        after_sha.to_string(),
    ]);
    cmd.extend(analyzer.extra_args.iter().map(|s| s.to_string()));
    cmd.extend(graph_target_args.iter().cloned());
    if analyzer.parser == "shell" {
        let mapping_ledger = std::env::var("SHELL_PROGRAM_MAPPING_LEDGER")
            .unwrap_or_default()
            .trim()
            .to_string();
        if !mapping_ledger.is_empty() {
            cmd.push("--program-mapping-ledger".to_string());
            cmd.push(mapping_ledger);
        }
    }
    if analyzer.parser == "cplus" {
        cmd.push("--repo".to_string());
        cmd.push(repository_name(project_name, std::path::Path::new(root)));
        cmd.push("--parse-quality".to_string());
        cmd.push(parse_quality.to_string());
        if parse_quality == "report" || parse_quality == "repair" {
            cmd.push("--disable-compile-db-bootstrap".to_string());
        }
        if let Some(report) = parse_quality_report {
            cmd.push("--parse-quality-report".to_string());
            cmd.push(report.to_string());
        }
        cmd.extend([
            "--parse-quality-max-files".to_string(),
            parse_quality_max_files.to_string(),
            "--parse-quality-wall-seconds".to_string(),
            parse_quality_wall_seconds.to_string(),
            "--parse-quality-workers".to_string(),
            parse_quality_workers.to_string(),
            "--parse-quality-max-records".to_string(),
            parse_quality_max_records.to_string(),
            "--parse-quality-max-bytes".to_string(),
            parse_quality_max_bytes.to_string(),
        ]);
    }
    if SHARED_VECTOR_CLI_PARSERS.contains(&analyzer.parser.as_str()) {
        if let Some(model) = embed_model {
            cmd.push("--embed-model".to_string());
            cmd.push(model.to_string());
        }
        if let Some(device) = embed_device {
            cmd.push("--device".to_string());
            cmd.push(device.to_string());
        }
        if let Some(batch) = embed_batch_size {
            cmd.push("--batch-size".to_string());
            cmd.push(batch.to_string());
        }
        if let Some(chars) = max_embed_chars {
            cmd.push("--max-embed-chars".to_string());
            cmd.push(chars.to_string());
        }
    }
    if let Some(collection) = qdrant_collection {
        cmd.push("--qdrant-collection".to_string());
        cmd.push(collection.to_string());
    }
    if incremental {
        cmd.push("--incremental".to_string());
        if let Some(manifest) = changed_manifest {
            cmd.push("--changed-files-manifest".to_string());
            cmd.push(manifest.to_string());
        }
        if let Some(manifest) = deleted_manifest {
            cmd.push("--deleted-files-manifest".to_string());
            cmd.push(manifest.to_string());
        }
    }
    if ignore_cache {
        cmd.push("--ignore-cache".to_string());
    }
    if message_scan_enabled {
        cmd.push("--enable-message-scan".to_string());
        if let Some(dir) = message_output_dir {
            cmd.push("--message-output-dir".to_string());
            cmd.push(dir.to_string());
        }
        if let Some(collection) = message_qdrant_collection {
            cmd.push("--message-qdrant-collection".to_string());
            cmd.push(collection.to_string());
        }
    } else {
        cmd.push("--disable-message-scan".to_string());
    }
    if verbose {
        cmd.push("--verbose".to_string());
    }
    BuiltCmd { program, args: cmd }
}
