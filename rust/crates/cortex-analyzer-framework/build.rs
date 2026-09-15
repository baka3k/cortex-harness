//! Build script that bakes the source commit short SHA into the framework so
//! every analyzer binary can answer `--version` with it — the orchestrator
//! (`cortex-sync`) compares this against its own stamp before spawning a
//! child (phase-08 build-commit handshake, red-team F5 stale binary).
//! Resolution order mirrors `cortex-sync/build.rs`:
//! 1. `CORTEX_BUILD_COMMIT` env var (CI / cutover builds inject this).
//! 2. `git rev-parse --short HEAD` when the build runs inside the repo.
//! 3. Literal "unknown" when neither is available.

use std::process::Command;

fn main() {
    println!("cargo:rerun-if-env-changed=CORTEX_BUILD_COMMIT");
    // Rerun when git state changes so the stamp tracks HEAD. Both paths are
    // resolved from `git rev-parse` — relative guesses like `../../.git/HEAD`
    // do not resolve from `crates/<name>/` and silently never trigger.
    if let Some(git_dir) = git_dir() {
        println!("cargo:rerun-if-changed={git_dir}/HEAD");
        if let Some(ref_path) = head_ref_path(&git_dir) {
            println!("cargo:rerun-if-changed={ref_path}");
        }
    }

    let commit = std::env::var("CORTEX_BUILD_COMMIT")
        .ok()
        .filter(|v| !v.trim().is_empty())
        .or_else(resolve_from_git)
        .unwrap_or_else(|| "unknown".to_string());

    println!("cargo:rustc-env=CORTEX_BUILD_COMMIT={commit}");
}

fn git_dir() -> Option<String> {
    let output = Command::new("git")
        .args(["rev-parse", "--absolute-git-dir"])
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let dir = String::from_utf8(output.stdout).ok()?.trim().to_string();
    if dir.is_empty() {
        None
    } else {
        Some(dir)
    }
}

/// The file holding the current branch's tip (commits update it; `HEAD`
/// alone only changes on branch switches). None on detached HEAD.
fn head_ref_path(git_dir: &str) -> Option<String> {
    let output = Command::new("git")
        .args(["rev-parse", "--symbolic-full-name", "HEAD"])
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let name = String::from_utf8(output.stdout).ok()?.trim().to_string();
    if name.starts_with("refs/") {
        Some(format!("{git_dir}/{name}"))
    } else {
        None
    }
}

fn resolve_from_git() -> Option<String> {
    let output = Command::new("git")
        .args(["rev-parse", "--short", "HEAD"])
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let sha = String::from_utf8(output.stdout).ok()?.trim().to_string();
    if sha.is_empty() {
        None
    } else {
        Some(sha)
    }
}
