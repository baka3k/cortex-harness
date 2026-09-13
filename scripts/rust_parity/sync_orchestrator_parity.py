#!/usr/bin/env python3
"""Phase 09 parity gate — incremental-sync orchestrator (Python vs Rust).

Run matrix (all runs use parsers=python,shell,ts, no qdrant):

  1. Main line (hybrid): full run at commit 1, incremental run after commit 2
     — Python reference vs Rust `cortex-sync`, isolated cache dirs, isolated
     FalkorDB graphs (`p09_sync_py` / `p09_sync_rs`).
  2. Change-detection matrix: `committed` and `hash` modes over the same git
     states (reset to commit 1 → full run → commit 2 → incremental run), for
     both backends.

Gates:
  (a) [SCAN_RESULT] stdout lines byte-identical between backends (run 1 + 2).
  (b) Summary JSONs equal after masking volatile fields and per-run tokens
      (cache dir, graph name, run ids, timestamps, durations, pids).
  (c) Changed-manifest file lists equal across backends for each run; run 2
      lists equal the commit-2 diff per parser.
  (d) FalkorDB graph states (`p09_sync_py` vs `p09_sync_rs`) diff 0 outside
      the dual_write_diff mask.
  (e) Change-detection matrix: committed and hash incremental changed-sets
      match Python's per mode and match the hybrid run's diff sets.

Usage (repo root):
    .venv/bin/python scripts/rust_parity/sync_orchestrator_parity.py \
        [--keep] [--corpus-dir PATH] [--rust-bin rust/target/release/cortex-sync]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "code-tiny"))
sys.path.insert(0, str(REPO / "scripts" / "rust_parity"))
sys.path.insert(0, str(REPO))

from dual_write_diff import MASKED_PROPS, diff_dump, dump_graph  # noqa: E402
from tools.graph.driver.falkordb_driver import FalkorDBDriver  # noqa: E402

PY_ORCHESTRATOR = REPO / "code-tiny" / "tools" / "sync" / "incremental_sync.py"
PYTON_BIN = REPO / ".venv" / "bin" / "python"
FALKORDB_URI = "127.0.0.1:6379"

MASKED_SUMMARY_KEYS = {
    "run_id",
    "correlation_id",
    "started_at",
    "finished_at",
    "duration_seconds",
    "wait_seconds",
    "updated_at",
}


def sanitize_env(graph: str, cache_dir: Path) -> dict:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", str(Path.home())),
        "FALKORDB_URI": FALKORDB_URI,
        "FALKORDB_GRAPH": graph,
        # keep any pre-existing values out of child processes
        "LANG": os.environ.get("LANG", "en_US.UTF-8"),
    }
    for key in (
        "QDRANT_CODE_PATH",
        "QDRANT_URL",
        "QDRANT_CACHE_DIR",
        "CODE_GRAPH_PROVIDER",
        "GRAPH_PROVIDER",
        "NEO4J_URI",
        "NEO4J_USER",
        "NEO4J_PASS",
        "NEO4J_DB",
        "CORTEX_RUST_ANALYZER",
        "CORTEX_RUST_ANALYZER_BIN_DIR",
        "CORTEX_GRAPH_JOURNAL_MODE",
        "CORTEX_DISABLE_GRAPH",
        "CORTEX_RUN_ID",
        "CORTEX_CORRELATION_ID",
        "GIT_COMMIT_SHA_BEFORE",
        "GIT_COMMIT_SHA_AFTER",
        "PROJECT_ID",
        "PROJECT_NAME",
        "PROJECT_CODE",
        "INCREMENTAL_CHANGE_DETECTION",
        "SYNC_MESSAGES",
        "CORTEX_EXTRA_IGNORE_DIRS",
        "HYPERPACK_COLLECTION_SCHEME",
    ):
        env.pop(key, None)
    env["QDRANT_CACHE_DIR"] = str(cache_dir)
    return env


def run_git(corpus: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(corpus), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def build_corpus(corpus: Path) -> None:
    corpus.mkdir(parents=True, exist_ok=True)
    shutil.copytree(REPO / "tests/fixtures/ts-analyzer", corpus / "ts-app")
    shutil.copytree(REPO / "tests/fixtures/shell-application", corpus / "scripts")
    # 5 stock python files from the harness sources.
    stock_sources = [
        "code-tiny/tools/common/git_diff.py",
        "code-tiny/tools/common/sync_scope.py",
        "code-tiny/tools/common/scan_ignore.py",
        "code-tiny/tools/jp1/sniff.py",
        "code-tiny/tools/ts/ts_project_detector.py",
    ]
    py_dir = corpus / "stock"
    py_dir.mkdir(exist_ok=True)
    for index, source in enumerate(stock_sources):
        shutil.copyfile(REPO / source, py_dir / f"module_{index}.py")
    run_git(corpus, "init")
    run_git(corpus, "config", "user.email", "parity@example.com")
    run_git(corpus, "config", "user.name", "parity")
    run_git(corpus, "add", "-A")
    run_git(corpus, "commit", "-m", "commit 1")
    return git_sha(corpus, "HEAD")


def git_sha(corpus: Path, ref: str) -> str:
    output = subprocess.check_output(["git", "-C", str(corpus), "rev-parse", ref], text=True)
    return output.strip()


def advance_corpus(corpus: Path) -> None:
    """Commit 2: modify a .py, add a .sh, delete a .ts."""
    target = corpus / "stock/module_1.py"
    target.write_text(target.read_text() + "\n\ndef parity_added_function():\n    return 42\n")
    added = corpus / "scripts/new_entry.sh"
    added.write_text("#!/bin/sh\n# added in commit 2\nrun_new_entry() {\n    echo ok\n}\n")
    deleted = corpus / "ts-app/src/store/counters.ts"
    deleted.unlink()
    run_git(corpus, "add", "-A")
    run_git(corpus, "commit", "-m", "commit 2")
    return git_sha(corpus, "HEAD")


def run_orchestrator(
    backend: str,
    corpus: Path,
    cache_dir: Path,
    graph: str,
    summary_path: Path,
    change_detection: str,
    rust_bin: Path,
    before: str,
) -> dict:
    env = sanitize_env(graph, cache_dir)
    common = [
        "--root", str(corpus),
        "--project-id", "p09",
        "--project-name", "phase09",
        "--parsers", "python,shell,ts",
        "--python-bin", str(PYTON_BIN),
        "--config", "/dev/null",
        "--summary-path", str(summary_path),
        "--cache-dir", str(cache_dir),
        "--change-detection", change_detection,
        "--before-sha", before,
    ]
    if backend == "py":
        cmd = [str(PYTON_BIN), str(PY_ORCHESTRATOR), *common]
    else:
        cmd = [str(rust_bin), *common]
        env["CORTEX_REPO_ROOT"] = str(REPO)
    proc = subprocess.run(
        cmd,
        env=env,
        cwd=str(REPO),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=900,
    )
    scan_lines = [line for line in proc.stdout.splitlines() if "[SCAN_RESULT]" in line]
    return {
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "scan_lines": scan_lines,
        "summary": (
            json.loads(summary_path.read_text())
            if summary_path.exists()
            else None
        ),
        "cmd": cmd,
    }


def normalize(value, replacements: list[tuple[str, str]], key: str | None = None):
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            # lock.owner is process-local diagnostic metadata (pid, time) and
            # mask fields that are volatile by design (ids, timestamps, durations).
            if key == "owner" or k in MASKED_SUMMARY_KEYS:
                out[k] = "<MASKED>"
            else:
                out[k] = normalize(v, replacements, k)
        return out
    if isinstance(value, list):
        return [normalize(item, replacements) for item in value]
    if isinstance(value, str):
        for old, new in replacements:
            value = value.replace(old, new)
        # artifact_token = f"{snapshot12}_{pid}_{uuid8}" — pid/uuid are
        # process-local by design in BOTH orchestrators.
        value = re.sub(r"_\d+_[0-9a-f]{8}", "_<TOKEN>", value)
        return value
    return value


def diff_summaries(py: dict, rs: dict) -> list[str]:
    diffs: list[str] = []

    def walk(a, b, path: str) -> None:
        if type(a) is not type(b):
            diffs.append(f"{path}: type {type(a).__name__} != {type(b).__name__} ({a!r} vs {b!r})")
            return
        if isinstance(a, dict):
            for key in sorted(set(a) | set(b)):
                if key not in a:
                    diffs.append(f"{path}.{key}: missing in py summary")
                elif key not in b:
                    diffs.append(f"{path}.{key}: missing in rust summary")
                else:
                    walk(a[key], b[key], f"{path}.{key}")
        elif isinstance(a, list):
            if len(a) != len(b):
                diffs.append(f"{path}: list length {len(a)} != {len(b)}")
            for index, (item_a, item_b) in enumerate(zip(a, b)):
                walk(item_a, item_b, f"{path}[{index}]")
        elif a != b:
            diffs.append(f"{path}: {a!r} != {b!r}")

    walk(py, rs, "$")
    return diffs


def parser_entries(summary: dict, key: str) -> dict:
    return {
        entry.get("parser"): entry
        for entry in summary.get(key, [])
        if isinstance(entry, dict)
    }


def manifest_files(path_value: str) -> list[str]:
    if not path_value:
        return []
    path = Path(path_value)
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    return sorted(data.get("files") or [])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--corpus-dir", default=None)
    parser.add_argument(
        "--rust-bin",
        default=str(REPO / "rust/target/release/cortex-sync"),
    )
    args = parser.parse_args()
    rust_bin = Path(args.rust_bin).resolve()
    if not rust_bin.exists():
        print(f"[FAIL] rust binary missing: {rust_bin} (cargo build --release -p cortex-sync)")
        return 1

    workdir = Path(args.corpus_dir) if args.corpus_dir else Path(tempfile.mkdtemp(prefix="p09_parity_"))
    corpus = workdir / "corpus"
    cache_root = workdir / "caches"
    summary_root = workdir / "summaries"
    cache_root.mkdir(parents=True, exist_ok=True)
    summary_root.mkdir(parents=True, exist_ok=True)
    print(f"[setup] workdir={workdir}")

    commit1 = build_corpus(corpus)

    results: dict[str, dict] = {}
    graphs_dirty = False

    def pair_runs(label: str, change_detection: str, run_full: bool, before: str) -> None:
        suffix = label
        stage = "full" if run_full else "inc"
        for backend in ("py", "rs"):
            results[f"{suffix}_{stage}_{backend}"] = run_orchestrator(
                backend,
                corpus,
                cache_root / f"cache_{backend}_{suffix}",
                f"p09_sync_{backend}",
                summary_root / f"{suffix}_{stage}_{backend}.json",
                change_detection,
                rust_bin,
                before,
            )

    def graph_dump_pair() -> tuple[dict, dict]:
        driver = FalkorDBDriver(uri=FALKORDB_URI)
        py_dump = dump_graph(driver, "p09_sync_py")
        rs_dump = dump_graph(driver, "p09_sync_rs")
        for graph in ("p09_sync_py", "p09_sync_rs"):
            import asyncio

            asyncio.run(driver.execute_query("MATCH (n) DETACH DELETE n", {}, graph))
        return py_dump, rs_dump

    gate_results: dict[str, dict] = {}

    # ── run 1 (full) + run 2 (incremental) ────────────────────────────────
    print("[main] run 1 (full) — python + rust")
    pair_runs("hybrid", "hybrid", run_full=True, before=commit1)
    commit2 = advance_corpus(corpus)
    print("[main] run 2 (incremental after commit 2) — python + rust")
    pair_runs("hybrid", "hybrid", run_full=False, before=commit1)

    # Gate (a): [SCAN_RESULT] parity.
    scan_full_py = results["hybrid_full_py"]["scan_lines"]
    scan_full_rs = results["hybrid_full_rs"]["scan_lines"]
    scan_inc_py = results["hybrid_inc_py"]["scan_lines"]
    scan_inc_rs = results["hybrid_inc_rs"]["scan_lines"]
    gate_a = {
        "full": scan_full_py == scan_full_rs,
        "incremental": scan_inc_py == scan_inc_rs,
        "full_counts": (len(scan_full_py), len(scan_full_rs)),
        "inc_counts": (len(scan_inc_py), len(scan_inc_rs)),
    }
    gate_results["a_scan_result_lines"] = gate_a

    # Gate (b): summary parity after masking.
    resolved_cache_root = cache_root.resolve()
    resolved_corpus = str(corpus.resolve())
    replacements_py = [
        (str(resolved_cache_root / "cache_py_hybrid"), "<CACHE>"),
        ("p09_sync_py", "<GRAPH>"),
        (resolved_corpus, "<CORPUS>"),
    ]
    replacements_rs = [
        (str(resolved_cache_root / "cache_rs_hybrid"), "<CACHE>"),
        ("p09_sync_rs", "<GRAPH>"),
        (resolved_corpus, "<CORPUS>"),
    ]
    summary_diffs: dict[str, list[str]] = {}
    for run in ("full", "inc"):
        py_summary = normalize(results[f"hybrid_{run}_py"]["summary"], replacements_py)
        rs_summary = normalize(results[f"hybrid_{run}_rs"]["summary"], replacements_rs)
        summary_diffs[run] = diff_summaries(py_summary, rs_summary)
    gate_b = {
        "full_equal": not summary_diffs["full"],
        "inc_equal": not summary_diffs["inc"],
        "diffs": summary_diffs,
    }
    gate_results["b_summary_parity"] = {
        "full_equal": gate_b["full_equal"],
        "inc_equal": gate_b["inc_equal"],
        "full_diff_count": len(summary_diffs["full"]),
        "inc_diff_count": len(summary_diffs["inc"]),
    }

    # Gate (c): changed manifests.
    parsers = ("python", "shell", "ts")
    manifest_report: dict = {}
    manifest_ok = True
    expected_commit2 = {
        "python": ["stock/module_1.py"],
        "shell": ["scripts/new_entry.sh"],
        "ts": None,  # impact expansion may add ts files; equality is checked
    }
    deleted_commit2 = {"ts": ["ts-app/src/store/counters.ts"]}
    for run in ("full", "inc"):
        py_summary = results[f"hybrid_{run}_py"]["summary"]
        rs_summary = results[f"hybrid_{run}_rs"]["summary"]
        py_entries = parser_entries(py_summary, "primary_parsers")
        rs_entries = parser_entries(rs_summary, "primary_parsers")
        for parser in parsers:
            py_files = manifest_files(py_entries.get(parser, {}).get("changed_manifest", ""))
            rs_files = manifest_files(rs_entries.get(parser, {}).get("changed_manifest", ""))
            equal = py_files == rs_files
            manifest_ok = manifest_ok and equal
            manifest_report[f"{run}/{parser}"] = {
                "equal": equal,
                "py": py_files,
                "rs": rs_files,
            }
    # run 2 changed sets must equal the commit-2 diff (from the python run's
    # manifests — cross-backend equality was asserted above).
    inc_py_entries = parser_entries(results["hybrid_inc_py"]["summary"], "primary_parsers")
    for parser in parsers:
        files = manifest_files(inc_py_entries.get(parser, {}).get("changed_manifest", ""))
        expected = expected_commit2[parser]
        if expected is None:
            matches = True
        else:
            matches = files == expected
        manifest_ok = manifest_ok and matches
        manifest_report[f"expected/{parser}"] = {"matches": matches, "files": files}
    # deleted manifests must agree across backends and match the deleted file
    inc_rs_entries = parser_entries(results["hybrid_inc_rs"]["summary"], "primary_parsers")
    for parser in parsers:
        py_files = manifest_files(inc_py_entries.get(parser, {}).get("deleted_manifest", ""))
        rs_files = manifest_files(inc_rs_entries.get(parser, {}).get("deleted_manifest", ""))
        equal = py_files == rs_files
        matches = py_files == deleted_commit2.get(parser, [])
        manifest_ok = manifest_ok and equal and matches
        manifest_report[f"expected_deleted/{parser}"] = {
            "matches": matches,
            "equal": equal,
            "files": py_files,
        }
    gate_results["c_manifests"] = {"pass": manifest_ok, "detail": manifest_report}

    # Gate (d): graph diff.
    py_dump, rs_dump = graph_dump_pair()
    graph_diff = diff_dump(py_dump, rs_dump)
    graph_diff_total = sum(len(v) for v in graph_diff.values())
    gate_results["d_graph_diff"] = {
        "pass": graph_diff_total == 0,
        "nodes_py": len(py_dump["nodes"]),
        "nodes_rs": len(rs_dump["nodes"]),
        "edges_py": len(py_dump["edges"]),
        "edges_rs": len(rs_dump["edges"]),
        "diff_total": graph_diff_total,
        "masked_props": sorted(MASKED_PROPS),
    }

    # ── change-detection matrix (gate e) ─────────────────────────────────
    matrix_report: dict = {}
    matrix_ok = True
    for mode in ("committed", "hash"):
        run_git(corpus, "checkout", commit1)
        pair_runs(f"{mode}", mode, run_full=True, before=commit1)
        run_git(corpus, "checkout", commit2)
        pair_runs(f"{mode}", mode, run_full=False, before=commit1)
        for parser in parsers:
            py_entries = parser_entries(results[f"{mode}_inc_py"]["summary"], "primary_parsers")
            rs_entries = parser_entries(results[f"{mode}_inc_rs"]["summary"], "primary_parsers")
            py_files = manifest_files(py_entries.get(parser, {}).get("changed_manifest", ""))
            rs_files = manifest_files(rs_entries.get(parser, {}).get("changed_manifest", ""))
            equal = py_files == rs_files
            expected = expected_commit2[parser]
            matches_expected = expected is None or py_files == expected
            matrix_ok = matrix_ok and equal and matches_expected
            matrix_report[f"{mode}/{parser}"] = {
                "equal": equal,
                "matches_expected": matches_expected,
                "py": py_files,
                "rs": rs_files,
            }
        # summaries between backends must also match for the matrix runs
        for run in ("full", "inc"):
            replacements_py_m = [
                (str(resolved_cache_root / f"cache_py_{mode}"), "<CACHE>"),
                ("p09_sync_py", "<GRAPH>"),
                (resolved_corpus, "<CORPUS>"),
            ]
            replacements_rs_m = [
                (str(resolved_cache_root / f"cache_rs_{mode}"), "<CACHE>"),
                ("p09_sync_rs", "<GRAPH>"),
                (resolved_corpus, "<CORPUS>"),
            ]
            diffs = diff_summaries(
                normalize(results[f"{mode}_{run}_py"]["summary"], replacements_py_m),
                normalize(results[f"{mode}_{run}_rs"]["summary"], replacements_rs_m),
            )
            matrix_ok = matrix_ok and not diffs
            matrix_report[f"{mode}/{run}/summary_diff_count"] = len(diffs)
            if diffs:
                matrix_report[f"{mode}/{run}/summary_diffs"] = diffs[:20]
    gate_results["e_change_detection_matrix"] = {"pass": matrix_ok, "detail": matrix_report}

    # Exit codes must match on the happy path.
    exit_codes_ok = all(
        results[name]["returncode"] == 0
        for name in results
        if name.endswith(("_py", "_rs"))
    )
    gate_results["exit_codes"] = {"pass": exit_codes_ok}

    # ── report ────────────────────────────────────────────────────────────
    all_pass = (
        gate_a["full"]
        and gate_a["incremental"]
        and gate_b["full_equal"]
        and gate_b["inc_equal"]
        and manifest_ok
        and gate_results["d_graph_diff"]["pass"]
        and matrix_ok
        and exit_codes_ok
    )
    print(json.dumps({k: v for k, v in gate_results.items() if k != "c_manifests"}, indent=2, default=str))
    print(json.dumps(gate_results["c_manifests"]["detail"], indent=2, default=str))
    if not gate_b["full_equal"] or not gate_b["inc_equal"]:
        for run in ("full", "inc"):
            for diff in summary_diffs[run][:40]:
                print(f"[summary-diff {run}] {diff}")
    print(f"[gates] all_pass={all_pass}")

    if args.keep:
        print(f"[keep] artifacts under {workdir}")
    else:
        shutil.rmtree(workdir, ignore_errors=True)
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
