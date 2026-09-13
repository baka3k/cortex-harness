//! Bridge to the Python layer.
//!
//! dev.py computes the per-process storage environment through
//! `cortex_harness.storage` (resolve_storage + storage_overlay + effective
//! topology fingerprints). Until the native Rust storage layer lands
//! (parallel phase-10 workstream), those computations — and other operations
//! that are pure Python in dev.py (`stop_sync_processes`, journal inspection,
//! `db_transfer`) — are invoked through the harness venv's Python using the
//! exact same functions, keeping observable behaviour identical.

use std::path::{Path, PathBuf};
use std::process::Command;

/// Repo root resolution: env override, walk-up from cwd, compile-time fallback.
pub fn repo_root() -> PathBuf {
    if let Ok(env_root) = std::env::var("CORTEX_HARNESS_REPO_ROOT") {
        let p = PathBuf::from(env_root);
        if p.join("cortex_harness/dev.py").is_file() {
            return p;
        }
    }
    let mut cur = std::env::current_dir().ok();
    while let Some(dir) = cur {
        if dir.join("cortex_harness/dev.py").is_file() {
            return dir;
        }
        cur = dir.parent().map(Path::to_path_buf);
    }
    // Compile-time fallback: <repo>/rust/crates/cortex-dev
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(|p| p.parent())
        .and_then(|p| p.parent())
        .map(Path::to_path_buf)
        .unwrap_or_else(|| PathBuf::from("."))
}

/// dev.py `_venv_python`: project venv first, then the harness repo venv,
/// then the ambient interpreter.
pub fn venv_python(base_dir: &Path) -> String {
    let candidates = [
        base_dir.join(".venv").join("Scripts").join("python.exe"),
        base_dir.join(".venv").join("bin").join("python"),
    ];
    for candidate in candidates.iter() {
        if candidate.exists() {
            return candidate.to_string_lossy().to_string();
        }
    }
    let root = repo_root();
    for candidate in [
        root.join(".venv").join("Scripts").join("python.exe"),
        root.join(".venv").join("bin").join("python"),
    ] {
        if candidate.exists() {
            return candidate.to_string_lossy().to_string();
        }
    }
    "python3".to_string()
}

/// Path to a helper inside code-tiny / doc-tiny.
pub fn repo_file(rel: &str) -> PathBuf {
    repo_root().join(rel)
}

pub struct PyOutcome {
    pub code: i32,
    pub stdout: String,
    pub stderr: String,
}

/// Run the python helper dispatcher with `op` and a JSON arg payload;
/// returns parsed stdout JSON. Exits like dev.py when the helper fails with
/// a `[error]`/`Error:` line (already forwarded on stderr).
pub fn call_json(op: &str, args: &serde_json::Value) -> serde_json::Value {
    match try_call_json(op, args) {
        Ok(v) => v,
        Err(out) => {
            if !out.stderr.trim().is_empty() {
                eprint!("{}", out.stderr);
            }
            std::process::exit(if out.code == 0 { 1 } else { out.code });
        }
    }
}

pub fn try_call_json(op: &str, args: &serde_json::Value) -> Result<serde_json::Value, PyOutcome> {
    let out = call_raw(op, args);
    if out.code != 0 {
        return Err(out);
    }
    serde_json::from_str(out.stdout.trim())
        .map_err(|_| PyOutcome { code: 1, stdout: out.stdout, stderr: out.stderr })
}

pub fn call_raw(op: &str, args: &serde_json::Value) -> PyOutcome {
    let root = repo_root();
    let output = Command::new(venv_python(&root))
        .arg("-c")
        .arg(HELPER_SRC)
        .arg(root.to_string_lossy().to_string())
        .arg(op)
        .arg(serde_json::to_string(args).unwrap_or_else(|_| "{}".to_string()))
        .output();

    match output {
        Ok(o) => PyOutcome {
            code: o.status.code().unwrap_or(1),
            stdout: String::from_utf8_lossy(&o.stdout).to_string(),
            stderr: String::from_utf8_lossy(&o.stderr).to_string(),
        },
        Err(e) => PyOutcome {
            code: 1,
            stdout: String::new(),
            stderr: format!("[error] failed to launch python helper: {}\n", e),
        },
    }
}

/// Single helper entrypoint reusing the dev.py implementations verbatim.
const HELPER_SRC: &str = r#"# cortex-dev python bridge (phase 10). Keep logic identical to dev.py.
import json, os, sys
from pathlib import Path

REPO_ROOT = Path(sys.argv[1])
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "code-tiny"))

op = sys.argv[2]
args = json.loads(sys.argv[3])


def out(payload):
    sys.stdout.write(json.dumps(payload, sort_keys=True, default=str))
    sys.stdout.flush()


def code_env(cfg, project_root):
    from cortex_harness.dev import _code_env_for_process
    return _code_env_for_process(cfg, Path(project_root))


def doc_env(cfg, project_root):
    from cortex_harness.dev import _doc_env_for_process
    return _doc_env_for_process(cfg, Path(project_root))


def load_cfg(project_root):
    from cortex_harness.dev import _load_active_config
    cfg, path = _load_active_config(Path(project_root))
    return cfg, str(path)


if op == "status_env":
    from cortex_harness.dev import (
        _code_env_for_process,
        _code_qdrant_collection,
        _doc_env_for_process,
        _graph_provider,
        _load_active_config,
    )
    project = Path(args["project_dir"])
    cfg, path = _load_active_config(project)
    code_env_raw = dict(cfg.get("code", {}).get("env", {}))
    doc_env_raw = dict(cfg.get("doc", {}).get("env", {}))
    out({
        "config_name": path.name,
        "project": cfg.get("project", {}),
        "code_env_raw": code_env_raw,
        "doc_env_raw": doc_env_raw,
        "code_env": _code_env_for_process(cfg, project),
        "doc_env": _doc_env_for_process(cfg, project),
        "code_provider": _graph_provider(code_env_raw, "CODE_GRAPH_PROVIDER"),
        "doc_provider": _graph_provider(doc_env_raw, "DOC_GRAPH_PROVIDER"),
        "code_collection": _code_qdrant_collection(
            code_env_raw, cfg.get("project", {})
        ),
    })
elif op == "code_env":
    cfg, path = load_cfg(args["project_dir"])
    payload = code_env(cfg, args["project_dir"])
    payload["CORTEX_HARNESS_CONFIG_PATH"] = str(Path(path).resolve())
    out(payload)
elif op == "doc_env":
    cfg, path = load_cfg(args["project_dir"])
    payload = doc_env(cfg, args["project_dir"])
    payload["CORTEX_HARNESS_CONFIG_PATH"] = str(Path(path).resolve())
    out(payload)
elif op == "mcp_env":
    from cortex_harness.dev import _mcp_env_from_config
    out(_mcp_env_from_config(Path(args["project_dir"]), args["service"]))
elif op == "stop_sync_workers":
    from cortex_harness.sync_processes import stop_sync_processes
    report = stop_sync_processes(
        args["owner"], root=REPO_ROOT,
        include_launchers=bool(args.get("include_launchers")),
    )
    configured = str(args.get("falkordb_path") or "").strip()
    stopped, remaining = [], []
    if configured:
        from cortex_harness.sync_processes import (
            embedded_falkordb_pids,
            stop_embedded_falkordb,
        )
        db_path = Path(configured)
        stopped = list(stop_embedded_falkordb(db_path))
        remaining = list(embedded_falkordb_pids(db_path))
    out({
        "matched": list(report.matched),
        "terminated": list(report.terminated),
        "forced": list(report.forced),
        "remaining": list(report.remaining),
        "embedded_stopped": stopped,
        "embedded_remaining": remaining,
    })
elif op == "embedded_falkordb_pids":
    from cortex_harness.sync_processes import embedded_falkordb_pids
    out(list(embedded_falkordb_pids(Path(args["db_path"]))))
elif op == "stop_embedded":
    from cortex_harness.sync_processes import stop_embedded_falkordb
    out(stop_embedded_falkordb(Path(args["db_path"])))
elif op == "mcp_pids":
    from cortex_harness.dev import _mcp_pids
    out(_mcp_pids(args["pattern"], instance_id=args.get("instance_id")))
elif op == "mcp_uptime":
    from cortex_harness.dev import _mcp_uptime
    out(_mcp_uptime(int(args["pid"])))
elif op == "mcp_stop":
    from cortex_harness.dev import _mcp_stop_pattern
    out(_mcp_stop_pattern(args["pattern"], instance_id=args.get("instance_id")))
elif op == "mcp_start":
    from cortex_harness.dev import _mcp_env_from_config, _mcp_start_one, MCP_SERVICES
    name = args["name"]
    svc = MCP_SERVICES[name]
    extra_env = _mcp_env_from_config(Path(args["project_dir"]), name)
    result = _mcp_start_one(name, svc, extra_env=extra_env)
    result.setdefault("extra_env", {})
    result["qdrant_collection"] = extra_env.get("QDRANT_COLLECTION", "")
    out(result)
elif op == "journal_status":
    from cortex_harness.dev import JournalError, inspect_journal
    resolved = Path(args["journal_path"]).expanduser().resolve(strict=True)
    try:
        summaries = inspect_journal(resolved)
    except JournalError as exc:
        sys.stderr.write(
            "Error: journal status failed (%s): %s\n" % (exc.code.value, exc)
        )
        raise SystemExit(1)
    out({"journal_path": str(resolved), "runs": summaries})
elif op == "journal_purge":
    from pathlib import Path as P
    from cortex_harness.dev import (
        JournalError,
        JournalLockBusyError,
        ProjectRunLock,
        SQLiteJournal,
        scan_scope_id,
    )
    import click as _click
    resolved = P(args["journal_path"]).expanduser().resolve(strict=True)
    canonical_root = P(args["root"]).expanduser().resolve(strict=True)
    project_id = args["project_id"]
    run_id = args["run_id"]
    try:
        scope_id = scan_scope_id(project_id, str(canonical_root))
        cache_root = resolved.parent.parent.parent
        expected_parent = (cache_root / "graph-write-journal" / scope_id).resolve()
        if resolved.parent != expected_parent:
            sys.stderr.write(
                "Error: journal path does not match the exact project/root scope\n"
            )
            raise SystemExit(1)
        lock_path = cache_root / "incremental_sync_locks" / f"{scope_id}.lock"
        ownership = ProjectRunLock(
            str(lock_path), f"journal purge project_id={project_id}",
            scope_id, str(canonical_root), timeout_seconds=0,
        )
        with ownership:
            with SQLiteJournal(resolved) as database:
                run = database.get_run(run_id)
                if run is None:
                    sys.stderr.write("Error: journal run does not exist\n")
                    raise SystemExit(1)
                if run.metadata.project_id != project_id or run.metadata.scope_id != scope_id:
                    sys.stderr.write(
                        "Error: journal run metadata does not match the exact project/root scope\n"
                    )
                    raise SystemExit(1)
                safe_parser = "".join(
                    c if c.isalnum() or c in "-_" else "_"
                    for c in run.metadata.parser
                )
                if resolved.name != f"{safe_parser}.sqlite3":
                    sys.stderr.write(
                        "Error: journal filename does not match the run parser\n"
                    )
                    raise SystemExit(1)
                removed = database.purge_run(run_id, ownership_confirmed=True)
    except JournalLockBusyError:
        sys.stderr.write(
            "Error: journal scope is active; wait for sync/consumer completion\n"
        )
        raise SystemExit(1)
    except JournalError as exc:
        sys.stderr.write(
            "Error: journal purge refused (%s): %s\n" % (exc.code.value, exc)
        )
        raise SystemExit(1)
    out({
        "event_type": "journal_purged",
        "journal_path": str(resolved),
        "run_id": run_id,
        "scope_id": scope_id,
        "removed_artifacts": removed,
    })
elif op == "db_export":
    from cortex_harness.db_transfer import DbTransferError, export_project
    try:
        export_project(
            Path(args["project_dir"]).resolve(),
            output=Path(args["output"]) if args.get("output") else None,
            project_id_override=args.get("project_id"),
            role=args["role"],
        )
    except DbTransferError as exc:
        sys.stderr.write("[error] %s\n" % exc)
        raise SystemExit(1)
    out({"ok": True})
elif op == "db_import":
    from cortex_harness.db_transfer import DbTransferError, import_project
    try:
        import_project(
            Path(args["project_dir"]).resolve(),
            Path(args["archive"]),
            overwrite=bool(args.get("overwrite")),
            role=args["role"],
        )
    except DbTransferError as exc:
        sys.stderr.write("[error] %s\n" % exc)
        raise SystemExit(1)
    out({"ok": True})
else:
    sys.stderr.write("unknown helper op: %s\n" % op)
    raise SystemExit(2)
"#;
