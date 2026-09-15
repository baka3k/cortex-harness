//! `dev migrate` — thin dispatcher cho `cortex-migrate` (phase 14 scope B:
//! FalkorDBLite `.rdb` → Ladybug cutover).
//!
//! cortex-dev giữ dependency mặt tối thiểu (serde-only) nên mọi logic
//! migration (boot redislite+falkordb.so, đọc graph, ghi Ladybug, verify)
//! nằm ở crate `cortex-migrate`; lệnh này chỉ định vị binary, forward argv và
//! propagate exit code — cùng pattern "thin surface, heavy op out-of-process"
//! của `dev.py`.

use crate::parser::Matches;
use crate::util::{echo, echo_err};
use std::path::PathBuf;
use std::process::Command;

/// Định vị binary `cortex-migrate`: env override → release → debug target.
pub fn locate_binary() -> Option<PathBuf> {
    if let Ok(explicit) = std::env::var("CORTEX_MIGRATE_BIN") {
        let path = PathBuf::from(explicit);
        if path.is_file() {
            return Some(path);
        }
    }
    let root = crate::util::repo_root();
    [
        root.join("rust").join("target").join("release").join("cortex-migrate"),
        root.join("rust").join("target").join("debug").join("cortex-migrate"),
    ]
    .into_iter()
    .find(|candidate| candidate.is_file())
}

/// Forward mọi option đã parse sang `cortex-migrate` và chạy exec-style.
pub fn run(m: &Matches) {
    let mut args: Vec<String> = Vec::new();
    if m.flag("--dry-run") {
        args.push("--dry-run".to_string());
    }
    if m.flag("--overwrite") {
        args.push("--overwrite".to_string());
    }
    if let Some(value) = m.value("--data-root") {
        args.extend(["--data-root".to_string(), value.to_string()]);
    }
    if let Some(value) = m.value("--instance") {
        args.extend(["--instance".to_string(), value.to_string()]);
    }
    // Owner có default ở tầng tree (--owner both) — chỉ forward khi user ghi
    // đè để binary tự xử lý giá trị mặc định.
    if let Some(value) = m.value("--owner")
        && value != "both"
    {
        args.extend(["--owner".to_string(), value.to_string()]);
    }
    for value in m.values("--graph") {
        args.extend(["--graph".to_string(), value]);
    }

    let Some(binary) = locate_binary() else {
        echo_err("cortex-migrate binary not found.");
        echo_err("Build it first:  cargo build --release -p cortex-migrate");
        echo_err("(or point CORTEX_MIGRATE_BIN at an existing binary)");
        std::process::exit(1);
    };

    echo(&format!(
        "[migrate] {} {}",
        binary.display(),
        args.join(" ")
    ));
    let status = Command::new(&binary).args(&args).status();
    match status {
        Ok(status) if status.code().is_some() => {
            std::process::exit(status.code().unwrap_or(1));
        }
        Ok(_) => std::process::exit(1),
        Err(error) => {
            echo_err(&format!("[error] failed to launch {}: {}", binary.display(), error));
            std::process::exit(1);
        }
    }
}
