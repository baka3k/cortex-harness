#!/usr/bin/env python3
"""Phase 08 parity gate — Servlet/JSP overlay analyzer Python vs Rust.

Dual-run: seed 2 graph FalkorDB bằng base java analyzer Python (journal
shared-shadow), rồi `tools/servlet_jsp/servlet_jsp_analyzer.py` vs
`analyzer-servlet-jsp`. Gate: preview artifact byte-identical + summary JSON
line + graph dump diff rỗng ngoài mask + incremental round.
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
RUST_BIN = REPO / "rust" / "target" / "release" / "analyzer-servlet-jsp"
PY_ANALYZER = REPO / "code-tiny" / "tools" / "servlet_jsp" / "servlet_jsp_analyzer.py"
REPORT_PATH = REPORTS / "phase08-servlet_jsp-parity.md"
PROJECT_ID = "parity_servlet_jsp"


def common_flags(graph: str, host: str, port: int) -> list[str]:
    return [
        "--project-id", PROJECT_ID,
        "--graph-provider", "falkordb",
        "--falkordb-uri", f"{host}:{port}",
        "--falkordb-graph", graph,
        "--disable-message-scan",
        "--verbose",
    ]


def run_py(root: Path, graph: str, host: str, port: int, preview: Path,
           cache_dir: Path, incremental: tuple[Path, Path] | None) -> str:
    cmd = [
        str(PY_BIN), str(PY_ANALYZER),
        "--root", str(root),
        *common_flags(graph, host, port),
        "--servlet-jsp-preview-output", str(preview),
        "--cache-dir", str(cache_dir),
    ]
    if incremental:
        changed, deleted = incremental
        cmd.append("--incremental")
        if changed:
            cmd.extend(["--changed-files-manifest", str(changed)])
        if deleted:
            cmd.extend(["--deleted-files-manifest", str(deleted)])
    return run_overlay(cmd)


def run_rust(root: Path, graph: str, host: str, port: int, preview: Path,
             cache_dir: Path, incremental: tuple[Path, Path] | None,
             rust_bin: Path) -> str:
    cmd = [
        str(rust_bin),
        "--root", str(root),
        *common_flags(graph, host, port),
        "--servlet-jsp-preview-output", str(preview),
        "--cache-dir", str(cache_dir),
    ]
    if incremental:
        changed, deleted = incremental
        cmd.append("--incremental")
        if changed:
            cmd.extend(["--changed-files-manifest", str(changed)])
        if deleted:
            cmd.extend(["--deleted-files-manifest", str(deleted)])
    return run_overlay(cmd)


def summary_line(log: str) -> str:
    for line in reversed(log.splitlines()):
        stripped = line.strip()
        if stripped.startswith('{"analyzer":"servlet_jsp"'):
            return stripped
    return "<missing>"


def dual(driver: FalkorDBDriver, root: Path, tag: str, host: str, port: int,
         scratch: Path, report: list[str], rust_bin: Path,
         incremental: tuple[Path, Path] | None = None) -> None:
    graph_py = f"p08_servlet_jsp_{tag}_py"
    graph_rust = f"p08_servlet_jsp_{tag}_rs"
    preview_py = scratch / f"{tag}_py" / "servlet_jsp_preview.json"
    preview_rs = scratch / f"{tag}_rs" / "servlet_jsp_preview.json"
    cache_py = scratch / f"{tag}_py" / "cache"
    cache_rs = scratch / f"{tag}_rs" / "cache"
    for directory in (preview_py.parent, preview_rs.parent, cache_py, cache_rs):
        directory.mkdir(parents=True, exist_ok=True)

    seed_base_graph(driver, root, PROJECT_ID, graph_py, host, port)
    seed_base_graph(driver, root, PROJECT_ID, graph_rust, host, port)

    py_log = run_py(root, graph_py, host, port, preview_py, cache_py, incremental)
    rs_log = run_rust(root, graph_rust, host, port, preview_rs, cache_rs, incremental, rust_bin)

    py_summary, rs_summary = summary_line(py_log), summary_line(rs_log)
    # "preview" chứa đường dẫn scratch riêng của từng backend (_py/_rs) —
    # normalize trước khi so (giá trị thật đã được gate bởi preview JSON
    # byte-identical).
    normalize = lambda text: re.sub(r'"preview":"[^"]*"', '"preview":"<normalized>"', text)
    check(f"{tag}: summary JSON byte-identical (preview path normalized)",
          normalize(py_summary) == normalize(rs_summary),
          f"py={py_summary!r} rs={rs_summary!r}")
    report.append(f"\n### summary {tag}\n\n- py: `{py_summary}`\n- rs: `{rs_summary}`\n")

    compare_files(preview_py, preview_rs, f"{tag} preview JSON", report)
    compare_graphs(driver, graph_py, graph_rust, tag.upper(), report)


def scenario_incremental(driver: FalkorDBDriver, host: str, port: int,
                         report: list[str], rust_bin: Path, scratch: Path) -> None:
    tmp = scratch_dir("p08_sjsp_inc")
    try:
        workdir = tmp / "corpus"
        shutil.copytree(TESTDATA, workdir)

        # FULL seed trên corpus copy (snapshots nằm trong cache từng backend).
        dual(driver, workdir, "inc_seed", host, port, scratch, report, rust_bin)

        # Mutate: thêm EL + servlet handler mới, xoá footer.jsp include target.
        jsp = workdir / "src" / "main" / "webapp" / "WEB-INF" / "jsp" / "catalog.jsp"
        jsp.write_text(
            jsp.read_text(encoding="utf-8").replace(
                "<h1>${param.view}</h1>",
                "<h1>${param.view}</h1>\n<h2>${requestScope.viewName.upper}</h2>",
            ),
            encoding="utf-8",
        )
        servlet = workdir / "src" / "main" / "java" / "com" / "example" / "shop" / "web" / "AdminServlet.java"
        servlet.write_text(
            servlet.read_text(encoding="utf-8").replace(
                "    @Override\n    public void destroy() {",
                "    @Override\n    protected void doPost(javax.servlet.http.HttpServletRequest request,"
                " javax.servlet.http.HttpServletResponse response)\n"
                "            throws java.io.IOException {\n"
                "        response.sendRedirect(request.getParameter(\"next\"));\n    }\n\n"
                "    @Override\n    public void destroy() {",
            ),
            encoding="utf-8",
        )
        changed_manifest = Path(tmp) / "changed.json"
        deleted_manifest = Path(tmp) / "deleted.json"
        changed_manifest.write_text(json.dumps({"files": [
            "src/main/webapp/WEB-INF/jsp/catalog.jsp",
            "src/main/java/com/example/shop/web/AdminServlet.java",
        ]}) + "\n", encoding="utf-8")
        deleted_manifest.write_text(json.dumps({"files": []}) + "\n", encoding="utf-8")
        dual(driver, workdir, "inc_run", host, port, scratch, report, rust_bin,
             incremental=(changed_manifest, deleted_manifest))
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
        "Servlet/JSP overlay", TESTDATA, rust_bin,
        ["- base seed: java analyzer Python (journal shared-shadow) trên CẢ HAI graph"],
    )
    driver = FalkorDBDriver(host=args.host, port=args.port)

    tmp = scratch_dir("p08_sjsp")
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
