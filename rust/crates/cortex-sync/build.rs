//! Build script that bakes the source commit short SHA into the binary so the
//! registry can surface it in retire errors. Resolution order:
//! 1. `CORTEX_BUILD_COMMIT` env var (CI / cutover commits inject this).
//! 2. `git rev-parse --short HEAD` when the build runs inside the repo.
//! 3. Literal "unknown" when neither is available.

use std::process::Command;

fn main() {
    println!("cargo:rerun-if-env-changed=CORTEX_BUILD_COMMIT");
    println!("cargo:rerun-if-changed=../../.git/HEAD");

    let commit = std::env::var("CORTEX_BUILD_COMMIT")
        .ok()
        .filter(|v| !v.trim().is_empty())
        .or_else(resolve_from_git)
        .unwrap_or_else(|| "unknown".to_string());

    println!("cargo:rustc-env=CORTEX_BUILD_COMMIT={commit}");
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