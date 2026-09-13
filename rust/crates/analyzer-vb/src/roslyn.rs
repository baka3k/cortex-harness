//! Port `vb_roslyn_adapter.py` — plane C# Roslyn giữ nguyên là SUBPROCESS
//! (plan key decision #8: "Không port C# Roslyn — subprocess invoke").
//!
//! Flow mirror: resolve worker .csproj (flag → env → layout repo chuẩn) →
//! `dotnet build -c Release` (cache in-process) → locate DLL theo TFM/runtime
//! đã cài → write manifest JSON tạm → `dotnet <dll> --root ... --files-manifest
//! ... --semantic ...` → parse JSON output
//! `{results: [{file_path, ok, payload, error}], workspace_kind, ...}`.
//!
//! Khác biệt duy nhất: Python default worker project nằm cạnh module
//! (`tools/vb/roslyn_worker/RoslynVbWorker.csproj` — theo `__file__`); Rust
//! resolve theo thứ tự flag → env `VBNET_ROSLYN_WORKER_PROJECT` → layout repo
//! chuẩn tính từ CWD (`code-tiny/tools/vb/roslyn_worker/RoslynVbWorker.csproj`).
//! Không tìm thấy ⇒ Err ⇒ caller fallback regex từng file (đúng semantic
//! batch-error của `_parse_vbnet_with_roslyn_batch`).

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::{LazyLock, Mutex};
use std::time::{Duration, Instant};

use serde_json::Value;

const DEFAULT_WORKER_RELATIVE: &str =
    "code-tiny/tools/vb/roslyn_worker/RoslynVbWorker.csproj";

static BUILD_LOCK: LazyLock<Mutex<()>> = LazyLock::new(|| Mutex::new(()));
static BUILD_CACHE: LazyLock<Mutex<HashMap<PathBuf, PathBuf>>> =
    LazyLock::new(|| Mutex::new(HashMap::new()));

fn normalize_rel(path: &str) -> String {
    path.replace('\\', "/")
}

fn tfm_major(tfm: &str) -> Option<String> {
    static RE: LazyLock<regex::Regex> =
        LazyLock::new(|| regex::Regex::new(r"^net(\d+)").unwrap());
    RE.captures(tfm).map(|m| m[1].to_string())
}

fn installed_dotnet_runtime_majors() -> HashSet<String> {
    let output = match std::process::Command::new("dotnet")
        .arg("--list-runtimes")
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null())
        .output()
    {
        Ok(output) => output,
        Err(_) => return HashSet::new(),
    };
    let stdout = String::from_utf8_lossy(&output.stdout);
    let mut majors = HashSet::new();
    for line in stdout.lines() {
        if !line.starts_with("Microsoft.NETCore.App ") {
            continue;
        }
        let parts: Vec<&str> = line.split_whitespace().collect();
        if parts.len() < 2 {
            continue;
        }
        let major = parts[1].split('.').next().unwrap_or("");
        if !major.is_empty() && major.chars().all(|c| c.is_ascii_digit()) {
            majors.insert(major.to_string());
        }
    }
    majors
}

/// `_worker_dll_path` — prefer net9.0/net8.0, quét TFMs khác (reverse sort),
/// chọn DLL khớp runtime major đã cài.
fn worker_dll_path(project_path: &Path) -> PathBuf {
    let project_dir = project_path.parent().unwrap_or(Path::new("."));
    let project_name = project_path
        .file_stem()
        .map(|s| s.to_string_lossy().to_string())
        .unwrap_or_default();
    let release_dir = project_dir.join("bin").join("Release");
    let mut existing: Vec<PathBuf> = vec![
        release_dir.join("net9.0").join(format!("{project_name}.dll")),
        release_dir.join("net8.0").join(format!("{project_name}.dll")),
    ]
    .into_iter()
    .filter(|p| p.exists())
    .collect();
    if existing.is_empty() && release_dir.is_dir() {
        let mut tfms: Vec<String> = match std::fs::read_dir(&release_dir) {
            Ok(entries) => entries
                .flatten()
                .map(|e| e.file_name().to_string_lossy().to_string())
                .collect(),
            Err(_) => Vec::new(),
        };
        tfms.sort();
        tfms.reverse();
        for tfm in tfms {
            if !tfm.starts_with("net") {
                continue;
            }
            let candidate = release_dir.join(&tfm).join(format!("{project_name}.dll"));
            if candidate.exists() {
                existing.push(candidate);
            }
        }
    }
    if existing.is_empty() {
        return release_dir
            .join("net9.0")
            .join(format!("{project_name}.dll"));
    }
    let installed = installed_dotnet_runtime_majors();
    for path in &existing {
        let tfm = path
            .parent()
            .map(|p| p.file_name().map(|n| n.to_string_lossy().to_string()))
            .unwrap_or_default()
            .unwrap_or_default();
        if let Some(major) = tfm_major(&tfm)
            && installed.contains(&major)
        {
            return path.clone();
        }
    }
    existing[0].clone()
}

/// `_ensure_worker_built` — dotnet build (bỏ qua nếu DLL đã có trong cache
/// in-process); raise khi build fail hoặc DLL không xuất hiện.
fn ensure_worker_built(project_path: &Path, verbose: bool) -> Result<PathBuf, String> {
    let project_path = cortex_analyzer_framework::cli::abs_root(&project_path.to_string_lossy());
    let _guard = BUILD_LOCK.lock().unwrap();
    if let Some(cached) = BUILD_CACHE.lock().unwrap().get(&project_path)
        && cached.exists()
    {
        return Ok(cached.clone());
    }

    let output = std::process::Command::new("dotnet")
        .arg("build")
        .arg(&project_path)
        .arg("-c")
        .arg("Release")
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .output()
        .map_err(|e| format!("roslyn worker build failed (spawn): {e}"))?;
    if !output.status.success() {
        let mut combined = String::from_utf8_lossy(&output.stdout).to_string();
        combined.push('\n');
        combined.push_str(&String::from_utf8_lossy(&output.stderr));
        let tail: Vec<&str> = combined.lines().collect::<Vec<_>>();
        let start = tail.len().saturating_sub(50);
        let tail_text = tail[start..].join("\n");
        return Err(format!(
            "roslyn worker build failed ({})\n{tail_text}",
            output.status.code().unwrap_or(-1)
        ));
    }

    let dll = worker_dll_path(&project_path);
    if !dll.exists() {
        return Err(format!(
            "roslyn worker dll not found after build: {}",
            dll.display()
        ));
    }
    BUILD_CACHE
        .lock()
        .unwrap()
        .insert(project_path.clone(), dll.clone());
    if verbose {
        println!("[roslyn] worker built: {}", dll.display());
    }
    Ok(dll)
}

/// Kết quả batch worker: (payload_by_rel, errors_by_rel, worker_meta).
pub type WorkerOutcome = (
    HashMap<String, Value>,
    HashMap<String, String>,
    WorkerMeta,
);

#[derive(Debug, Clone, Default)]
pub struct WorkerMeta {
    pub workspace_kind: String,
    pub solution_or_project_path: String,
    pub semantic_enabled: bool,
    pub semantic_errors: Vec<String>,
}

/// Resolve worker .csproj — flag → env → layout repo chuẩn từ CWD.
fn resolve_worker_project(explicit: Option<&str>) -> Result<PathBuf, String> {
    if let Some(path) = explicit
        && !path.is_empty()
    {
        return Ok(PathBuf::from(path));
    }
    if let Ok(env_path) = std::env::var("VBNET_ROSLYN_WORKER_PROJECT")
        && !env_path.is_empty()
    {
        return Ok(PathBuf::from(env_path));
    }
    let candidate = std::env::current_dir()
        .unwrap_or_else(|_| PathBuf::from("."))
        .join(DEFAULT_WORKER_RELATIVE);
    if candidate.exists() {
        return Ok(candidate);
    }
    Err(format!(
        "roslyn worker project not found (tried {DEFAULT_WORKER_RELATIVE} từ CWD; \
         dùng --vbnet-roslyn-worker-project hoặc env VBNET_ROSLYN_WORKER_PROJECT)"
    ))
}

/// `parse_vbnet_files_with_roslyn` — chạy worker cho batch file; trả
/// (payload_by_rel, errors_by_rel, worker_meta).
#[allow(clippy::too_many_arguments)]
pub fn parse_vbnet_files_with_roslyn(
    root: &str,
    files: &[PathBuf],
    semantic_mode: &str,
    worker_project_path: Option<&str>,
    timeout_sec: f64,
    workspace_timeout_ms: i64,
    file_timeout_ms: i64,
    parse_cache_version: &str,
    verbose: bool,
) -> Result<WorkerOutcome, String> {
    if files.is_empty() {
        return Ok((HashMap::new(), HashMap::new(), WorkerMeta::default()));
    }

    // root_abs = realpath(abspath(root))
    let root_abs = {
        let abs = cortex_analyzer_framework::cli::abs_root(root);
        std::fs::canonicalize(&abs).unwrap_or(abs)
    };

    let mut rel_files: Vec<String> = Vec::with_capacity(files.len());
    for path in files {
        let abs = {
            let abs_path = if path.is_absolute() {
                path.clone()
            } else {
                std::env::current_dir()
                    .unwrap_or_else(|_| PathBuf::from("."))
                    .join(path)
            };
            std::fs::canonicalize(&abs_path).unwrap_or(abs_path)
        };
        let rel = match abs.strip_prefix(&root_abs) {
            Ok(rest) => rest.to_string_lossy().to_string(),
            Err(_) => abs.to_string_lossy().to_string(),
        };
        rel_files.push(normalize_rel(&rel));
    }

    let project_path = resolve_worker_project(worker_project_path)?;
    let dll_path = ensure_worker_built(&project_path, verbose)?;

    let manifest_file = std::env::temp_dir().join(format!(
        "vb_roslyn_manifest_{}_{}.json",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos()
    ));
    std::fs::write(
        &manifest_file,
        serde_json::to_string(&serde_json::json!({ "files": rel_files })).unwrap_or_default(),
    )
    .map_err(|e| format!("cannot write roslyn manifest: {e}"))?;

    let run = || -> Result<WorkerOutcome, String> {
        let cmd = [
            "dotnet".to_string(),
            dll_path.to_string_lossy().to_string(),
            "--root".to_string(),
            root_abs.to_string_lossy().to_string(),
            "--files-manifest".to_string(),
            manifest_file.to_string_lossy().to_string(),
            "--semantic".to_string(),
            semantic_mode.to_string(),
            "--workspace-timeout-ms".to_string(),
            workspace_timeout_ms.max(5000).to_string(),
            "--file-timeout-ms".to_string(),
            file_timeout_ms.max(5000).to_string(),
            "--parse-cache-version".to_string(),
            parse_cache_version.to_string(),
        ];

        let mut child = std::process::Command::new(&cmd[0])
            .args(&cmd[1..])
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .spawn()
            .map_err(|e| format!("roslyn worker spawn failed: {e}"))?;

        // Drain stdout/stderr trên thread riêng (tránh deadlock pipe) rồi
        // join với timeout polling.
        let mut stdout_pipe = child.stdout.take().expect("stdout piped");
        let mut stderr_pipe = child.stderr.take().expect("stderr piped");
        let stdout_handle = std::thread::spawn(move || {
            use std::io::Read;
            let mut buf = String::new();
            let _ = stdout_pipe.read_to_string(&mut buf);
            buf
        });
        let stderr_handle = std::thread::spawn(move || {
            use std::io::Read;
            let mut buf = String::new();
            let _ = stderr_pipe.read_to_string(&mut buf);
            buf
        });

        let deadline = Duration::from_secs_f64(timeout_sec.max(1.0));
        let start = Instant::now();
        let status = loop {
            match child.try_wait() {
                Ok(Some(status)) => break Some(status),
                Ok(None) => {
                    if start.elapsed() >= deadline {
                        let _ = child.kill();
                        let _ = child.wait();
                        break None;
                    }
                    std::thread::sleep(Duration::from_millis(50));
                }
                Err(e) => return Err(format!("roslyn worker wait failed: {e}")),
            }
        };

        let stdout = stdout_handle.join().unwrap_or_default();
        let stderr = stderr_handle.join().unwrap_or_default();

        let status = match status {
            Some(status) => status,
            None => return Err(format!("roslyn worker timed out after {timeout_sec}s")),
        };

        if !status.success() {
            let stderr_tail: String = {
                let lines: Vec<&str> = stderr.lines().collect();
                let start = lines.len().saturating_sub(80);
                lines[start..].join("\n")
            };
            let stdout_tail: String = {
                let lines: Vec<&str> = stdout.lines().collect();
                let start = lines.len().saturating_sub(80);
                lines[start..].join("\n")
            };
            return Err(format!(
                "roslyn worker execution failed (code={})\nSTDERR:\n{stderr_tail}\nSTDOUT:\n{stdout_tail}",
                status.code().unwrap_or(-1)
            ));
        }

        let data: Value = serde_json::from_str(stdout.trim()).map_err(|e| {
            let snippet: String = stdout.chars().take(2000).collect();
            format!("invalid roslyn worker json output: {e}\n{snippet}")
        })?;

        let mut success: HashMap<String, Value> = HashMap::new();
        let mut errors: HashMap<String, String> = HashMap::new();
        if let Some(results) = data.get("results").and_then(Value::as_array) {
            for item in results {
                let rel = normalize_rel(
                    item.get("file_path")
                        .and_then(Value::as_str)
                        .unwrap_or("")
                        .trim(),
                );
                let ok = item.get("ok").and_then(Value::as_bool).unwrap_or(false);
                let payload = item.get("payload");
                if ok && payload.map(Value::is_object).unwrap_or(false) {
                    success.insert(rel.clone(), payload.unwrap().clone());
                } else if !rel.is_empty() {
                    let error = item
                        .get("error")
                        .and_then(Value::as_str)
                        .unwrap_or("roslyn parse failed");
                    errors.insert(rel, error.to_string());
                }
            }
        }

        let meta = WorkerMeta {
            workspace_kind: data
                .get("workspace_kind")
                .and_then(Value::as_str)
                .filter(|s| !s.is_empty())
                .unwrap_or("none")
                .to_string(),
            solution_or_project_path: data
                .get("solution_or_project_path")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string(),
            semantic_enabled: data
                .get("semantic_enabled")
                .and_then(Value::as_bool)
                .unwrap_or(false),
            semantic_errors: data
                .get("semantic_errors")
                .and_then(Value::as_array)
                .map(|items| {
                    items
                        .iter()
                        .map(|v| match v {
                            Value::String(s) => s.clone(),
                            other => other.to_string(),
                        })
                        .collect()
                })
                .unwrap_or_default(),
        };
        Ok((success, errors, meta))
    };

    let result = run();
    let _ = std::fs::remove_file(&manifest_file);
    result
}
