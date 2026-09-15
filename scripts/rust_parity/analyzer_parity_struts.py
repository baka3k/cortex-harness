#!/usr/bin/env python3
"""Phase 08 parity gate — Struts overlay analyzer Python vs Rust.

Dual-run: seed 2 graph FalkorDB bằng base java analyzer Python (journal
shared-shadow), rồi `tools/struts/struts_analyzer.py` vs `analyzer-struts`.
Gate: `--output` JSON byte-identical + `[SCAN_RESULT]` line + graph dump diff
rỗng ngoài mask + incremental round.
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
import re
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyzer_parity_p08_common import (  # noqa: E402
    PY_BIN, REPO, REPORTS, FAILURES, check, compare_files, scratch_dir, compare_graphs,
    header, run_overlay, seed_base_graph,
)
from tools.graph.driver.falkordb_driver import FalkorDBDriver  # noqa: E402

TESTDATA = REPO / "tests" / "fixtures" / "java-spring-overlays"
RUST_BIN = REPO / "rust" / "target" / "release" / "analyzer-struts"
PY_ANALYZER = REPO / "code-tiny" / "tools" / "struts" / "struts_analyzer.py"
REPORT_PATH = REPORTS / "phase08-struts-parity.md"
PROJECT_ID = "parity_struts"

SCAN_RE = re.compile(
    r"\[SCAN_RESULT\] parser=struts facts=(\d+) relationships=(\d+) diagnostics=(\d+) graph=(\d+)"
)


def common_flags(graph: str, host: str, port: int) -> list[str]:
    return [
        "--project-id", PROJECT_ID,
        "--graph-provider", "falkordb",
        "--falkordb-uri", f"{host}:{port}",
        "--falkordb-graph", graph,
        "--disable-message-scan",
        "--verbose",
    ]


def run_py(root: Path, graph: str, host: str, port: int, output: Path,
           incremental: bool) -> str:
    cmd = [
        str(PY_BIN), str(PY_ANALYZER),
        "--root", str(root),
        *common_flags(graph, host, port),
        "--output", str(output),
    ]
    if incremental:
        cmd.append("--incremental")
    return run_overlay(cmd)


def run_rust(root: Path, graph: str, host: str, port: int, output: Path,
             incremental: bool, rust_bin: Path) -> str:
    cmd = [
        str(rust_bin),
        "--root", str(root),
        *common_flags(graph, host, port),
        "--output", str(output),
    ]
    if incremental:
        cmd.append("--incremental")
    return run_overlay(cmd)


def scan_line(log: str) -> str:
    matches = list(SCAN_RE.finditer(log))
    if not matches:
        return "<missing>"
    return matches[-1].group(0)


def dual(driver: FalkorDBDriver, root: Path, tag: str, host: str, port: int,
         scratch: Path, report: list[str], rust_bin: Path,
         incremental: bool = False) -> None:
    graph_py = f"p08_struts_{tag}_py"
    graph_rust = f"p08_struts_{tag}_rs"
    output_py = scratch / f"{tag}_py" / "struts_analysis.json"
    output_rs = scratch / f"{tag}_rs" / "struts_analysis.json"
    output_py.parent.mkdir(parents=True, exist_ok=True)
    output_rs.parent.mkdir(parents=True, exist_ok=True)

    seed_base_graph(driver, root, PROJECT_ID, graph_py, host, port)
    seed_base_graph(driver, root, PROJECT_ID, graph_rust, host, port)

    py_log = run_py(root, graph_py, host, port, output_py, incremental)
    rs_log = run_rust(root, graph_rust, host, port, output_rs, incremental, rust_bin)

    py_scan, rs_scan = scan_line(py_log), scan_line(rs_log)
    check(f"{tag}: [SCAN_RESULT] byte-identical", py_scan == rs_scan,
          f"py={py_scan!r} rs={rs_scan!r}")
    report.append(f"\n### scan_result {tag}\n\n- py: `{py_scan}`\n- rs: `{rs_scan}`\n")

    compare_files(output_py, output_rs, f"{tag} analysis JSON", report)
    compare_graphs(driver, graph_py, graph_rust, tag.upper(), report)


def scenario_incremental(driver: FalkorDBDriver, host: str, port: int,
                         report: list[str], rust_bin: Path, scratch: Path) -> None:
    tmp = scratch_dir("p08_struts_inc")
    try:
        workdir = tmp / "corpus"
        shutil.copytree(TESTDATA, workdir)

        # FULL seed trên corpus copy.
        dual(driver, workdir, "inc_seed", host, port, scratch, report, rust_bin)

        # Mutate: thêm action mới vào struts-admin.xml.
        admin = workdir / "src" / "main" / "resources" / "struts-admin.xml"
        admin.write_text(
            admin.read_text(encoding="utf-8").replace(
                "</package>",
                "    <action name=\"audit\" class=\"com.example.shop.struts.AdminAction\">\n"
                "      <result name=\"success\">/WEB-INF/jsp/catalog.jsp</result>\n"
                "    </action>\n  </package>",
            ),
            encoding="utf-8",
        )
        dual(driver, workdir, "inc_run", host, port, scratch, report, rust_bin,
             incremental=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6379)
    parser.add_argument("--rust-bin", default=str(RUST_BIN))
    args = parser.parse_args()

    rust_bin = Path(args.rust_bin)
    report = header(
        "Struts overlay", TESTDATA, rust_bin,
        ["- base seed: java analyzer Python (journal shared-shadow) trên CẢ HAI graph"],
    )
    driver = FalkorDBDriver(host=args.host, port=args.port)

    tmp = scratch_dir("p08_struts")
    try:
        scratch = tmp
        dual(driver, TESTDATA, "testdata_full", args.host, args.port, scratch, report, rust_bin)
        scenario_incremental(driver, args.host, args.port, report, rust_bin, scratch)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

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
