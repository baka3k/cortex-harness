//! Port `tools/common/aspnet/roslyn_adapter.py` — gọi ASP.NET Roslyn worker
//! (dotnet) GIỐNG HỆT phía Python: build worker, chọn dll theo mtime/runtime,
//! gửi request manifest JSON, đọc payload. Evidence do cùng worker sinh nên
//! byte-identical giữa 2 backend.

use std::path::{Path, PathBuf};
use std::sync::Mutex;

use serde_json::{json, Value};

use crate::aspnet::models::ASPNET_PROTOCOL_VERSION;
use crate::aspnet::safe_formats::resolve_inside_root;
use crate::pyutil::{normalize_relative_path, realpath};

const DEFAULT_TIMEOUT_SEC: f64 = 600.0;
const DEFAULT_WORKSPACE_TIMEOUT_MS: i64 = 120_000;
const DEFAULT_FILE_TIMEOUT_MS: i64 = 60_000;
const DEFAULT_MAX_FILE_BYTES: usize = 2 * 1024 * 1024;

/// `_BUILD_CACHE` — per-process (mỗi lần chạy binary là 1 process như Python).
static BUILD_CACHE: Mutex<Vec<String>> = Mutex::new(Vec::new());

/// `default_worker_project()` — đặt cạnh roslyn_worker của repo code-tiny.
pub fn default_worker_project() -> PathBuf {
    PathBuf::from(
        "code-tiny/tools/common/aspnet/roslyn_worker/AspNetRoslynWorker.csproj",
    )
}

fn runtime_majors() -> Vec<String> {
    let output = match std::process::Command::new("dotnet")
        .arg("--list-runtimes")
        .output()
    {
        Ok(output) => output,
        Err(_) => return Vec::new(),
    };
    let stdout = String::from_utf8_lossy(&output.stdout);
    let mut majors = Vec::new();
    for line in stdout.lines() {
        if let Some(rest) = line.strip_prefix("Microsoft.NETCore.App ") {
            let major: String = rest
                .chars()
                .take_while(|character| character.is_ascii_digit())
                .collect();
            if !major.is_empty() {
                majors.push(major);
            }
        }
    }
    majors
}

/// `_worker_dll` — duyệt bin/Release, lọc theo TargetFramework(s) khai báo,
/// sort mtime DESC (stable), chọn dll có major runtime đã cài.
fn worker_dll(project_path: &Path) -> PathBuf {
    let file_stem = project_path
        .file_stem()
        .map(|stem| stem.to_string_lossy().to_string())
        .unwrap_or_default();
    let release = project_path.parent().unwrap_or(Path::new(".")).join("bin").join("Release");
    let mut declared_targets: Vec<String> = Vec::new();
    if let Ok(text) = std::fs::read(project_path) {
        let text = String::from_utf8_lossy(&text[..text.len().min(64 * 1024)]);
        let regex = regex_lite_target_frameworks();
        for capture in regex.captures_iter(&text).flatten() {
            if let Some(group) = capture.get(1) {
                for item in group.as_str().split(';') {
                    let trimmed = item.trim();
                    if !trimmed.is_empty() {
                        declared_targets.push(trimmed.to_string());
                    }
                }
            }
        }
    }
    let mut candidates: Vec<(String, String, PathBuf)> = Vec::new();
    if release.is_dir() {
        let mut targets: Vec<String> = std::fs::read_dir(&release)
            .map(|entries| {
                entries
                    .flatten()
                    .filter(|entry| entry.file_type().map(|t| t.is_dir()).unwrap_or(false))
                    .map(|entry| entry.file_name().to_string_lossy().to_string())
                    .collect()
            })
            .unwrap_or_default();
        targets.sort();
        targets.reverse();
        for target in targets {
            if !declared_targets.is_empty() && !declared_targets.contains(&target) {
                continue;
            }
            let candidate = release.join(&target).join(format!("{file_stem}.dll"));
            if candidate.is_file() {
                let major = target
                    .strip_prefix("net")
                    .map(|rest| rest.chars().take_while(|c| c.is_ascii_digit()).collect())
                    .unwrap_or_default();
                candidates.push((major, target, candidate));
            }
        }
    }
    let mut mtimes: Vec<(std::time::SystemTime, usize)> = candidates
        .iter()
        .enumerate()
        .map(|(index, (_, _, path))| {
            let mtime = std::fs::metadata(path)
                .and_then(|metadata| metadata.modified())
                .unwrap_or(std::time::UNIX_EPOCH);
            (mtime, index)
        })
        .collect();
    // Python: sort(key=getmtime, reverse=True) — stable, tie giữ thứ tự cũ
    // (đã reverse theo target name).
    mtimes.sort_by(|(time_a, _), (time_b, _)| time_b.cmp(time_a));
    let ordered: Vec<(String, String, PathBuf)> = mtimes
        .into_iter()
        .map(|(_, index)| candidates[index].clone())
        .collect();
    let candidates = ordered;
    let runtimes = runtime_majors();
    for (major, _, candidate) in &candidates {
        if runtimes.contains(major) {
            return candidate.clone();
        }
    }
    if let Some((_, _, candidate)) = candidates.first() {
        return candidate.clone();
    }
    let mut sorted_targets = declared_targets.clone();
    sorted_targets.sort();
    let fallback_target = sorted_targets
        .last()
        .cloned()
        .unwrap_or_else(|| "net8.0".to_string());
    release.join(fallback_target).join(format!("{file_stem}.dll"))
}

fn regex_lite_target_frameworks() -> &'static fancy_regex::Regex {
    use std::sync::OnceLock;
    static REGEX: OnceLock<fancy_regex::Regex> = OnceLock::new();
    REGEX.get_or_init(|| {
        fancy_regex::Regex::new(r"<TargetFrameworks?>([^<]+)</TargetFrameworks?>").expect("static regex")
    })
}

/// `ensure_worker_built` — luôn chạy `dotnet build -c Release` khi chưa có
/// cache trong process này (giống Python với _BUILD_CACHE per-process).
pub fn ensure_worker_built(project_path: Option<&str>, verbose: bool) -> Result<PathBuf, String> {
    let candidate: PathBuf = match project_path {
        Some(path) if !path.is_empty() => realpath(&PathBuf::from(path)),
        _ => {
            let default_path = default_worker_project();
            if default_path.is_absolute() {
                default_path
            } else {
                // Binary chạy từ repo root (harness); khớp layout code-tiny.
                std::env::current_dir()
                    .unwrap_or_else(|_| PathBuf::from("."))
                    .join(&default_path)
            }
        }
    };
    let project = realpath(&candidate);
    let mut cache = BUILD_CACHE.lock().expect("build cache lock");
    if let Some(cached) = cache.first().filter(|path| Path::new(path).is_file()) {
        return Ok(PathBuf::from(cached));
    }
    let output = std::process::Command::new("dotnet")
        .arg("build")
        .arg(&project)
        .arg("-c")
        .arg("Release")
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::inherit())
        .output()
        .map_err(|error| format!("dotnet is unavailable: {error}"))?;
    if !output.status.success() {
        let combined = format!(
            "{}{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        let tail = tail_lines(&combined, 80);
        return Err(format!(
            "ASP.NET Roslyn worker build failed ({})\n{}",
            output.status.code().unwrap_or(-1),
            tail
        ));
    }
    let dll = worker_dll(&project);
    if !dll.is_file() {
        return Err(format!(
            "ASP.NET Roslyn worker DLL was not produced: {}",
            dll.to_string_lossy()
        ));
    }
    cache.push(dll.to_string_lossy().to_string());
    if verbose {
        println!("[aspnet][roslyn] worker={}", dll.to_string_lossy());
    }
    Ok(dll)
}

fn tail_lines(text: &str, limit: usize) -> String {
    let lines: Vec<&str> = text.lines().collect();
    let start = lines.len().saturating_sub(limit);
    lines[start..].join("\n")
}

/// Payload "trống" khi không còn file .cs — khớp dict Python.
fn empty_payload() -> Value {
    json!({
        "protocol_version": ASPNET_PROTOCOL_VERSION,
        "coverage_status": "empty",
        "workspace_kind": "none",
        "semantic_enabled": false,
        "results": [],
        "diagnostics": [],
    })
}

/// `analyze_csharp_files` — trả payload JSON của worker.
#[allow(clippy::too_many_arguments)]
pub fn analyze_csharp_files(
    root: &Path,
    files: &[String],
    semantic_mode: &str,
    project_path: &str,
    worker_project_path: Option<&str>,
    timeout_sec: f64,
    verbose: bool,
) -> Result<Value, String> {
    if !matches!(semantic_mode, "auto" | "on" | "off") {
        return Err("semantic_mode must be auto, on, or off".into());
    }
    let mut relative_files: Vec<String> = Vec::new();
    for path in files {
        let (_, relative) = resolve_inside_root(root, path, true)?;
        if relative.to_lowercase().ends_with(".cs") {
            relative_files.push(relative);
        }
    }
    relative_files.sort();
    relative_files.dedup();
    let project_relative = if !project_path.is_empty() {
        let (_, relative) = resolve_inside_root(root, project_path, true)?;
        normalize_relative_path(&relative)
    } else {
        String::new()
    };
    if relative_files.is_empty() {
        return Ok(empty_payload());
    }
    let dll = ensure_worker_built(worker_project_path, verbose)?;
    let request = json!({
        "protocol_version": ASPNET_PROTOCOL_VERSION,
        "root": root.to_string_lossy(),
        "files": relative_files,
        "semantic_mode": semantic_mode,
        "project_path": project_relative,
        "workspace_timeout_ms": DEFAULT_WORKSPACE_TIMEOUT_MS.max(5_000),
        "file_timeout_ms": DEFAULT_FILE_TIMEOUT_MS.max(5_000),
        "max_file_bytes": DEFAULT_MAX_FILE_BYTES.max(1),
    });
    let manifest_dir = std::env::temp_dir();
    let manifest = manifest_dir.join(format!(
        "aspnet_roslyn_p08_{}_{}.json",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos()
    ));
    std::fs::write(&manifest, crate::pyjson::dumps_compact_sorted_ascii(&request))
        .map_err(|error| error.to_string())?;
    let result = std::process::Command::new("dotnet")
        .arg(&dll)
        .arg("--manifest")
        .arg(&manifest)
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .output();
    let _ = std::fs::remove_file(&manifest);
    let output = match result {
        Ok(output) => output,
        Err(error) => return Err(error.to_string()),
    };
    if !output.status.success() {
        let source = if output.stderr.is_empty() {
            String::from_utf8_lossy(&output.stdout).into_owned()
        } else {
            String::from_utf8_lossy(&output.stderr).into_owned()
        };
        return Err(format!(
            "ASP.NET Roslyn worker failed ({})\n{}",
            output.status.code().unwrap_or(-1),
            tail_lines(&source, 80)
        ));
    }
    let stdout = String::from_utf8_lossy(&output.stdout);
    let payload: Value = serde_json::from_str(stdout.trim())
        .map_err(|error| format!("invalid ASP.NET Roslyn worker JSON: {error}"))?;
    if payload.get("protocol_version").and_then(Value::as_str) != Some(ASPNET_PROTOCOL_VERSION) {
        return Err(format!(
            "ASP.NET Roslyn protocol mismatch: {:?}",
            payload.get("protocol_version").and_then(Value::as_str)
        ));
    }
    if !payload.get("results").map(Value::is_array).unwrap_or(false) {
        return Err("ASP.NET Roslyn response is missing results".into());
    }
    let _ = timeout_sec;
    Ok(payload)
}

/// Timeout 600s mặc định (chỉ informational, khớp signature Python).
pub fn default_timeout_sec() -> f64 {
    DEFAULT_TIMEOUT_SEC
}
