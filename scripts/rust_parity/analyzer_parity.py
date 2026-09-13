#!/usr/bin/env python3
"""Phase 04 parity gate — analyzer Python vs analyzer Rust trên repo thật.

Chạy `tools/python/python_analyzer.py` (backend tham chiếu) và
`rust/crates/analyzer-python` (backend Rust) trên 2 graph FalkorDB riêng
với CÙNG CLI contract, sau đó dump cả 2 graph (nodes + edges + properties,
mask timestamp/_graph_id) và so exact.

Gates (phase-04.md):
  1. FULL mode: graph diff rỗng (ngoài mask) trên repo stock thật + testdata.
  2. Incremental: sửa/thêm/xoá file trên bản copy → manifest → cleanup counts
     và graph state khớp Python.
  3. `[SCAN_RESULT]` line byte-identical giữa 2 backend.
  4. Summary JSON schema (`--summary-path`, Rust-side) đầy đủ trường.

Usage (từ repo root):
    .venv/bin/python scripts/rust_parity/analyzer_parity.py [--skip-stock]
        [--rust-bin rust/target/release/analyzer-python] [--host 127.0.0.1]
        [--port 6379]

Exit code 0 = PASS (diff rỗng ngoài mask); 1 = FAIL (in diff).
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
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "code-tiny"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tools.graph.driver.falkordb_driver import FalkorDBDriver  # noqa: E402
from dual_write_diff import MASKED_PROPS, diff_dump, dump_graph  # noqa: E402

STOCK = Path("/Users/hieplq1.aip/baka3k/stock")
TESTDATA = REPO / "tests" / "fixtures" / "python-analyzer"
PY_ANALYZER = REPO / "code-tiny" / "tools" / "python" / "python_analyzer.py"
PY_BIN = REPO / ".venv" / "bin" / "python"
RUST_BIN = REPO / "rust" / "target" / "release" / "analyzer-python"
REPORT_PATH = (
    REPO / "plans" / "260913-2130-rust-full-migration" / "reports"
    / "phase04-analyzer-parity.md"
)

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


# ── Env sanitis cho subprocess (không lây env qdrant/neo4j của shell) ───────

SANITIZE_KEYS = [
    "QDRANT_CODE_PATH",
    "QDRANT_COLLECTION",
    "NEO4J_URI",
    "NEO4J_USER",
    "NEO4J_PASS",
    "NEO4J_DB",
    "FALKORDB_URI",
    "FALKORDB_PATH",
    "FALKORDB_GRAPH",
    "PROJECT_ID",
    "PROJECT_NAME",
    "PROJECT_LANGUAGE",
    "PROJECT_REPO",
    "PROJECT_BUILD_SYSTEM",
    "GIT_COMMIT_SHA_BEFORE",
    "GIT_COMMIT_SHA_AFTER",
    "CORTEX_EXTRA_IGNORE_DIRS",
    "ENABLE_FLOWS",
    "ENABLE_LLM_SUMMARY",
]


def analyzer_env() -> dict:
    env = dict(os.environ)
    for key in SANITIZE_KEYS:
        env.pop(key, None)
    return env


# ── Analyzer runners ─────────────────────────────────────────────────────────


def run_python_analyzer(
    root: Path,
    project_id: str,
    graph: str,
    host: str,
    port: int,
    *,
    incremental: bool = False,
    changed_manifest: Path | None = None,
    deleted_manifest: Path | None = None,
) -> tuple[str, float]:
    cmd = [
        str(PY_BIN),
        str(PY_ANALYZER),
        "--config",
        "/dev/null",
        "--root",
        str(root),
        "--project-id",
        project_id,
        "--project-name",
        project_id,
        "--commit-sha-before",
        "",
        "--commit-sha-after",
        "",
        "--graph-provider",
        "falkordb",
        "--falkordb-uri",
        f"{host}:{port}",
        "--falkordb-graph",
        graph,
        "--disable-message-scan",
        "--verbose",
    ]
    if incremental:
        cmd.append("--incremental")
        if changed_manifest:
            cmd.extend(["--changed-files-manifest", str(changed_manifest)])
        if deleted_manifest:
            cmd.extend(["--deleted-files-manifest", str(deleted_manifest)])
    start = time.monotonic()
    proc = subprocess.run(
        cmd, capture_output=True, text=True, timeout=900, env=analyzer_env()
    )
    duration = time.monotonic() - start
    if proc.returncode != 0:
        raise RuntimeError(
            f"python analyzer failed ({proc.returncode}):\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
        )
    return proc.stdout, duration


def run_rust_analyzer(
    root: Path,
    project_id: str,
    graph: str,
    host: str,
    port: int,
    *,
    incremental: bool = False,
    changed_manifest: Path | None = None,
    deleted_manifest: Path | None = None,
    summary_path: Path | None = None,
) -> tuple[str, float]:
    cmd = [
        str(RUST_BIN),
        "--root",
        str(root),
        "--project-id",
        project_id,
        "--project-name",
        project_id,
        "--commit-sha-before",
        "",
        "--commit-sha-after",
        "",
        "--graph-provider",
        "falkordb",
        "--falkordb-uri",
        f"{host}:{port}",
        "--falkordb-graph",
        graph,
        "--disable-message-scan",
        "--verbose",
    ]
    if incremental:
        cmd.append("--incremental")
        if changed_manifest:
            cmd.extend(["--changed-files-manifest", str(changed_manifest)])
        if deleted_manifest:
            cmd.extend(["--deleted-files-manifest", str(deleted_manifest)])
    if summary_path:
        cmd.extend(["--summary-path", str(summary_path)])
    start = time.monotonic()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    duration = time.monotonic() - start
    if proc.returncode != 0:
        raise RuntimeError(
            f"rust analyzer failed ({proc.returncode}):\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
        )
    return proc.stdout, duration


SCAN_RESULT_RE = re.compile(
    r"\[SCAN_RESULT\] parser=(\S+) files=(\d+) functions=(\d+) classes=(\d+)"
)
CLEANUP_RE = re.compile(r"\[cleanup\]\[graph\] deleted_nodes=(\d+) deleted_unknown_functions=(\d+)")


def scan_result_of(log: str) -> str:
    matches = list(SCAN_RESULT_RE.finditer(log))
    if not matches:
        raise RuntimeError(f"[SCAN_RESULT] not found in log:\n{log[-1500:]}")
    return matches[-1].group(0)


def cleanup_counts_of(log: str) -> tuple[int, int]:
    matches = CLEANUP_RE.findall(log)
    if not matches:
        return (0, 0)
    return tuple(int(value) for value in matches[-1])  # type: ignore[return-value]


# ── Graph compare ────────────────────────────────────────────────────────────


def compare_graphs(
    driver: FalkorDBDriver,
    graph_py: str,
    graph_rust: str,
    label: str,
    report_lines: list[str],
) -> None:
    py_dump = dump_graph(driver, graph_py)
    rust_dump = dump_graph(driver, graph_rust)
    diff = diff_dump(py_dump, rust_dump)
    diff_total = sum(len(section) for key, section in diff.items() if not key.startswith("_"))
    check(f"{label}: graph diff rỗng ngoài mask", diff_total == 0,
          f"nodes py={len(py_dump['nodes'])} rust={len(rust_dump['nodes'])} diff={diff_total}")
    report_lines.append(
        f"\n### {label}\n\n- nodes: py={len(py_dump['nodes'])} rust={len(rust_dump['nodes'])}\n"
        f"- edges: py={len(py_dump['edges'])} rust={len(rust_dump['edges'])}\n"
        f"- diff_total (ngoài mask {sorted(MASKED_PROPS)}): **{diff_total}**\n"
    )
    if diff_total:
        report_lines.append("```json\n")
        report_lines.append(json.dumps(diff, indent=2, ensure_ascii=True, default=str)[:12000])
        report_lines.append("\n```\n")


# ── Scenarios ────────────────────────────────────────────────────────────────


def scenario_full(
    driver: FalkorDBDriver,
    root: Path,
    tag: str,
    host: str,
    port: int,
    report_lines: list[str],
) -> None:
    graph_py = f"p04_{tag}_py"
    graph_rust = f"p04_{tag}_rs"
    clean_graph(driver, graph_py)
    clean_graph(driver, graph_rust)

    print(f"[full:{tag}] python analyzer → {graph_py}")
    py_log, py_time = run_python_analyzer(root, f"parity_{tag}", graph_py, host, port)
    print(f"[full:{tag}] rust analyzer → {graph_rust}")
    rust_log, rust_time = run_rust_analyzer(root, f"parity_{tag}", graph_rust, host, port)

    py_scan = scan_result_of(py_log)
    rust_scan = scan_result_of(rust_log)
    check(f"{tag}: [SCAN_RESULT] byte-identical", py_scan == rust_scan,
          f"py={py_scan!r} rust={rust_scan!r}")
    report_lines.append(
        f"\n### scan_result {tag}\n\n- py: `{py_scan}`\n- rust: `{rust_scan}`\n"
        f"- duration: python={py_time:.1f}s rust={rust_time:.1f}s\n"
    )
    compare_graphs(driver, graph_py, graph_rust, f"FULL {tag}", report_lines)


def clean_graph(driver: FalkorDBDriver, graph: str) -> None:
    import asyncio

    asyncio.run(driver.execute_query("MATCH (n) DETACH DELETE n", {}, graph))


def scenario_incremental(
    driver: FalkorDBDriver,
    source_root: Path,
    max_files: int,
    host: str,
    port: int,
    report_lines: list[str],
) -> None:
    graph_py = "p04_inc_py"
    graph_rust = "p04_inc_rs"
    with tempfile.TemporaryDirectory(prefix="p04_inc_") as tmp:
        workdir = Path(tmp) / "corpus"
        workdir.mkdir()
        source_files = sorted(
            path
            for path in source_root.rglob("*.py")
            if not any(
                part in {"venv", ".venv", "node_modules", "__pycache__", "build", "dist", ".git"}
                for part in path.relative_to(source_root).parts
            )
        )[:max_files]
        if not source_files:
            check("incremental: corpus copy", False, "no source files")
            return
        py_files = []
        for path in source_files:
            target = workdir / path.relative_to(source_root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            py_files.append(target)

        clean_graph(driver, graph_py)
        clean_graph(driver, graph_rust)
        print(f"[inc] FULL seed trên copy ({len(py_files)} files)")
        run_python_analyzer(workdir, "parity_inc", graph_py, host, port)
        run_rust_analyzer(workdir, "parity_inc", graph_rust, host, port)

        # ── Mutate: sửa 1 file, thêm 1 file, xoá 1 file ─────────────────────
        modified = py_files[0]
        with open(modified, "a", encoding="utf-8") as handle:
            handle.write(
                "\n\ndef appended_parity_helper(values):\n"
                '    """Appended helper for parity incremental test."""\n'
                "    return sum(values)\n"
            )
        added = workdir / "parity_added_module.py"
        added.write_text(
            '"""Added module."""\n\n\ndef brand_new_function(payload):\n'
            "    return len(payload)\n",
            encoding="utf-8",
        )
        deleted = py_files[-1]
        deleted.unlink()

        changed_manifest = Path(tmp) / "changed.json"
        deleted_manifest = Path(tmp) / "deleted.json"
        changed_rel = [
            str(modified.relative_to(workdir)),
            "parity_added_module.py",
        ]
        changed_manifest.write_text(
            json.dumps({"files": sorted(changed_rel)}, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        deleted_manifest.write_text(
            json.dumps({"files": [str(deleted.relative_to(workdir))]}, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )

        print("[inc] incremental python")
        py_log, _ = run_python_analyzer(
            workdir, "parity_inc", graph_py, host, port,
            incremental=True,
            changed_manifest=changed_manifest,
            deleted_manifest=deleted_manifest,
        )
        print("[inc] incremental rust")
        rust_log, _ = run_rust_analyzer(
            workdir, "parity_inc", graph_rust, host, port,
            incremental=True,
            changed_manifest=changed_manifest,
            deleted_manifest=deleted_manifest,
        )

        py_cleanup = cleanup_counts_of(py_log)
        rust_cleanup = cleanup_counts_of(rust_log)
        check("incremental: cleanup counts khớp", py_cleanup == rust_cleanup,
              f"py={py_cleanup} rust={rust_cleanup}")
        report_lines.append(
            f"\n### incremental (corpus {len(py_files)} files từ {source_root.name})\n\n"
            f"- cleanup deleted_nodes: py={py_cleanup[0]} rust={rust_cleanup[0]}\n"
            f"- cleanup deleted_unknown: py={py_cleanup[1]} rust={rust_cleanup[1]}\n"
        )
        compare_graphs(driver, graph_py, graph_rust, "INCREMENTAL", report_lines)


def scenario_summary_schema(report_lines: list[str]) -> None:
    with tempfile.TemporaryDirectory(prefix="p04_summary_") as tmp:
        summary_path = Path(tmp) / "summary.json"
        log, _ = run_rust_analyzer(
            TESTDATA, "parity_summary", "p04_summary_rs", "127.0.0.1", 6379,
            summary_path=summary_path,
        )
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
        required = {
            "parser", "project_id", "project_name", "language", "repo",
            "incremental", "commit_sha_before", "commit_sha", "files",
            "functions", "classes", "relations", "calls", "written",
            "duration_seconds",
        }
        missing = required - set(payload)
        check("summary JSON schema (--summary-path)", not missing,
              f"missing={sorted(missing)}")
        report_lines.append(
            f"\n### summary schema\n\n- keys: {sorted(payload)}\n"
            f"- files={payload['files']} functions={payload['functions']}\n"
        )
        # [SCAN_RESULT] phải khớp số liệu summary
        scan = scan_result_of(log)
        expected_files = int(scan.split("files=")[1].split()[0])
        check("summary files khớp [SCAN_RESULT]", payload["files"] == expected_files,
              f"summary={payload['files']} scan={expected_files}")


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> int:
    global RUST_BIN

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6379)
    parser.add_argument("--rust-bin", default=str(RUST_BIN))
    parser.add_argument("--skip-stock", action="store_true")
    parser.add_argument("--report", default=str(REPORT_PATH))
    args = parser.parse_args()

    rust_bin = Path(args.rust_bin)
    RUST_BIN = rust_bin
    if not rust_bin.exists():
        print(f"Rust analyzer binary not found: {rust_bin}")
        print("Build first: cargo build --release -p analyzer-python")
        return 1

    report_lines = [
        "# Phase 04 — analyzer parity (python vs rust)",
        "",
        f"- chạy: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- stock: `{STOCK}`",
        f"- testdata: `{TESTDATA.relative_to(REPO)}`",
        f"- mask: `{sorted(MASKED_PROPS)}`",
    ]

    driver = FalkorDBDriver(host=args.host, port=args.port)

    scenario_full(driver, TESTDATA, "testdata", args.host, args.port, report_lines)
    scenario_summary_schema(report_lines)
    if not args.skip_stock and STOCK.exists():
        scenario_full(driver, STOCK, "stock", args.host, args.port, report_lines)
        scenario_incremental(driver, STOCK, max_files=30, host=args.host,
                             port=args.port, report_lines=report_lines)
    else:
        print("[warn] skip stock scenarios")

    report_lines.append(
        f"\n## Kết luận\n\n- FAILURES: {FAILURES if FAILURES else 'không có — PASS toàn bộ'}\n"
    )
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    print(f"\nreport → {report_path}")

    if FAILURES:
        print(f"FAILED: {len(FAILURES)} gate(s): {FAILURES}")
        return 1
    print("ALL GATES PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
