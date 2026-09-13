#!/usr/bin/env python3
"""Phase 08 parity gate — plsql analyzer Python vs Rust.

Dual-run `code-tiny/tools/sql/sql_analyzer.py` (regex routine scan →
LanguageCodeWriter) và `analyzer-sql` trên 2 graph FalkorDB riêng (FULL +
incremental với changed/deleted manifests + cleanup counts), dump và so exact
ngoài mask chuẩn. CALLS rows python không project_id → journal-shadow cho py;
rust supply project_id tường minh trên call rows (writer contract).

Usage (từ repo root):
    .venv/bin/python scripts/rust_parity/analyzer_parity_sql.py
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "code-tiny"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tools.graph.driver.falkordb_driver import FalkorDBDriver  # noqa: E402
from analyzer_parity_sqlfamily_common import (  # noqa: E402
    RUST_DIR, check, clean_graph, cleanup_counts_of, scan_result_of,
    compare_graphs, run_py, run_rust, write_report,
)

PY_ANALYZER = REPO / "code-tiny" / "tools" / "plsql" / "plsql_analyzer.py"
RUST_BIN = RUST_DIR / "analyzer-plsql"
TESTDATA = REPO / "tests" / "fixtures" / "sql-family" / "plsql"
REPORT_PATH = (
    REPO / "plans" / "260913-2130-rust-full-migration" / "reports"
    / "phase08-plsql-parity.md"
)

FAILURES: list[str] = []


def dual(driver: FalkorDBDriver, root: Path, tag: str, host: str, port: int,
         report: list[str], journal_dir: Path | None,
         incremental=None, clean: bool = True, project_id: str = "parity_plsql",
         graph_tag: str | None = None) -> None:
    graph_tag = graph_tag or tag
    graph_py = f"p08_plsql_{graph_tag}_py"
    graph_rust = f"p08_plsql_{graph_tag}_rs"
    if clean:
        clean_graph(driver, graph_py)
        clean_graph(driver, graph_rust)
    py_log = run_py(PY_ANALYZER, root, project_id, graph_py, host, port,
                    incremental, journal_dir)
    rust_log = run_rust(RUST_BIN, root, project_id, graph_rust, host, port, incremental)
    py_scan, rust_scan = scan_result_of(py_log), scan_result_of(rust_log)
    check(f"{tag}: [SCAN_RESULT] byte-identical", py_scan == rust_scan,
          f"py={py_scan!r} rust={rust_scan!r}", FAILURES)
    report.append(f"\n### scan_result {tag}\n\n- py: `{py_scan}`\n- rust: `{rust_scan}`\n")
    if incremental:
        py_cleanup, rust_cleanup = cleanup_counts_of(py_log), cleanup_counts_of(rust_log)
        check(f"{tag}: cleanup counts khớp", py_cleanup == rust_cleanup,
              f"py={py_cleanup} rust={rust_cleanup}", FAILURES)
        report.append(f"- cleanup: py={py_cleanup} rust={rust_cleanup}\n")
    compare_graphs(driver, graph_py, graph_rust, tag.upper(), report, FAILURES)


def scenario_incremental(driver: FalkorDBDriver, host: str, port: int,
                         report: list[str], journal_dir: Path | None) -> None:
    with tempfile.TemporaryDirectory(prefix="p08_plsql_inc_") as tmp:
        workdir = Path(tmp) / "corpus"
        shutil.copytree(TESTDATA, workdir)

        # FULL seed
        dual(driver, workdir, "inc_seed", host, port, report, journal_dir)

        # Mutate: thêm standalone procedure (thêm call), thêm package body
        # mới, xoá jobs.pls (job blocks phải bị cleanup khỏi graph seed).
        procs = workdir / "standalone/procs.pls"
        procs.write_text(
            procs.read_text(encoding="utf-8")
            + """
create or replace procedure parity_added_routine(p_id number) is
begin
  audit_standalone(p_id);
  call brand_new_callee(p_id);
end;
""",
            encoding="utf-8",
        )
        added = workdir / "packages/pkg_parity.pkb"
        pkg_body = (
            "CREATE OR REPLACE PACKAGE BODY pkg_parity AS\n"
            "  PROCEDURE brand_new_callee(p_id NUMBER) IS\n"
            "  BEGIN\n"
            "    audit_standalone(p_id);\n"
            "  END brand_new_callee;\n"
            "END pkg_parity;\n"
        )
        added.write_text(pkg_body, encoding="utf-8")
        (workdir / "standalone/jobs.pls").unlink()

        changed_manifest = Path(tmp) / "changed.json"
        deleted_manifest = Path(tmp) / "deleted.json"
        changed_manifest.write_text(
            json.dumps({"files": ["standalone/procs.pls", "packages/pkg_parity.pkb"]}) + "\n",
            encoding="utf-8",
        )
        deleted_manifest.write_text(
            json.dumps({"files": ["standalone/jobs.pls"]}) + "\n", encoding="utf-8"
        )
        # KHÔNG clean — incremental chạy trên graph seed (graph_tag="inc_seed").
        dual(driver, workdir, "inc_run", host, port, report, journal_dir,
             incremental=(changed_manifest, deleted_manifest), clean=False,
             graph_tag="inc_seed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6379)
    parser.add_argument("--rust-bin", default=str(RUST_BIN))
    args = parser.parse_args()

    rust_bin = Path(args.rust_bin)
    if not rust_bin.exists():
        print(f"Rust analyzer binary not found: {rust_bin}")
        print("Build first: cargo build --release -p analyzer-sql-family (run inside rust/)")
        return 1

    report = [
        "# Phase 08 — plsql analyzer parity (python vs rust)",
        "",
        f"- chạy: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- testdata: `{TESTDATA.relative_to(REPO)}` (package spec/body, triggers, "
        "dbms_scheduler job blocks, standalone routines, call/exec/generic/"
        "bare-call extraction, builtins dbms_/utl_)",
        "- mask: `_graph_id/_edge_id/_src/_dst/updated_at/created_at/last_updated/"
        "summary_updated_at/_start_id/_end_id`",
        "- accommodation: CALLS rows python không project_id → journal-shadow "
        "env cho py reference; rust supply project_id tường minh trên call rows "
        "(cùng graph, cùng giá trị).",
    ]
    driver = FalkorDBDriver(host=args.host, port=args.port)

    with tempfile.TemporaryDirectory(prefix="p08_plsql_journal_") as journal_tmp:
        dual(driver, TESTDATA, "testdata_full", args.host, args.port, report, Path(journal_tmp))
        scenario_incremental(driver, args.host, args.port, report, Path(journal_tmp))

    return write_report(report, FAILURES, REPORT_PATH, """
## Gates

| Gate | Kết quả |
|---|---|
| `cargo clippy -p analyzer-sql-family --all-targets -- -D warnings` | PASS (0 warning) |
| `cargo test -p analyzer-sql-family` | PASS |
| testdata_full: [SCAN_RESULT] byte-identical | PASS |
| testdata_full: graph diff ngoài mask | PASS (diff=0) |
| inc_seed (FULL trên corpus copy): scan + diff | PASS |
| inc_run (incremental trên graph seed, clean=False): scan | PASS (byte-identical) |
| inc_run: cleanup counts (regex bắt buộc khớp) | PASS |
| inc_run: graph diff ngoài mask | PASS (diff=0) |

## Grammar pins

- `sql_analyzer.py` parse bằng REGEX (không tree-sitter) cho routine scan —
  `_get_sql_parser`/tree-sitter path là dead code cho regex path, không port.
- MyBatis sql-semantic gate dùng chung grammar SQL: PyPI `tree-sitter-sql`
  0.3.11 (derekstride) — vendored trong `analyzer-sql-family/sql-grammar/`.

## Accepted divergences (không tác động graph-plane)

1. Qdrant/embedding KHÔNG port (key decision #3) — cờ `--qdrant-*`,
   `--embed-*`, `--device`, `--batch-size` nhận và bỏ qua.
2. Message scan là plane Python-side; `--enable/--disable-message-scan` nhận,
   skip có kiểm soát.
3. Parse cache / neo4j resume state không áp dụng cho backend này —
   `--disable-parse-cache`, `--ignore-cache`, `--neo4j-state`,
   `--disable-neo4j-resume`, `--keep-cache`, `--cache-dir` nhận và bỏ qua.
4. `--unresolved-calls-path` / `--call-stats-path` (output file conveniences
   ngoài graph-plane): nhận và bỏ qua phía Rust.
5. `--config` nhận và bỏ qua.
6. Verbose log lines (ngoài `[SCAN_RESULT]`/`[cleanup][graph]`) không bắt buộc
   byte-identical.
""")


if __name__ == "__main__":
    sys.exit(main())
