//! Unit tests for the analyzer backend registry: flip matrix, `.exe`
//! probing, map parity, the embedding `force_python` pin, and the overlay
//! `extra_args` carrier (plan 260915-analyzer-layer-rust-cutover phase-01,
//! post-cutover semantics from phase-08).
//!
//! The resolver reads `CORTEX_RUST_ANALYZER` / `CORTEX_RUST_ANALYZER_BIN_DIR`
//! from process env, so every env-touching test holds `ENV_LOCK`.

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, MutexGuard, OnceLock};

use crate::registry::{
    build_analyzer_cmd, framework_rust_binaries, rust_analyzer_binaries, rust_analyzer_binary,
    AnalyzerConfig,
};

fn env_lock() -> MutexGuard<'static, ()> {
    static LOCK: OnceLock<Mutex<()>> = OnceLock::new();
    LOCK.get_or_init(|| Mutex::new(()))
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
}

fn set_env(key: &str, value: Option<&str>) {
    // Tests only; requires the crate-level `cfg_attr(test, allow(unsafe_code))`.
    unsafe {
        match value {
            Some(v) => std::env::set_var(key, v),
            None => std::env::remove_var(key),
        }
    }
}

/// Temporary bin dir with empty stub files; removed on drop.
struct TempBinDir {
    path: PathBuf,
}

impl TempBinDir {
    fn with(files: &[&str]) -> TempBinDir {
        let unique = format!(
            "cortex-sync-registry-test-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_nanos()
        );
        let path = std::env::temp_dir().join(unique);
        std::fs::create_dir_all(&path).expect("create temp bin dir");
        for file in files {
            std::fs::write(path.join(file), b"").expect("write stub binary");
        }
        TempBinDir { path }
    }
}

impl Drop for TempBinDir {
    fn drop(&mut self) {
        if let Ok(entries) = std::fs::read_dir(&self.path) {
            for entry in entries.flatten() {
                let _ = std::fs::remove_file(entry.path());
            }
        }
        let _ = std::fs::remove_dir(&self.path);
    }
}

fn analyzer(parser: &str) -> AnalyzerConfig {
    AnalyzerConfig {
        parser: parser.to_string(),
        script_path: format!("/tools/{parser}/{parser}_analyzer.py"),
        incremental_supported: true,
        extra_args: vec![],
        writes_vectors: false,
        seeded_by: vec![],
        force_python: false,
    }
}

fn assert_vec_str(cmd_args: &[String], expected: &[&str]) {
    let got: Vec<&str> = cmd_args.iter().map(String::as_str).collect();
    assert_eq!(got, expected);
}

#[test]
fn primary_map_covers_25_parsers_with_bin_names() {
    let expected: BTreeSet<&str> = [
        "python", "shell", "ts", "js", "php", "perl", "java", "kotlin", "android", "go",
        "rust", "swift", "delphi", "cobol", "jp1", "vbnet", "vb6", "vba", "vbscript",
        "cplus", "sql", "plsql", "dart", "csharp", "project_topology",
    ]
    .into_iter()
    .collect();
    let map = rust_analyzer_binaries();
    let keys: BTreeSet<&str> = map.keys().copied().collect();
    assert_eq!(keys, expected);
    for (parser, binary) in &map {
        assert!(
            binary.starts_with("analyzer-"),
            "binary '{binary}' should begin with 'analyzer-' for parser '{parser}'"
        );
    }
}

#[test]
fn framework_map_entries_and_shared_database_schema() {
    let map = framework_rust_binaries();
    assert_eq!(map.get("servlet_jsp"), Some(&"analyzer-servlet-jsp"));
    assert_eq!(map.get("database_sql"), Some(&"analyzer-database-schema"));
    assert_eq!(map.get("database_plsql"), Some(&"analyzer-database-schema"));
    assert_eq!(map.get("fastapi_django"), Some(&"analyzer-fastapi-django"));
    assert_eq!(map.get("flutter"), Some(&"analyzer-dart"));
}

#[test]
fn binary_path_probes_bare_then_exe() {
    let dir = TempBinDir::with(&["analyzer-java"]);
    assert_eq!(
        binary_probe(&dir.path, "java"),
        Some(dir.path.join("analyzer-java"))
    );
    let exe_dir = TempBinDir::with(&["analyzer-java.exe"]);
    assert_eq!(
        binary_probe(&exe_dir.path, "java"),
        Some(exe_dir.path.join("analyzer-java.exe"))
    );
    let both = TempBinDir::with(&["analyzer-java", "analyzer-java.exe"]);
    assert_eq!(
        binary_probe(&both.path, "java"),
        Some(both.path.join("analyzer-java"))
    );
    // Empty dir: mapped parser + `=rust` + missing binary is the hard-error
    // cell of the matrix (never a silent None).
    let empty = TempBinDir::with(&[]);
    let _guard = env_lock();
    set_env("CORTEX_RUST_ANALYZER_BIN_DIR", Some(empty.path.to_str().unwrap()));
    set_env("CORTEX_RUST_ANALYZER", Some("rust"));
    let resolved = rust_analyzer_binary(&analyzer("java"));
    set_env("CORTEX_RUST_ANALYZER", None);
    set_env("CORTEX_RUST_ANALYZER_BIN_DIR", None);
    assert!(resolved.is_err(), "missing binary under =rust must hard-error");
}

fn binary_probe(bin_dir: &Path, parser: &str) -> Option<PathBuf> {
    let _guard = env_lock();
    set_env("CORTEX_RUST_ANALYZER_BIN_DIR", Some(bin_dir.to_str().unwrap()));
    set_env("CORTEX_RUST_ANALYZER", Some("rust"));
    let resolved = rust_analyzer_binary(&analyzer(parser));
    set_env("CORTEX_RUST_ANALYZER", None);
    set_env("CORTEX_RUST_ANALYZER_BIN_DIR", None);
    match resolved {
        Ok(Some(path)) => Some(PathBuf::from(path)),
        Ok(None) => None,
        Err(reason) => panic!("binary probe must not hard-error, got: {reason}"),
    }
}

#[test]
fn flip_matrix_unset_resolves_when_binary_present() {
    // Phase-08: unset → auto-flip to Rust; binary present → resolved.
    let _guard = env_lock();
    let dir = TempBinDir::with(&["analyzer-java"]);
    set_env("CORTEX_RUST_ANALYZER_BIN_DIR", Some(dir.path.to_str().unwrap()));
    set_env("CORTEX_RUST_ANALYZER", None);
    let resolved = rust_analyzer_binary(&analyzer("java"));
    set_env("CORTEX_RUST_ANALYZER", None);
    set_env("CORTEX_RUST_ANALYZER_BIN_DIR", None);
    assert_eq!(
        resolved.expect("unset + binary present").as_deref(),
        Some(dir.path.join("analyzer-java").to_str().unwrap())
    );
}

#[test]
fn flip_matrix_unset_missing_binary_is_retire_error() {
    // Phase-08: unset + binary missing → retire error (not silent Python
    // fallback — the Python analyzer scripts are deleted).
    let _guard = env_lock();
    let dir = TempBinDir::with(&[]);
    set_env("CORTEX_RUST_ANALYZER_BIN_DIR", Some(dir.path.to_str().unwrap()));
    set_env("CORTEX_RUST_ANALYZER", None);
    let resolved = rust_analyzer_binary(&analyzer("java"));
    set_env("CORTEX_RUST_ANALYZER", None);
    set_env("CORTEX_RUST_ANALYZER_BIN_DIR", None);
    let message = resolved.expect_err("unset + missing binary must retire-error");
    assert!(message.contains("retired"), "error names the retire condition: {message}");
    assert!(message.contains("rollback"), "error carries the rollback hint: {message}");
}

#[test]
fn flip_matrix_python_and_other_values_are_retire_errors() {
    // Phase-08: `python` and any non-`rust` value → retire error.
    let _guard = env_lock();
    let dir = TempBinDir::with(&["analyzer-java"]);
    set_env("CORTEX_RUST_ANALYZER_BIN_DIR", Some(dir.path.to_str().unwrap()));
    for mode in ["python", "Python", "1", "true", "RUST_AUTO", "rollback"] {
        set_env("CORTEX_RUST_ANALYZER", Some(mode));
        let resolved = rust_analyzer_binary(&analyzer("java"));
        set_env("CORTEX_RUST_ANALYZER", None);
        let message = resolved.unwrap_err_or("mode '{mode}' must retire-error");
        assert!(message.contains("retired"), "mode '{mode}' retire message: {message}");
        assert!(message.contains("rollback"), "mode '{mode}' rollback hint: {message}");
    }
    set_env("CORTEX_RUST_ANALYZER_BIN_DIR", None);
}

#[test]
fn flip_matrix_rust_with_binary_present_resolves() {
    let _guard = env_lock();
    let dir = TempBinDir::with(&["analyzer-java"]);
    set_env("CORTEX_RUST_ANALYZER_BIN_DIR", Some(dir.path.to_str().unwrap()));
    set_env("CORTEX_RUST_ANALYZER", Some("rust"));
    let resolved = rust_analyzer_binary(&analyzer("java"));
    set_env("CORTEX_RUST_ANALYZER", None);
    set_env("CORTEX_RUST_ANALYZER_BIN_DIR", None);
    assert_eq!(
        resolved.expect("binary present").as_deref(),
        Some(dir.path.join("analyzer-java").to_str().unwrap())
    );
}

#[test]
fn flip_matrix_rust_missing_mapped_binary_is_hard_error() {
    // Phase-08: `=rust` + mapped parser + missing binary → hard error
    // (build hint) — operator explicitly opted in, so they own the build.
    let _guard = env_lock();
    let dir = TempBinDir::with(&[]);
    set_env("CORTEX_RUST_ANALYZER_BIN_DIR", Some(dir.path.to_str().unwrap()));
    set_env("CORTEX_RUST_ANALYZER", Some("rust"));
    let resolved = rust_analyzer_binary(&analyzer("java"));
    set_env("CORTEX_RUST_ANALYZER", None);
    set_env("CORTEX_RUST_ANALYZER_BIN_DIR", None);
    let message = resolved.expect_err("mapped parser with missing binary must hard-error");
    assert!(message.contains("analyzer-java"), "error names the missing binary: {message}");
    assert!(message.contains("cargo build"), "error carries the build hint: {message}");
}

#[test]
fn flip_matrix_unmapped_parser_under_rust_is_retire_error() {
    // Phase-08: unmapped parser + `=rust` → retire error (Python scripts are
    // deleted; there is no fallback lane).
    let _guard = env_lock();
    let dir = TempBinDir::with(&[]);
    set_env("CORTEX_RUST_ANALYZER_BIN_DIR", Some(dir.path.to_str().unwrap()));
    set_env("CORTEX_RUST_ANALYZER", Some("rust"));
    let resolved = rust_analyzer_binary(&analyzer("future_parser"));
    set_env("CORTEX_RUST_ANALYZER", None);
    set_env("CORTEX_RUST_ANALYZER_BIN_DIR", None);
    let message = resolved.expect_err("unmapped parser must retire-error post-cutover");
    assert!(message.contains("retired"), "unmapped retire message: {message}");
}

#[test]
fn flip_matrix_rust_project_topology_resolves_analyzer_topology() {
    // Phase-04 wiring: `project_topology` is a real entry in the map (not a
    // framework overlay) so it resolves through `rust_analyzer_binaries()`
    // exactly like the primary parsers above.
    let _guard = env_lock();
    let dir = TempBinDir::with(&["analyzer-topology"]);
    set_env("CORTEX_RUST_ANALYZER_BIN_DIR", Some(dir.path.to_str().unwrap()));
    set_env("CORTEX_RUST_ANALYZER", Some("rust"));
    let resolved = rust_analyzer_binary(&analyzer("project_topology"));
    set_env("CORTEX_RUST_ANALYZER", None);
    set_env("CORTEX_RUST_ANALYZER_BIN_DIR", None);
    assert_eq!(
        resolved.expect("mapped topology binary present").as_deref(),
        Some(dir.path.join("analyzer-topology").to_str().unwrap())
    );
}

#[test]
fn force_python_beats_flip_in_build_cmd() {
    // `force_python` still pins the embedding pass + message lane to Python
    // children — it survives the phase-08 flip so the orchestrator keeps
    // those planes on their proven implementations until they are ported.
    let _guard = env_lock();
    let dir = TempBinDir::with(&["analyzer-java"]);
    set_env("CORTEX_RUST_ANALYZER_BIN_DIR", Some(dir.path.to_str().unwrap()));
    set_env("CORTEX_RUST_ANALYZER", Some("rust"));
    let mut config = analyzer("java");
    config.force_python = true;
    let cmd = build_analyzer_cmd(
        "/venv/bin/python",
        &config,
        "/repo/stock",
        "proj",
        "Proj",
        "before",
        "after",
        None,
        None,
        None,
        false,
        None,
        None,
        false,
        false,
        false,
        None,
        None,
        None,
        None,
        "off",
        None,
        0,
        0,
        0,
        0,
        0,
        &[],
    );
    set_env("CORTEX_RUST_ANALYZER", None);
    set_env("CORTEX_RUST_ANALYZER_BIN_DIR", None);
    assert_eq!(cmd.program, "/venv/bin/python", "embedding pin must keep Python children");
    assert_vec_str(&cmd.args[0..1], &["/tools/java/java_analyzer.py"]);
}

#[test]
fn overlay_extra_args_carried_into_cmd() {
    // Phase-08: unset now auto-flips to the Rust binary. The overlay
    // ``extra_args`` carrier is still verified, just via the Rust backend
    // (the binary stub lives in a temp bin dir so the resolver succeeds).
    let _guard = env_lock();
    let dir = TempBinDir::with(&["analyzer-fastapi-django"]);
    set_env("CORTEX_RUST_ANALYZER_BIN_DIR", Some(dir.path.to_str().unwrap()));
    set_env("CORTEX_RUST_ANALYZER", None);
    let mut config = analyzer("fastapi_django");
    config.extra_args = vec!["--framework", "fastapi_django"];
    let cmd = build_analyzer_cmd(
        "/venv/bin/python",
        &config,
        "/repo/stock",
        "proj",
        "Proj",
        "before",
        "after",
        None,
        None,
        None,
        false,
        None,
        None,
        false,
        false,
        false,
        None,
        None,
        None,
        None,
        "off",
        None,
        0,
        0,
        0,
        0,
        0,
        &[],
    );
    set_env("CORTEX_RUST_ANALYZER", None);
    set_env("CORTEX_RUST_ANALYZER_BIN_DIR", None);
    assert_eq!(
        cmd.program,
        dir.path.join("analyzer-fastapi-django").to_str().unwrap(),
        "post-cutover unset selects the Rust binary"
    );
    let flags: Vec<&str> = cmd.args.iter().map(String::as_str).collect();
    let pos = flags
        .iter()
        .position(|f| *f == "--framework")
        .expect("overlay cmd must carry the required --framework flag");
    assert_eq!(flags[pos + 1], "fastapi_django");
}

#[test]
fn overlay_flip_resolves_rust_binary_with_extra_args() {
    let _guard = env_lock();
    let dir = TempBinDir::with(&["analyzer-fastapi-django"]);
    set_env("CORTEX_RUST_ANALYZER_BIN_DIR", Some(dir.path.to_str().unwrap()));
    set_env("CORTEX_RUST_ANALYZER", Some("rust"));
    let mut config = analyzer("fastapi_django");
    config.extra_args = vec!["--framework", "fastapi_django"];
    let cmd = build_analyzer_cmd(
        "/venv/bin/python",
        &config,
        "/repo/stock",
        "proj",
        "Proj",
        "before",
        "after",
        None,
        None,
        None,
        false,
        None,
        None,
        false,
        false,
        false,
        None,
        None,
        None,
        None,
        "off",
        None,
        0,
        0,
        0,
        0,
        0,
        &[],
    );
    set_env("CORTEX_RUST_ANALYZER", None);
    set_env("CORTEX_RUST_ANALYZER_BIN_DIR", None);
    assert_eq!(
        cmd.program,
        dir.path.join("analyzer-fastapi-django").to_str().unwrap(),
        "framework map must route overlays through the flip matrix"
    );
    let flags: Vec<&str> = cmd.args.iter().map(String::as_str).collect();
    assert!(
        flags.windows(2).any(|w| w[0] == "--framework" && w[1] == "fastapi_django"),
        "rust overlay backend still receives its required extra_args"
    );
}

trait ResultExt<T> {
    fn unwrap_err_or(self, msg: &str) -> String;
}

impl<T> ResultExt<T> for Result<Option<T>, String> {
    fn unwrap_err_or(self, msg: &str) -> String {
        match self {
            Ok(_) => panic!("{msg}: got Ok(_)"),
            Err(e) => e,
        }
    }
}