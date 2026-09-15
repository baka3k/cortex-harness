//! Native ONNX Runtime provisioning (`ensure-ort`, phase-05 port of
//! `scripts/ensure_ort.py`). `cortex-embed` builds `ort` with `load-dynamic`,
//! so the dylib must exist somewhere on disk; we provision the exact wheel
//! build the parity fixtures were measured with into `.cache/ort/<version>/`.
//!
//! Resolution order:
//!   1. `ORT_DYLIB_PATH` set  → respect it (print, no-op)
//!   2. `.cache/ort/<ver>/`   → already provisioned (no-network OK)
//!   3. venv wheel capi       → copy the installed wheel's dylib (same build
//!      the Python fixtures measure against)
//!   4. PyPI pinned wheel     → download via the pinned version's JSON API
//!      (curl) and extract (bsdtar/unzip — system tools, no new crate deps)

use crate::pyexec;
use serde_json::Value;
use std::path::{Path, PathBuf};

/// Pinned to the onnxruntime version resolved in `uv.lock` / the venv wheel —
/// keep in lockstep with `requirements.txt` updates.
pub const PINNED_ORT_VERSION: &str = "1.29.0";

const DYLIB_PATTERNS: [&str; 3] = [
    "libonnxruntime*.dylib",
    "libonnxruntime.so*",
    "onnxruntime.dll",
];

fn repo_root() -> PathBuf {
    pyexec::repo_root()
}

fn cache_root() -> PathBuf {
    repo_root().join(".cache").join("ort")
}

fn find_dylib(dir: &Path) -> Option<PathBuf> {
    for pattern in DYLIB_PATTERNS {
        if let Some(found) = glob_like(dir, pattern).and_then(|entries| entries.into_iter().next()) {
            return Some(found);
        }
    }
    None
}

/// Minimal `glob` for the three shapes above (no glob crate): literal prefix +
/// `*` suffix per pattern.
fn glob_like(dir: &Path, pattern: &str) -> Option<Vec<PathBuf>> {
    let (prefix, suffix) = pattern.split_once('*')?;
    let mut out = Vec::new();
    if let Ok(entries) = std::fs::read_dir(dir) {
        for entry in entries.flatten() {
            let name = entry.file_name().to_string_lossy().to_string();
            if name.len() >= prefix.len() + suffix.len()
                && name.starts_with(prefix)
                && name.ends_with(suffix)
                && entry.path().is_file()
            {
                out.push(entry.path());
            }
        }
    }
    out.sort();
    Some(out)
}

/// Locate the venv wheel's `capi` dir + its version
/// (`site-packages/onnxruntime/capi`).
fn venv_capi() -> Option<(PathBuf, String)> {
    let venv = repo_root().join(".venv");
    let lib = venv.join("lib");
    let site_dirs: Vec<PathBuf> = std::fs::read_dir(&lib)
        .into_iter()
        .flatten()
        .flatten()
        .map(|e| e.path())
        .filter(|p| {
            p.file_name()
                .map(|n| n.to_string_lossy().starts_with("python"))
                .unwrap_or(false)
        })
        .collect();
    #[cfg(windows)]
    {
        site_dirs.push(venv.join("Lib").join("site-packages"));
    }
    for site in site_dirs {
        // macOS/Linux: <lib>/python3.x/site-packages/onnxruntime/capi
        let capi = site
            .join("site-packages")
            .join("onnxruntime")
            .join("capi");
        if !capi.is_dir() {
            continue;
        }
        let found = glob_like(&capi, "libonnxruntime*.dylib")
            .or_else(|| glob_like(&capi, "libonnxruntime.so*"))
            .or_else(|| glob_like(&capi, "onnxruntime.dll"))
            .and_then(|v| v.into_iter().next());
        let Some(found) = found else { continue };
        let name = found
            .file_name()
            .map(|n| n.to_string_lossy().to_string())
            .unwrap_or_default();
        // `1.29.0` out of names like libonnxruntime.1.29.0.dylib / .so.1.29.0.
        let trimmed = name
            .trim_start_matches("libonnxruntime")
            .trim_start_matches(['.', '_'])
            .trim_end_matches(".dylib")
            .trim_end_matches(".dll");
        let trimmed = trimmed.trim_end_matches(".so");
        let version = if trimmed
            .chars()
            .next()
            .is_some_and(|c| c.is_ascii_digit())
        {
            trimmed.to_string()
        } else {
            PINNED_ORT_VERSION.to_string()
        };
        return Some((capi, version));
    }
    None
}

fn download_wheel(version: &str) -> Result<PathBuf, String> {
    let api = format!("https://pypi.org/pypi/onnxruntime/{version}/json");
    let json = curl_to_string(&api)?;
    let payload: Value = serde_json::from_str(&json).map_err(|e| format!("PyPI metadata parse: {e}"))?;
    let urls = payload
        .get("urls")
        .and_then(Value::as_array)
        .ok_or("PyPI metadata has no urls")?;
    let wanted = platform_wheel_tags();
    let mut candidate: Option<&Value> = None;
    for url in urls {
        let filename = url.get("filename").and_then(Value::as_str).unwrap_or("");
        if !filename.ends_with(".whl") {
            continue;
        }
        if wanted.iter().any(|tag| filename.contains(tag)) {
            candidate = Some(url);
            break;
        }
    }
    let url = candidate
        .and_then(|u| u.get("url"))
        .and_then(Value::as_str)
        .ok_or_else(|| format!("no wheel for this platform among PyPI assets (want {:?})", wanted))?;
    let staging = cache_root().join(format!("download-{version}"));
    std::fs::create_dir_all(&staging).map_err(|e| e.to_string())?;
    let wheel_path = staging.join(
        url.rsplit('/')
            .next()
            .unwrap_or("onnxruntime.whl")
            .to_string(),
    );
    if !wheel_path.is_file() {
        run_ok(&[
            "curl".to_string(),
            "-fL".to_string(),
            "-o".to_string(),
            wheel_path.to_string_lossy().to_string(),
            url.to_string(),
        ])?;
    }
    // Extract just the capi directory (bsdtar reads zip; unzip as fallback).
    let extract_ok = run_ok(&[
        "tar".to_string(),
        "-xf".to_string(),
        wheel_path.to_string_lossy().to_string(),
        "-C".to_string(),
        staging.to_string_lossy().to_string(),
        "onnxruntime/capi".to_string(),
    ])
    .is_ok();
    if !extract_ok {
        run_ok(&[
            "unzip".to_string(),
            "-o".to_string(),
            wheel_path.to_string_lossy().to_string(),
            "onnxruntime/capi/*".to_string(),
            "-d".to_string(),
            staging.to_string_lossy().to_string(),
        ])?;
    }
    Ok(staging.join("onnxruntime").join("capi"))
}

fn platform_wheel_tags() -> Vec<String> {
    if cfg!(target_os = "macos") {
        return vec![
            "macosx_11_0_arm64".into(),
            "macosx_12_0_arm64".into(),
            "macosx_11_0_x86_64".into(),
            "macosx_10".into(),
        ];
    }
    if cfg!(target_os = "windows") {
        return vec!["win_amd64".into()];
    }
    if cfg!(target_arch = "aarch64") {
        return vec!["manylinux_2_28_aarch64".into(), "manylinux2014_aarch64".into(), "manylinux_2_17_aarch64".into()];
    }
    vec![
        "manylinux_2_28_x86_64".into(),
        "manylinux2014_x86_64".into(),
        "manylinux_2_17_x86_64".into(),
    ]
}

fn curl_to_string(url: &str) -> Result<String, String> {
    let output = std::process::Command::new("curl")
        .args(["-fL", url])
        .output()
        .map_err(|e| format!("curl: {e}"))?;
    if !output.status.success() {
        return Err(format!(
            "curl {} failed: {}",
            url,
            String::from_utf8_lossy(&output.stderr).trim()
        ));
    }
    Ok(String::from_utf8_lossy(&output.stdout).to_string())
}

fn run_ok(args: &[String]) -> Result<(), String> {
    let status = std::process::Command::new(&args[0])
        .args(&args[1..])
        .stdout(std::process::Stdio::null())
        .status()
        .map_err(|e| format!("{}: {e}", args[0]))?;
    if status.success() {
        Ok(())
    } else {
        Err(format!("{} exited with {}", args[0], status.code().unwrap_or(1)))
    }
}

fn print_path_of(target: &Path, stable: &Path) {
    let _ = stable;
    println!("{}", target.display());
}

/// Entry: `dev ensure-ort [--force] [--print-path]`.
pub fn run(force: bool, print_path: bool) -> i32 {
    // 1) explicit dylib wins.
    if let Ok(explicit) = std::env::var("ORT_DYLIB_PATH") {
        if Path::new(&explicit).is_file() {
            if print_path {
                println!("{explicit}");
            } else {
                println!("[ort] ORT_DYLIB_PATH set: {explicit}");
            }
            return 0;
        }
    }

    // 2) already provisioned cache.
    let target_dir = cache_root().join(PINNED_ORT_VERSION);
    if !force {
        if let Some(existing) = find_dylib(&target_dir) {
            let stable = stable_name_for(&existing);
            let stable_path = target_dir.join(&stable);
            if print_path {
                print_path_of(&stable_path, &existing);
            } else {
                println!("[ort] already provisioned: {}", existing.display());
            }
            return 0;
        }
    }

    // 3) copy from the venv wheel; 4) download the pinned PyPI wheel.
    let sourced: Result<(PathBuf, String), String> = match venv_capi() {
        Some((capi, version)) => match find_dylib(&capi) {
            Some(source) => Ok((source, version)),
            None => Err(format!("no ONNX Runtime shared library under {}", capi.display())),
        },
        None => download_wheel(PINNED_ORT_VERSION)
            .map(|capi| (capi, PINNED_ORT_VERSION.to_string()))
            .map_err(|e| format!("onnxruntime wheel not found: no venv and PyPI download failed: {e}")),
    };
    let (source, version) = match sourced {
        Ok(pair) => pair,
        Err(message) => {
            eprintln!("[error] {message}");
            return 1;
        }
    };
    let target_dir = cache_root().join(&version);
    if std::fs::create_dir_all(&target_dir).is_err() {
        eprintln!("[error] cannot create {}", target_dir.display());
        return 1;
    }
    let file_name = source
        .file_name()
        .map(|n| n.to_string_lossy().to_string())
        .unwrap_or_default();
    let target = target_dir.join(&file_name);
    if std::fs::copy(&source, &target).is_err() {
        eprintln!("[error] cannot copy {} -> {}", source.display(), target.display());
        return 1;
    }
    // ORT's versioned soname is what the loader opens next; keep a stable
    // plain name alongside for globbing (`libonnxruntime*`).
    let stable_name = stable_name_for(&target);
    let stable_path = target_dir.join(&stable_name);
    if !stable_path.exists() {
        #[cfg(unix)]
        {
            if std::os::unix::fs::symlink(&file_name, &stable_path).is_err() {
                let _ = std::fs::copy(&target, &stable_path);
            }
        }
        #[cfg(not(unix))]
        {
            let _ = std::fs::copy(&target, &stable_path);
        }
    }

    if print_path {
        println!("{}", stable_path.display());
        return 0;
    }
    let size_mb = std::fs::metadata(&target).map(|m| m.len()).unwrap_or(0) as f64 / 1e6;
    println!(
        "[ort] provisioned onnxruntime {version}: {file_name} ({size_mb:.1} MB) -> {} (+ {stable_name})",
        target
            .strip_prefix(repo_root())
            .unwrap_or(&target)
            .display()
    );
    0
}

fn stable_name_for(source: &Path) -> String {
    let name = source
        .file_name()
        .map(|n| n.to_string_lossy().to_string())
        .unwrap_or_default();
    if name.ends_with(".dylib") {
        "libonnxruntime.dylib".to_string()
    } else if name.ends_with(".dll") {
        "onnxruntime.dll".to_string()
    } else {
        "libonnxruntime.so".to_string()
    }
}
