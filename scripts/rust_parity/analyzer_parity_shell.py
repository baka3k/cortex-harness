#!/usr/bin/env python3
"""Phase 05 parity gate — shell analyzer Python vs Rust.

Dual-run `tools/shell/shell_analyzer.py` và `analyzer-shell` trên 2 graph
FalkorDB riêng (FULL + incremental + program-mapping ledger), dump và so
exact ngoài mask chuẩn.

Usage (từ repo root):
    .venv/bin/python scripts/rust_parity/analyzer_parity_shell.py [--skip-stock]
"""

from __future__ import annotations

import argparse
import json
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
TESTDATA = REPO / "tests" / "fixtures" / "shell-application"
PY_BIN = REPO / ".venv" / "bin" / "python"
PY_ANALYZER = REPO / "code-tiny" / "tools" / "shell" / "shell_analyzer.py"
RUST_BIN = REPO / "rust" / "target" / "release" / "analyzer-shell"
REPORT_PATH = (
    REPO / "plans" / "260913-2130-rust-full-migration" / "reports"
    / "phase05-shell-parity.md"
)

SCAN_RE = re.compile(
    r"\[SCAN_RESULT\] parser=shell files=(\d+) functions=(\d+) vectors=(\d+) "
    r"vector_status=(\w+)"
)
CLEANUP_RE = re.compile(
    r"\[cleanup\]\[graph\] deleted_nodes=(\d+) deleted_unknown_functions=(\d+)"
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
        "SHELL_PROGRAM_MAPPING_LEDGER",
    ]:
        env.pop(key, None)
    return env


def run_py(root: Path, project_id: str, graph: str, host: str, port: int,
           ledger: Path | None = None, incremental: tuple[Path, Path] | None = None) -> str:
    cmd = [
        str(PY_BIN), str(PY_ANALYZER),
        "--root", str(root),
        "--project-id", project_id,
        "--graph-provider", "falkordb",
        "--falkordb-uri", f"{host}:{port}",
        "--falkordb-graph", graph,
        "--disable-message-scan",
        "--verbose",
    ]
    if ledger:
        cmd.extend(["--program-mapping-ledger", str(ledger)])
    if incremental:
        cmd.append("--incremental")
        changed, deleted = incremental
        if changed:
            cmd.extend(["--changed-files-manifest", str(changed)])
        if deleted:
            cmd.extend(["--deleted-files-manifest", str(deleted)])
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900,
                          env=analyzer_env())
    if proc.returncode != 0:
        raise RuntimeError(f"py analyzer failed:\n{proc.stdout[-1500:]}\n{proc.stderr[-1500:]}")
    return proc.stdout


def run_rust(root: Path, project_id: str, graph: str, host: str, port: int,
             ledger: Path | None = None, incremental: tuple[Path, Path] | None = None) -> str:
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
    if ledger:
        cmd.extend(["--program-mapping-ledger", str(ledger)])
    if incremental:
        cmd.append("--incremental")
        changed, deleted = incremental
        if changed:
            cmd.extend(["--changed-files-manifest", str(changed)])
        if deleted:
            cmd.extend(["--deleted-files-manifest", str(deleted)])
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if proc.returncode != 0:
        raise RuntimeError(f"rust analyzer failed:\n{proc.stdout[-1500:]}\n{proc.stderr[-1500:]}")
    return proc.stdout


def scan_result_of(log: str) -> str:
    matches = list(SCAN_RE.finditer(log))
    if not matches:
        raise RuntimeError(f"[SCAN_RESULT] not found:\n{log[-1200:]}")
    return matches[-1].group(0)


def cleanup_counts_of(log: str) -> tuple[int, int]:
    matches = CLEANUP_RE.findall(log)
    return tuple(int(v) for v in matches[-1]) if matches else (0, 0)  # type: ignore[return-value]


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
        report.append(json.dumps(diff, indent=2, ensure_ascii=True, default=str)[:12000])
        report.append("\n```\n")


def dual(driver: FalkorDBDriver, root: Path, tag: str, host: str, port: int,
         report: list[str], ledger: Path | None = None,
         incremental: tuple[Path, Path] | None = None) -> None:
    graph_py = f"p05_shell_{tag}_py"
    graph_rust = f"p05_shell_{tag}_rs"
    clean_graph(driver, graph_py)
    clean_graph(driver, graph_rust)
    py_log = run_py(root, "parity_shell", graph_py, host, port, ledger, incremental)
    rust_log = run_rust(root, "parity_shell", graph_rust, host, port, ledger, incremental)
    py_scan, rust_scan = scan_result_of(py_log), scan_result_of(rust_log)
    check(f"{tag}: [SCAN_RESULT] byte-identical", py_scan == rust_scan,
          f"py={py_scan!r} rust={rust_scan!r}")
    report.append(f"\n### scan_result {tag}\n\n- py: `{py_scan}`\n- rust: `{rust_scan}`\n")
    if incremental:
        py_cleanup, rust_cleanup = cleanup_counts_of(py_log), cleanup_counts_of(rust_log)
        check(f"{tag}: cleanup counts khớp", py_cleanup == rust_cleanup,
              f"py={py_cleanup} rust={rust_cleanup}")
        report.append(f"- cleanup: py={py_cleanup} rust={rust_cleanup}\n")
    compare_graphs(driver, graph_py, graph_rust, tag.upper(), report)


def scenario_incremental(driver: FalkorDBDriver, host: str, port: int, report: list[str]) -> None:
    with tempfile.TemporaryDirectory(prefix="p05_shell_inc_") as tmp:
        workdir = Path(tmp) / "corpus"
        workdir.mkdir()
        for name in ("batch_entry.sh", "other_target.sh", "settings.ini"):
            shutil.copy2(TESTDATA / name, workdir / name)
        shutil.copy2(TESTDATA / "program_mappings.json", workdir / "program_mappings.json")

        ledger = workdir / "program_mappings.json"
        # FULL seed (có ledger để lineage path được exercise)
        dual(driver, workdir, "inc_seed", host, port, report, ledger=ledger)

        # Mutate: sửa batch_entry.sh (thêm function + invocation), thêm file, xoá file
        (workdir / "batch_entry.sh").write_text(
            "#!/bin/sh\n"
            "CONFIG=settings.ini\n\n"
            "run_batch() {\n"
            "    . other_target.sh\n"
            "    grep 'MODE' \"${CONFIG}\" | awk -F: '{print $2}'\n"
            "}\n\n"
            "added_parity_helper() {\n"
            "    echo helper step\n"
            "}\n\n"
            "source missing.sh\n",
            encoding="utf-8",
        )
        added = workdir / "parity_added.sh"
        added.write_text("#!/bin/sh\nuname -a\n", encoding="utf-8")
        (workdir / "other_target.sh").unlink()

        changed_manifest = Path(tmp) / "changed.json"
        deleted_manifest = Path(tmp) / "deleted.json"
        changed_manifest.write_text(
            json.dumps({"files": ["batch_entry.sh", "parity_added.sh"]}) + "\n",
            encoding="utf-8",
        )
        deleted_manifest.write_text(
            json.dumps({"files": ["other_target.sh"]}) + "\n", encoding="utf-8"
        )
        dual(driver, workdir, "inc_run", host, port, report, ledger=ledger,
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
        "# Phase 05 — shell analyzer parity (python vs rust)",
        "",
        f"- chạy: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- testdata: `{TESTDATA.relative_to(REPO)}` (có program-mapping ledger)",
        f"- mask: `{sorted(MASKED_PROPS)}`",
    ]
    driver = FalkorDBDriver(host=args.host, port=args.port)

    dual(driver, TESTDATA, "testdata_full", args.host, args.port, report,
         ledger=TESTDATA / "program_mappings.json")
    dual(driver, TESTDATA, "testdata_no_ledger", args.host, args.port, report)
    scenario_incremental(driver, args.host, args.port, report)
    if not args.skip_stock and STOCK.exists():
        dual(driver, STOCK, "stock_full", args.host, args.port, report)
    else:
        print("[warn] skip stock")

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
