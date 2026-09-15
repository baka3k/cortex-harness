#!/usr/bin/env python3
"""Phase 05 parity gate — TypeScript analyzer Python vs Rust.

Dual-run `tools/ts/ts_analyzer.py` và `analyzer-ts` trên 2 graph FalkorDB
riêng (FULL testdata + stock/frontend + incremental), dump và so exact
ngoài mask chuẩn.

Usage (từ repo root):
    .venv/bin/python scripts/rust_parity/analyzer_parity_ts.py [--skip-stock]
"""

# === Phase-08 archive notice (2026-09-15) ===
# PY side archived at phase-08 cutover, fixtures = golden.
# Python analyzer entry points were retired at the phase-08 cutover;
# fixtures under tests/fixtures/ are now the golden reference.
# Do not attempt to re-run the Python side — tools/<lang>/<lang>_analyzer.py
# no longer exists. See plans/260915-analyzer-layer-rust-cutover/reports/phase08-cutover.md.
# === end archive notice ===


from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "code-tiny"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tools.graph.driver.falkordb_driver import FalkorDBDriver  # noqa: E402
from dual_write_diff import MASKED_PROPS, diff_dump, dump_graph  # noqa: E402

STOCK_FRONTEND = Path("/Users/hieplq1.aip/baka3k/stock/frontend")
TESTDATA = REPO / "tests" / "fixtures" / "ts-analyzer"
PY_BIN = REPO / ".venv" / "bin" / "python"
PY_ANALYZER = REPO / "code-tiny" / "tools" / "ts" / "ts_analyzer.py"
RUST_BIN = REPO / "rust" / "target" / "release" / "analyzer-ts"
REPORT_PATH = (
    REPO / "plans" / "260913-2130-rust-full-migration" / "reports"
    / "phase05-ts-parity.md"
)

SCAN_RE = re.compile(
    r"\[SCAN_RESULT\] parser=(\S+) files=(\d+) functions=(\d+) classes=(\d+)"
)

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def analyzer_env() -> dict:
    import os

    env = dict(os.environ)
    for key in [
        "QDRANT_CODE_PATH",
        "QDRANT_COLLECTION",
        "FALKORDB_URI",
        "FALKORDB_GRAPH",
        "PROJECT_ID",
        "CORTEX_EXTRA_IGNORE_DIRS",
        "REACT_ROLE_LLM_CLASSIFY",
    ]:
        env.pop(key, None)
    return env


def run_py(root: Path, project_id: str, graph: str, host: str, port: int,
           incremental: tuple[Path, Path] | None = None) -> str:
    cmd = [
        str(PY_BIN), str(PY_ANALYZER),
        "--config", "/dev/null",
        "--root", str(root),
        "--project-id", project_id,
        "--graph-provider", "falkordb",
        "--falkordb-uri", f"{host}:{port}",
        "--falkordb-graph", graph,
        "--disable-message-scan",
        "--verbose",
    ]
    if incremental:
        cmd.append("--incremental")
        changed, deleted = incremental
        if changed:
            cmd.extend(["--changed-files-manifest", str(changed)])
        if deleted:
            cmd.extend(["--deleted-files-manifest", str(deleted)])
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800,
                          env=analyzer_env())
    if proc.returncode != 0:
        raise RuntimeError(f"py analyzer failed:\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")
    return proc.stdout


def run_rust(root: Path, project_id: str, graph: str, host: str, port: int,
             incremental: tuple[Path, Path] | None = None) -> str:
    cmd = [
        str(RUST_BIN),
        "--root", str(root),
        "--project-id", project_id,
        "--graph-provider", "falkordb",
        "--falkordb-uri", f"{host}:{port}",
        "--falkordb-graph", graph,
        "--disable-message-scan",
        "--verbose",
    ]
    if incremental:
        cmd.append("--incremental")
        changed, deleted = incremental
        if changed:
            cmd.extend(["--changed-files-manifest", str(changed)])
        if deleted:
            cmd.extend(["--deleted-files-manifest", str(deleted)])
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if proc.returncode != 0:
        raise RuntimeError(f"rust analyzer failed:\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")
    return proc.stdout


def scan_result_of(log: str) -> str:
    matches = list(SCAN_RE.finditer(log))
    if not matches:
        raise RuntimeError(f"[SCAN_RESULT] not found:\n{log[-1500:]}")
    return matches[-1].group(0)


def clean_graph(driver: FalkorDBDriver, graph: str) -> None:
    import asyncio

    asyncio.run(driver.execute_query("MATCH (n) DETACH DELETE n", {}, graph))


def compare_graphs(driver: FalkorDBDriver, graph_py: str, graph_rust: str,
                   label: str, report: list[str]) -> None:
    py_dump = dump_graph(driver, graph_py)
    rust_dump = dump_graph(driver, graph_rust)
    diff = diff_dump(py_dump, rust_dump)
    diff_total = sum(len(v) for k, v in diff.items() if not k.startswith("_"))
    check(f"{label}: graph diff rỗng ngoài mask", diff_total == 0,
          f"nodes py={len(py_dump['nodes'])} rust={len(rust_dump['nodes'])} diff={diff_total}")
    report.append(
        f"\n### {label}\n\n- nodes: py={len(py_dump['nodes'])} rust={len(rust_dump['nodes'])}\n"
        f"- edges: py={len(py_dump['edges'])} rust={len(rust_dump['edges'])}\n"
        f"- diff_total: **{diff_total}**\n"
    )
    if diff_total:
        report.append("```json\n")
        report.append(json.dumps(diff, indent=2, ensure_ascii=True, default=str)[:16000])
        report.append("\n```\n")


def dual(driver: FalkorDBDriver, root: Path, tag: str, host: str, port: int,
         report: list[str], incremental: tuple[Path, Path] | None = None) -> None:
    graph_py = f"p05_ts_{tag}_py"
    graph_rust = f"p05_ts_{tag}_rs"
    clean_graph(driver, graph_py)
    clean_graph(driver, graph_rust)
    py_log = run_py(root, "parity_ts", graph_py, host, port, incremental)
    rust_log = run_rust(root, "parity_ts", graph_rust, host, port, incremental)
    py_scan, rust_scan = scan_result_of(py_log), scan_result_of(rust_log)
    check(f"{tag}: [SCAN_RESULT] byte-identical", py_scan == rust_scan,
          f"py={py_scan!r} rust={rust_scan!r}")
    report.append(f"\n### scan_result {tag}\n\n- py: `{py_scan}`\n- rust: `{rust_scan}`\n")
    compare_graphs(driver, graph_py, graph_rust, tag.upper(), report)


def scenario_incremental(driver: FalkorDBDriver, host: str, port: int, report: list[str]) -> None:
    import tempfile

    with tempfile.TemporaryDirectory(prefix="p05_ts_inc_") as tmp:
        workdir = Path(tmp) / "corpus"
        shutil.copytree(TESTDATA, workdir)
        dual(driver, workdir, "inc_seed", host, port, report)

        # Mutate: sửa HomeScreen (thêm navigate), thêm file, xoá SettingsScreen
        (workdir / "src" / "screens" / "HomeScreen.tsx").write_text(
            Path(workdir / "src" / "screens" / "HomeScreen.tsx").read_text(encoding="utf-8")
            + "\nexport function HomeBanner(): JSX.Element {\n"
            "  return <Text>banner</Text>;\n}\n",
            encoding="utf-8",
        )
        added = workdir / "src" / "screens" / "ModalScreen.tsx"
        added.write_text(
            "import React from 'react';\nimport { Text } from 'react-native';\n\n"
            "export function ModalScreen(): JSX.Element {\n"
            "  return <Text>modal</Text>;\n}\n",
            encoding="utf-8",
        )
        (workdir / "src" / "screens" / "SettingsScreen.tsx").unlink()

        changed_manifest = Path(tmp) / "changed.json"
        deleted_manifest = Path(tmp) / "deleted.json"
        changed_manifest.write_text(json.dumps({"files": [
            "src/screens/HomeScreen.tsx", "src/screens/ModalScreen.tsx",
        ]}) + "\n", encoding="utf-8")
        deleted_manifest.write_text(json.dumps({"files": [
            "src/screens/SettingsScreen.tsx",
        ]}) + "\n", encoding="utf-8")
        dual(driver, workdir, "inc_run", host, port, report,
             incremental=(changed_manifest, deleted_manifest))


def main() -> int:
    global RUST_BIN

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6379)
    parser.add_argument("--rust-bin", default=str(RUST_BIN))
    parser.add_argument("--skip-stock", action="store_true")
    args = parser.parse_args()
    RUST_BIN = Path(args.rust_bin)

    report = [
        "# Phase 05 — TypeScript analyzer parity (python vs rust)",
        "",
        f"- chạy: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- testdata: `{TESTDATA.relative_to(REPO)}`",
        f"- stock frontend: `{STOCK_FRONTEND}`",
        f"- mask: `{sorted(MASKED_PROPS)}`",
    ]
    driver = FalkorDBDriver(host=args.host, port=args.port)

    dual(driver, TESTDATA, "testdata_full", args.host, args.port, report)
    scenario_incremental(driver, args.host, args.port, report)
    if not args.skip_stock and STOCK_FRONTEND.exists():
        dual(driver, STOCK_FRONTEND, "stock_frontend", args.host, args.port, report)
    else:
        print("[warn] skip stock frontend")

    report.append(
        f"\n## Kết luận\n\n- FAILURES: {FAILURES if FAILURES else 'không có — PASS toàn bộ'}\n"
    )
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"\nreport → {REPORT_PATH.relative_to(REPO)}")
    if FAILURES:
        print(f"FAILED: {FAILURES}")
        return 1
    print("ALL GATES PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
