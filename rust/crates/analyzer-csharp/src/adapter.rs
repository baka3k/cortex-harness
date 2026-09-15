//! Port `code-tiny/tools/csharp/roslyn_adapter.py` — wire protocol với
//! CSharpRoslynWorker .NET subprocess:
//!
//! - `dotnet <dll> --manifest <request.json>` → JSON trên stdout.
//! - Worker bootstrap: `dotnet build -c Release` khi dll chưa có/stale
//!   (roslyn_adapter.py:92-103) — dll chọn theo mtime + runtime major.
//! - `DOTNET_ROLL_FORWARD=LatestMajor` set cho mọi lời gọi dotnet (khớp
//!   parity aspnet).
//!
//! KHÁC BIỆT CÓ CHỦ ĐÍCH so với Python: KHÔNG có tree-sitter fallback —
//! worker unavailable = loud error cho caller (entry exit 3 với hướng dẫn).

use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde_json::{json, Value};

pub const CSHARP_ROSLYN_PROTOCOL_VERSION: &str = "csharp-v1";
#[allow(dead_code)] // phase-03: chưa wire — giữ cho wiring composition/message lane sau
const CSHARP_ROSLYN_MODEL_VERSION: &str = "csharp-primary-v1";
pub const CSHARP_ROSLYN_CACHE_VERSION: &str = "csharp-v2026-09-10-1";

pub const DEFAULT_WORKSPACE_TIMEOUT_MS: i64 = 120_000;
pub const DEFAULT_FILE_TIMEOUT_MS: i64 = 60_000;
pub const DEFAULT_MAX_FILE_BYTES: i64 = 2 * 1024 * 1024;
#[allow(dead_code)] // phase-03: chưa wire — giữ cho wiring composition/message lane sau
const DEFAULT_TIMEOUT_SEC: f64 = 600.0;

/// `DOTNET_ROLL_FORWARD` — worker target framework có thể mới hơn runtime
/// đang cài; aspnet parity đã chứng minh env này cần thiết.
pub const DOTNET_ROLL_FORWARD: &str = "LatestMajor";

/// `_BUILD_CACHE` — per-process (mỗi binary chạy 1 process như Python).
static BUILD_CACHE: Mutex<Vec<String>> = Mutex::new(Vec::new());

/// Runtime dùng để spawn worker — override được trong unit test
/// (`dotnet_bin` trỏ sang script/binary giả).
#[derive(Debug, Clone)]
pub struct WorkerRuntime {
    /// Chương trình chạy worker (mặc định `dotnet`).
    pub dotnet_bin: String,
}

impl Default for WorkerRuntime {
    fn default() -> Self {
        Self {
            dotnet_bin: "dotnet".to_string(),
        }
    }
}

/// `default_worker_project()` — csproj của worker trong repo code-tiny,
/// resolve tương đối với current dir (binary chạy từ repo root).
pub fn default_worker_project() -> PathBuf {
    PathBuf::from("code-tiny/tools/csharp/roslyn_worker/CSharpRoslynWorker.csproj")
}

/// `_runtime_majors()` — các major version của Microsoft.NETCore.App đã cài.
fn runtime_majors(runtime: &WorkerRuntime) -> Vec<String> {
    let output = match std::process::Command::new(&runtime.dotnet_bin)
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

/// `_worker_dll(project_path)` — duyệt bin/Release, lọc theo
/// TargetFramework(s) khai báo trong csproj, sort mtime DESC (stable),
/// ưu tiên dll có major runtime đã cài.
pub fn worker_dll(project_path: &Path) -> PathBuf {
    let file_stem = project_path
        .file_stem()
        .map(|stem| stem.to_string_lossy().to_string())
        .unwrap_or_default();
    let release = project_path
        .parent()
        .unwrap_or(Path::new("."))
        .join("bin")
        .join("Release");
    let mut declared_targets: Vec<String> = Vec::new();
    if let Ok(text) = std::fs::read(project_path) {
        let text = String::from_utf8_lossy(&text[..text.len().min(64 * 1024)]);
        for capture in target_framework_captures(&text) {
            for item in capture.split(';') {
                let trimmed = item.trim();
                if !trimmed.is_empty() {
                    declared_targets.push(trimmed.to_string());
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
    // Python: sort(key=getmtime, reverse=True) — stable, tie giữ thứ tự cũ.
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
    mtimes.sort_by(|(time_a, _), (time_b, _)| time_b.cmp(time_a));
    let ordered: Vec<(String, String, PathBuf)> = mtimes
        .into_iter()
        .map(|(_, index)| candidates[index].clone())
        .collect();
    let candidates = ordered;
    let majors = runtime_majors(&WorkerRuntime::default());
    for (major, _, candidate) in &candidates {
        if majors.contains(major) {
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

/// `<TargetFramework?>([^<]+)</TargetFramework?>` — tách thủ công để tránh
/// dependency regex (pattern cố định, không có nested).
fn target_framework_captures(text: &str) -> Vec<String> {
    let mut captures = Vec::new();
    let mut rest = text;
    while let Some(start) = rest.find("<TargetFramework>") {
        let after = &rest[start + "<TargetFramework>".len()..];
        if let Some(end) = after.find("</TargetFramework>") {
            captures.push(after[..end].to_string());
            rest = &after[end..];
            continue;
        }
        break;
    }
    let mut rest = text;
    while let Some(start) = rest.find("<TargetFrameworks>") {
        let after = &rest[start + "<TargetFrameworks>".len()..];
        if let Some(end) = after.find("</TargetFrameworks>") {
            captures.push(after[..end].to_string());
            rest = &after[end..];
            continue;
        }
        break;
    }
    captures
}

fn tail_lines(text: &str, limit: usize) -> String {
    let lines: Vec<&str> = text.lines().collect();
    let start = lines.len().saturating_sub(limit);
    lines[start..].join("\n")
}

/// Mọi lời gọi dotnet đều mang `DOTNET_ROLL_FORWARD=LatestMajor`.
fn dotnet_command(dotnet_bin: &str) -> std::process::Command {
    let mut command = std::process::Command::new(dotnet_bin);
    command.env("DOTNET_ROLL_FORWARD", DOTNET_ROLL_FORWARD);
    command
}

/// `ensure_worker_built` — luôn chạy `dotnet build -c Release` khi chưa có
/// cache trong process này (giống Python: build mỗi lần chạy binary → dll
/// không bao giờ stale). Lỗi build = loud error (không fallback).
pub fn ensure_worker_built(
    runtime: &WorkerRuntime,
    project_path: Option<&str>,
    verbose: bool,
) -> Result<PathBuf, String> {
    let candidate: PathBuf = match project_path {
        Some(path) if !path.is_empty() => PathBuf::from(path),
        _ => {
            let default_path = default_worker_project();
            if default_path.is_absolute() {
                default_path
            } else {
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
    let mut command = dotnet_command(&runtime.dotnet_bin);
    let output = command
        .args(["build"])
        .arg(&project)
        .args(["-c", "Release"])
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .output()
        .map_err(|error| format!("dotnet is unavailable: {error}"))?;
    if !output.status.success() {
        let combined = format!(
            "{}{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        return Err(format!(
            "C# Roslyn worker build failed ({})\n{}",
            output.status.code().unwrap_or(-1),
            tail_lines(&combined, 80)
        ));
    }
    let dll = worker_dll(&project);
    if !dll.is_file() {
        return Err(format!(
            "C# Roslyn worker DLL was not produced: {}",
            dll.to_string_lossy()
        ));
    }
    cache.push(dll.to_string_lossy().to_string());
    if verbose {
        println!("[csharp][roslyn] worker={}", dll.to_string_lossy());
    }
    Ok(dll)
}

fn realpath(path: &Path) -> PathBuf {
    std::fs::canonicalize(path).unwrap_or_else(|_| path.to_path_buf())
}

/// Payload "trống" khi không còn file .cs — khớp dict Python
/// (analyze_csharp_files early-return).
pub fn empty_payload() -> Value {
    json!({
        "protocol_version": CSHARP_ROSLYN_PROTOCOL_VERSION,
        "coverage_status": "empty",
        "workspace_kind": "none",
        "semantic_enabled": false,
        "workspace_requested": false,
        "project_path": "",
        "results": [],
        "diagnostics": [],
        "project": null,
    })
}

/// Request manifest — cùng JSON shape với `analyze_csharp_files`
/// (roslyn_adapter.py:196-210).
pub fn build_manifest_request(
    root: &Path,
    relative_files: &[String],
    semantic_mode: &str,
    project_relative: &str,
    workspace_timeout_ms: i64,
    file_timeout_ms: i64,
    max_file_bytes: i64,
) -> Value {
    json!({
        "protocol_version": CSHARP_ROSLYN_PROTOCOL_VERSION,
        "root": root.to_string_lossy(),
        "files": relative_files,
        "semantic_mode": semantic_mode,
        "project_path": project_relative,
        "workspace_timeout_ms": workspace_timeout_ms.max(5_000),
        "file_timeout_ms": file_timeout_ms.max(5_000),
        "max_file_bytes": max_file_bytes.max(1),
        "extract_members": true,
        "extract_semantic": true,
        "extract_attributes": true,
        "extract_xml_docs": true,
        "extract_calls_resolved": true,
    })
}

/// Chạy worker trên manifest với timeout (poll loop vì stdlib không có
/// `subprocess.run(timeout=...)`). Stdout/stderr drain bằng 2 thread để pipe
/// không đầy trước khi process exit. Timeout → kill + loud error.
fn run_worker_with_timeout(
    runtime: &WorkerRuntime,
    dll: &Path,
    manifest: &Path,
    timeout_sec: f64,
) -> Result<(i32, String, String), String> {
    use std::io::Read;

    fn drain_pipe<R: Read + Send + 'static>(pipe: Option<R>) -> std::thread::JoinHandle<String> {
        std::thread::spawn(move || {
            let mut pipe = match pipe {
                Some(pipe) => pipe,
                None => return String::new(),
            };
            let mut buffer = Vec::new();
            let _ = pipe.read_to_end(&mut buffer);
            String::from_utf8_lossy(&buffer).into_owned()
        })
    }

    let mut command = dotnet_command(&runtime.dotnet_bin);
    command.arg(dll);
    command.args(["--manifest"]).arg(manifest);
    command.stdout(std::process::Stdio::piped());
    command.stderr(std::process::Stdio::piped());
    let mut child = command
        .spawn()
        .map_err(|error| format!("dotnet is unavailable: {error}"))?;
    let stdout_handle = drain_pipe(child.stdout.take());
    let stderr_handle = drain_pipe(child.stderr.take());
    let deadline = Instant::now() + Duration::from_secs_f64(timeout_sec.max(1.0));
    let status = loop {
        match child.try_wait().expect("try_wait polled child") {
            Some(status) => break status,
            None => {
                if Instant::now() >= deadline {
                    let _ = child.kill();
                    let _ = child.wait();
                    return Err(format!(
                        "C# Roslyn worker timed out after {}s",
                        timeout_sec.max(1.0)
                    ));
                }
                std::thread::sleep(Duration::from_millis(25));
            }
        }
    };
    let stdout = stdout_handle.join().unwrap_or_default();
    let stderr = stderr_handle.join().unwrap_or_default();
    Ok((
        status.code().unwrap_or(-1),
        stdout,
        stderr,
    ))
}

/// `analyze_csharp_files` — dựng request, spawn worker, validate response.
/// Mọi lỗi đều loud (caller KHÔNG fallback).
#[allow(clippy::too_many_arguments)]
pub fn analyze_csharp_files(
    runtime: &WorkerRuntime,
    root: &Path,
    files: &[String],
    semantic_mode: &str,
    worker_project_path: Option<&str>,
    timeout_sec: f64,
    verbose: bool,
) -> Result<Value, String> {
    if !matches!(semantic_mode, "auto" | "on" | "off") {
        return Err("semantic_mode must be auto, on, or off".into());
    }
    let root_abs = realpath(root);
    // File ngoài root / không .cs / không tồn tại → skip (khớp Python).
    let mut relative_files: Vec<String> = Vec::new();
    for path in files {
        let abs = if Path::new(path).is_absolute() {
            PathBuf::from(path)
        } else {
            root_abs.join(path)
        };
        let abs = realpath(&abs);
        if abs == root_abs || !abs.is_file() {
            continue;
        }
        let Ok(relative) = abs.strip_prefix(&root_abs) else {
            continue;
        };
        let rel = relative
            .components()
            .map(|component| component.as_os_str().to_string_lossy().to_string())
            .collect::<Vec<_>>()
            .join("/");
        if !rel.to_lowercase().ends_with(".cs") {
            continue;
        }
        relative_files.push(rel);
    }
    relative_files.sort();
    relative_files.dedup();
    if relative_files.is_empty() {
        return Ok(empty_payload());
    }

    let dll = ensure_worker_built(runtime, worker_project_path, verbose)?;
    let request = build_manifest_request(
        &root_abs,
        &relative_files,
        semantic_mode,
        "",
        DEFAULT_WORKSPACE_TIMEOUT_MS,
        DEFAULT_FILE_TIMEOUT_MS,
        DEFAULT_MAX_FILE_BYTES,
    );
    let manifest = std::env::temp_dir().join(format!(
        "csharp_roslyn_p03_{}_{}.json",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos()
    ));
    std::fs::write(&manifest, serde_json::to_vec(&request).map_err(|e| e.to_string())?)
        .map_err(|error| error.to_string())?;
    let result = run_worker_with_timeout(runtime, &dll, &manifest, timeout_sec);
    let _ = std::fs::remove_file(&manifest);
    let (code, stdout, stderr) = result?;
    if code != 0 {
        let source = if stderr.is_empty() { &stdout } else { &stderr };
        return Err(format!("C# Roslyn worker failed ({code})\n{}", tail_lines(source, 80)));
    }
    let payload: Value = serde_json::from_str(stdout.trim())
        .map_err(|error| format!("invalid C# Roslyn worker JSON: {error}"))?;
    if payload.get("protocol_version").and_then(Value::as_str)
        != Some(CSHARP_ROSLYN_PROTOCOL_VERSION)
    {
        return Err(format!(
            "C# Roslyn protocol mismatch: {:?}",
            payload.get("protocol_version").and_then(Value::as_str)
        ));
    }
    if !payload.get("results").map(Value::is_array).unwrap_or(false) {
        return Err("C# Roslyn response is missing results".into());
    }
    Ok(payload)
}

/// `parse_provenance` — map metadata worker về parse_meta shape của
/// csharp_analyzer.
pub fn parse_provenance(payload: &Value) -> Value {
    let coverage = payload
        .get("coverage_status")
        .and_then(Value::as_str)
        .unwrap_or("empty");
    let semantic_enabled = payload
        .get("semantic_enabled")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let workspace_kind = payload
        .get("workspace_kind")
        .and_then(Value::as_str)
        .unwrap_or("none");
    // Python: cả hai nhánh else đều là csharp_roslyn_syntax.
    let parser_language = if semantic_enabled && coverage == "full" {
        "csharp_roslyn_workspace"
    } else {
        "csharp_roslyn_syntax"
    };
    json!({
        "parser_language": parser_language,
        "parser_available": true,
        "semantic_enabled": semantic_enabled,
        "has_error": false,
        "error_nodes": 0,
        "coverage_status": coverage,
        "roslyn_workspace_kind": workspace_kind,
        "worker_protocol": payload.get("protocol_version").and_then(Value::as_str).unwrap_or(""),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    #[cfg(test)]
    pub(crate) fn reset_build_cache() {
        BUILD_CACHE.lock().expect("build cache lock").clear();
    }

    /// Prime cache để `ensure_worker_built` trả fake dll (script) mà không
    /// chạy `dotnet build` — giúp test wire protocol không cần .NET.
    #[cfg(test)]
    pub(crate) fn prime_build_cache(path: &Path) {
        BUILD_CACHE
            .lock()
            .expect("build cache lock")
            .push(path.to_string_lossy().to_string());
    }

    fn write_script(dir: &Path, name: &str, body: &str) -> PathBuf {
        let path = dir.join(name);
        let mut file = std::fs::File::create(&path).expect("create script");
        file.write_all(body.as_bytes()).expect("write script");
        path
    }

    /// Fake worker: `/bin/sh <script> --manifest request.json` — script nhận
    /// $2 = "--manifest", $3 = đường dẫn manifest (khớp run_worker_with_timeout:
    /// program=sh, args=[dll(=script), --manifest, manifest]).
    fn fake_worker_script(dir: &Path, response: &str) -> PathBuf {
        let response_path = dir.join("response.json");
        std::fs::write(&response_path, response).expect("write response");
        let seen = dir.join("request_seen.txt");
        write_script(
            dir,
            "fake_worker.sh",
            &format!(
                "#!/bin/sh\n# Khi `/bin/sh <script> --manifest <manifest>` chạy, shell\n# shift positional params vào $1=--manifest $2=<manifest>.\nMANIFEST=\"$2\"\ncp \"$MANIFEST\" \"{seen}\"\ncat \"{response}\"\n",
                seen = seen.to_string_lossy(),
                response = response_path.to_string_lossy()
            ),
        )
    }

    fn make_corpus(dir: &Path) -> Vec<String> {
        std::fs::create_dir_all(dir.join("Controllers")).expect("mkdir");
        std::fs::write(dir.join("Program.cs"), "class Program { }\n").expect("write cs");
        std::fs::write(
            dir.join("Controllers/HomeController.cs"),
            "class HomeController {{ }}\n",
        )
        .expect("write cs");
        std::fs::write(dir.join("notes.txt"), "not csharp").expect("write txt");
        vec![
            "Controllers/HomeController.cs".to_string(),
            "Program.cs".to_string(),
        ]
    }

    /// BUILD_CACHE là static per-process → các test đụng cache chạy serial
    /// trong 1 suite duy nhất.
    #[test]
    fn worker_protocol_and_bootstrap_suite() {
        // ── 1. Bootstrap: dotnet missing → actionable error ────────────────
        reset_build_cache();
        let runtime_missing = WorkerRuntime {
            dotnet_bin: "csharp-nonexistent-dotnet-binary-xyz".to_string(),
        };
        let error = ensure_worker_built(&runtime_missing, None, false).expect_err("must fail");
        assert!(error.contains("dotnet is unavailable"), "{error}");

        // ── 2. Bootstrap: build fail → message kèm tail + exit code ────────
        reset_build_cache();
        let temp = tempfile::tempdir().expect("tempdir");
        let project = temp.path().join("FailWorker.csproj");
        std::fs::write(&project, "<Project />").expect("write csproj");
        let runtime_false = WorkerRuntime {
            dotnet_bin: "/usr/bin/false".to_string(),
        };
        let error = ensure_worker_built(&runtime_false, Some(project.to_str().expect("path")), false)
            .expect_err("must fail");
        assert!(error.contains("C# Roslyn worker build failed (1)"), "{error}");

        // ── 3. Wire protocol round-trip với fake worker ────────────────────
        reset_build_cache();
        let root = temp.path().join("corpus");
        std::fs::create_dir_all(&root).expect("mkdir corpus");
        let expected_files = make_corpus(&root);
        let response = format!(
            r#"{{"protocol_version":"{CSHARP_ROSLYN_PROTOCOL_VERSION}","coverage_status":"full","workspace_kind":"out_of_sync_workspace","semantic_enabled":true,"workspace_requested":true,"project_path":"","results":[{{"file_path":"Program.cs","ok":true,"evidence":{{"file_path":"Program.cs","namespace":"App","types":[],"members":[],"fields":[],"events":[],"delegates":[],"parameters":[],"usings":[],"attributes":[],"calls":[]}},"error":null}}],"diagnostics":[],"project":null}}"#
        );
        let script = fake_worker_script(temp.path(), &response);
        prime_build_cache(&script);
        let runtime_sh = WorkerRuntime {
            dotnet_bin: "/bin/sh".to_string(),
        };
        let payload = analyze_csharp_files(
            &runtime_sh,
            &root,
            &expected_files,
            "auto",
            None,
            DEFAULT_TIMEOUT_SEC,
            false,
        )
        .expect("fake worker round trip");
        assert_eq!(
            payload.get("protocol_version").and_then(Value::as_str),
            Some(CSHARP_ROSLYN_PROTOCOL_VERSION)
        );
        assert_eq!(payload.get("coverage_status").and_then(Value::as_str), Some("full"));
        let results = payload.get("results").and_then(Value::as_array).expect("results");
        assert_eq!(results.len(), 1);
        // Manifest gửi đi phải sort + chỉ chứa file .cs.
        let manifest_name = std::fs::read_to_string(temp.path().join("request_seen.txt"))
            .expect("manifest seen");
        let manifest: Value = serde_json::from_str(manifest_name.trim()).expect("manifest json");
        assert_eq!(
            manifest.get("protocol_version").and_then(Value::as_str),
            Some(CSHARP_ROSLYN_PROTOCOL_VERSION)
        );
        let files: Vec<String> = manifest
            .get("files")
            .and_then(Value::as_array)
            .expect("files array")
            .iter()
            .map(|value| value.as_str().expect("str file").to_string())
            .collect();
        assert_eq!(files, expected_files); // sorted, .txt bị loại
        assert_eq!(manifest.get("semantic_mode").and_then(Value::as_str), Some("auto"));
        assert_eq!(manifest.get("extract_members").and_then(Value::as_bool), Some(true));
        assert_eq!(
            manifest.get("workspace_timeout_ms").and_then(Value::as_i64),
            Some(DEFAULT_WORKSPACE_TIMEOUT_MS)
        );

        // ── 4. Worker failure → loud error với stderr tail ─────────────────
        reset_build_cache();
        let fail_script =
            write_script(temp.path(), "fail_worker.sh", "#!/bin/sh\necho boom >&2\nexit 3\n");
        prime_build_cache(&fail_script);
        let error = analyze_csharp_files(
            &runtime_sh,
            &root,
            &["Program.cs".to_string()],
            "auto",
            None,
            DEFAULT_TIMEOUT_SEC,
            false,
        )
        .expect_err("must fail");
        assert!(error.contains("C# Roslyn worker failed (3)"), "{error}");
        assert!(error.contains("boom"), "{error}");

        // ── 5. Protocol mismatch → rejected ────────────────────────────────
        reset_build_cache();
        let bad_script = fake_worker_script(
            temp.path(),
            r#"{"protocol_version":"other-v9","results":[]}"#,
        );
        prime_build_cache(&bad_script);
        let error = analyze_csharp_files(
            &runtime_sh,
            &root,
            &["Program.cs".to_string()],
            "auto",
            None,
            DEFAULT_TIMEOUT_SEC,
            false,
        )
        .expect_err("must fail");
        assert!(error.contains("C# Roslyn protocol mismatch"), "{error}");

        // ── 6. Empty file list → empty payload, không đụng worker ──────────
        reset_build_cache();
        let payload = analyze_csharp_files(
            &runtime_missing,
            temp.path(),
            &[],
            "auto",
            None,
            DEFAULT_TIMEOUT_SEC,
            false,
        )
        .expect("empty payload without worker");
        assert_eq!(payload.get("coverage_status").and_then(Value::as_str), Some("empty"));
    }

    #[test]
    fn worker_dll_picks_release_output() {
        let temp = tempfile::tempdir().expect("tempdir");
        let project = temp.path().join("CSharpRoslynWorker.csproj");
        std::fs::write(
            &project,
            "<Project><PropertyGroup><TargetFramework>net8.0</TargetFramework></PropertyGroup></Project>",
        )
        .expect("write csproj");
        let dll_dir = temp.path().join("bin/Release/net8.0");
        std::fs::create_dir_all(&dll_dir).expect("mkdir dll dir");
        let dll = dll_dir.join("CSharpRoslynWorker.dll");
        std::fs::write(&dll, "fake").expect("write dll");
        let picked = worker_dll(&project);
        assert_eq!(picked, dll);
    }

    #[test]
    fn provenance_maps_coverage() {
        let payload = json!({
            "protocol_version": CSHARP_ROSLYN_PROTOCOL_VERSION,
            "coverage_status": "full",
            "semantic_enabled": true,
            "workspace_kind": "out_of_sync_workspace",
        });
        let provenance = parse_provenance(&payload);
        assert_eq!(
            provenance.get("parser_language").and_then(Value::as_str),
            Some("csharp_roslyn_workspace")
        );
        let syntax = parse_provenance(&json!({
            "coverage_status": "partial",
            "semantic_enabled": false,
            "workspace_kind": "none",
        }));
        assert_eq!(
            syntax.get("parser_language").and_then(Value::as_str),
            Some("csharp_roslyn_syntax")
        );
    }
}
