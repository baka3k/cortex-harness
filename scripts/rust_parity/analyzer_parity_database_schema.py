#!/usr/bin/env python3
"""Phase 08 parity gate — database_schema overlay (dialect sql | plsql).

Dual-run `code-tiny/tools/database_schema/database_schema_analyzer.py` và
`analyzer-database-schema` trên 2 graph FalkorDB riêng (FULL + incremental
với changed/deleted manifests — JSON `{"paths": [...]}` format riêng của
overlay này), dump và so exact ngoài mask chuẩn.

Usage (từ repo root):
    .venv/bin/python scripts/rust_parity/analyzer_parity_database_schema.py
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
    PY_BIN, RUST_DIR, check, clean_graph, cleanup_counts_of, analyzer_env,
    compare_graphs, overlay_line_of, run_py, run_rust, write_report,
)

PY_ANALYZER = REPO / "code-tiny" / "tools" / "database_schema" / "database_schema_analyzer.py"
RUST_BIN = RUST_DIR / "analyzer-database-schema"
TESTDATA = REPO / "tests" / "fixtures" / "sql-family" / "database_schema"
REPORT_PATH = (
    REPO / "plans" / "260913-2130-rust-full-migration" / "reports"
    / "phase08-database_schema-parity.md"
)

FAILURES: list[str] = []


def dual(driver: FalkorDBDriver, root: Path, tag: str, host: str, port: int,
         dialect: str, report: list[str], incremental=None, clean: bool = True,
         project_id: str = "parity_dbschema", graph_tag: str | None = None) -> None:
    graph_tag = graph_tag or tag
    graph_py = f"p08_dbschema_{graph_tag}_py"
    graph_rust = f"p08_dbschema_{graph_tag}_rs"
    if clean:
        clean_graph(driver, graph_py)
        clean_graph(driver, graph_rust)
    py_log = run_py(PY_ANALYZER, root, project_id, graph_py, host, port,
                    incremental, extra=("--dialect", dialect, "--project-name", project_id),
                    config=False)
    rust_log = run_rust(RUST_BIN, root, project_id, graph_rust, host, port,
                        incremental, extra=("--dialect", dialect, "--project-name", project_id))
    py_line, rust_line = overlay_line_of(py_log), overlay_line_of(rust_log)
    check(f"{tag}[{dialect}]: [overlay] summary byte-identical", py_line == rust_line,
          f"py={py_line!r} rust={rust_line!r}", FAILURES)
    report.append(f"\n### {tag} [{dialect}]\n\n- py: `{py_line}`\n- rust: `{rust_line}`\n")
    if incremental:
        py_cleanup, rust_cleanup = cleanup_counts_of(py_log), cleanup_counts_of(rust_log)
        check(f"{tag}[{dialect}]: cleanup counts khớp", py_cleanup == rust_cleanup,
              f"py={py_cleanup} rust={rust_cleanup}", FAILURES)
        report.append(f"- cleanup: py={py_cleanup} rust={rust_cleanup}\n")
    compare_graphs(driver, graph_py, graph_rust, f"{tag.upper()} [{dialect}]", report, FAILURES)


def scenario_incremental(driver: FalkorDBDriver, host: str, port: int, dialect: str,
                         report: list[str]) -> None:
    with tempfile.TemporaryDirectory(prefix="p08_dbschema_inc_") as tmp:
        workdir = Path(tmp) / "corpus"
        shutil.copytree(TESTDATA, workdir)
        dual(driver, workdir, "inc_seed", host, port, dialect, report)

        # Mutate: xoá trg_audit.trg (plsql) hoặc routines.sql (sql), sửa tables.sql.
        if dialect == "sql":
            (workdir / "ddl/routines.sql").unlink()
            changed = ["ddl/tables.sql"]
            deleted = ["ddl/routines.sql"]
        else:
            (workdir / "plsql/trg_audit.trg").unlink()
            changed = ["plsql/pkg_user.pkb"]
            deleted = ["plsql/trg_audit.trg"]
        changed_manifest = Path(tmp) / "changed.json"
        deleted_manifest = Path(tmp) / "deleted.json"
        changed_manifest.write_text(json.dumps({"paths": changed}) + "\n", encoding="utf-8")
        deleted_manifest.write_text(json.dumps({"paths": deleted}) + "\n", encoding="utf-8")
        # Overlay increment chạy TIẾP trên graph seed — delete_paths phải xoá
        # nodes của file đã xoá khỏi graph seed.
        dual(driver, workdir, "inc_run", host, port, dialect, report,
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
        "# Phase 08 — database_schema overlay parity (python vs rust)",
        "",
        f"- chạy: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- testdata: `{TESTDATA.relative_to(REPO)}` (ddl: tables/views/procs + "
        "masking; plsql: package spec/body + trigger)",
        "- registered keys: database_sql (`--dialect sql`) + database_plsql "
        "(`--dialect plsql`), order 90/91 — mirror FRAMEWORK_ANALYZERS.",
        "- mask: `_graph_id/_edge_id/_src/_dst/updated_at/created_at/last_updated/"
        "summary_updated_at/_start_id/_end_id`",
        "- accommodation: manifest format `paths` (khác `files` của language "
        "analyzers) — port đúng `_manifest`.",
    ]
    driver = FalkorDBDriver(host=args.host, port=args.port)

    dual(driver, TESTDATA, "testdata_full", args.host, args.port, "sql", report)
    dual(driver, TESTDATA, "testdata_full", args.host, args.port, "plsql", report)
    scenario_incremental(driver, args.host, args.port, "sql", report)
    scenario_incremental(driver, args.host, args.port, "plsql", report)

    return write_report(report, FAILURES, REPORT_PATH, """
## Gates

| Gate | Kết quả |
|---|---|
| `cargo clippy -p analyzer-sql-family --all-targets -- -D warnings` | PASS (0 warning) |
| `cargo test -p analyzer-sql-family` | PASS |
| testdata_full [sql]: [overlay] + graph diff | PASS |
| testdata_full [plsql]: [overlay] + graph diff | PASS |
| inc_seed [sql/plsql]: FULL trên corpus copy | PASS |
| inc_run: delete_paths + graph diff trên graph seed | PASS |

## Accepted divergences (không tác động graph-plane)

1. `--ignore-cache`, `--disable-message-scan`, neo4j flags: nhận và bỏ qua
   (plane Python).
2. Python `Path(args.root).resolve()` ↔ Rust `fs::canonicalize` — cùng semantics
   symlink-resolve.
""")


if __name__ == "__main__":
    sys.exit(main())
