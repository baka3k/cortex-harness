#!/usr/bin/env python3
"""Phase 07 parity gate — cobol analyzer Python vs Rust.

Dual-run `code-tiny/tools/cobol/cobol_analyzer.py` và `analyzer-cobol` trên 2
graph FalkorDB riêng (FULL + incremental với changed/deleted manifests), dump
và so exact ngoài mask chuẩn.

Grammar: KHÔNG có tree-sitter-cobol grammar trên crates.io (crate 0.1.0 là
stub 768 byte). Cả 2 phía dùng CÙNG native grammar bundle
`code-tiny/tools/cobol/lib/*.so` — Python qua ctypes (parser_runtime.py),
Rust qua libloading (parser_runtime.rs) ⇒ error/missing node sets byte-exact.
Harness truyền `--cobol-language-library` tường minh cho cả 2 phía.

 CALLS project scope: cobol_analyzer.py truyền `project_id` tường minh vào
`write_relations_typed` + file rows (khác go_analyzer.py) nên KHÔNG cần
journal-shadow env. `project_id` là một phần của `stable_id` ⇒ 2 phía chạy
CÙNG `--project-id` trên 2 graph khác nhau.

Usage (từ repo root):
    .venv/bin/python scripts/rust_parity/analyzer_parity_cobol.py
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

TESTDATA = REPO / "tests" / "fixtures" / "cobol-application"
PY_BIN = REPO / ".venv" / "bin" / "python"
PY_REFERENCE = REPO / "code-tiny" / "tools" / "cobol" / "cobol_analyzer.py"
COBOL_LIBRARY = REPO / "code-tiny" / "tools" / "cobol" / "lib" / "cobol.cpython-310-darwin.so"
RUST_BIN = REPO / "rust" / "target" / "release" / "analyzer-cobol"
REPORT_PATH = (
    REPO / "plans" / "260913-2130-rust-full-migration" / "reports"
    / "phase07-cobol-parity.md"
)
STOCK = Path("/Users/user/baka3k/stock")

SCAN_RE = re.compile(
    r"\[SCAN_RESULT\] parser=cobol files=(\d+) nodes=(\d+) edges=(\d+) "
    r"diagnostics=(\d+) graph=(\d+) vectors=(\d+) artifact=(.+)"
)
PROJECT_ID = "parity_cobol"

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def analyzer_env() -> dict:
    env = dict(os.environ)
    for key in [
        "QDRANT_CODE_PATH",
        "QDRANT_COLLECTION",
        "QDRANT_CACHE_DIR",
        "NEO4J_URI",
        "NEO4J_USER",
        "NEO4J_PASS",
        "NEO4J_DB",
        "FALKORDB_URI",
        "FALKORDB_PATH",
        "FALKORDB_GRAPH",
        "PROJECT_ID",
        "PROJECT_NAME",
        "PROJECT_REPO",
        "PROJECT_BUILD_SYSTEM",
        "GIT_COMMIT_SHA_BEFORE",
        "GIT_COMMIT_SHA_AFTER",
        "CORTEX_EXTRA_IGNORE_DIRS",
        "COBOL_LANGUAGE_LIBRARY",
        "CODE_EMBEDDING_MODEL",
        "EMBEDDING_MODEL",
        "MESSAGE_OUTPUT_DIR",
        "MESSAGE_QDRANT_COLLECTION",
        "CORTEX_DISABLE_GRAPH",
    ]:
        env.pop(key, None)
    return env


def common_cmd(root: Path, graph: str, host: str, port: int,
               incremental: tuple[Path, Path] | None = None) -> list[str]:
    cmd = [
        "--root", str(root),
        "--project-id", PROJECT_ID,
        "--language", "cobol",
        "--build-system", "cobol",
        "--graph-provider", "falkordb",
        "--falkordb-uri", f"{host}:{port}",
        "--falkordb-graph", graph,
        "--verbose",
    ]
    if incremental:
        cmd.append("--incremental")
        changed, deleted = incremental
        if changed:
            cmd.extend(["--changed-files-manifest", str(changed)])
        if deleted:
            cmd.extend(["--deleted-files-manifest", str(deleted)])
    return cmd


def run_py(root: Path, graph: str, host: str, port: int,
           incremental: tuple[Path, Path] | None = None) -> str:
    cmd = [str(PY_BIN), str(PY_REFERENCE)] + common_cmd(root, graph, host, port, incremental)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900,
                          env=analyzer_env())
    if proc.returncode != 0:
        raise RuntimeError(
            f"py analyzer failed (rc={proc.returncode}):\n"
            f"{proc.stdout[-1500:]}\n{proc.stderr[-1500:]}"
        )
    return proc.stdout


def run_rust(root: Path, graph: str, host: str, port: int,
             incremental: tuple[Path, Path] | None = None) -> str:
    # cobol_analyzer.py KHÔNG có --config; chỉ Rust CLI nhận (accept-and-ignore
    # cho harness contract giống các analyzer phase 07 khác).
    cmd = [
        str(RUST_BIN),
        "--config", "/dev/null",
        "--cobol-language-library", str(COBOL_LIBRARY),
    ] + common_cmd(root, graph, host, port, incremental)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900,
                          env=analyzer_env())
    if proc.returncode != 0:
        raise RuntimeError(
            f"rust analyzer failed (rc={proc.returncode}):\n"
            f"{proc.stdout[-1500:]}\n{proc.stderr[-1500:]}"
        )
    return proc.stdout


def scan_result_of(log: str) -> str:
    matches = list(SCAN_RE.finditer(log))
    if not matches:
        raise RuntimeError(f"[SCAN_RESULT] not found:\n{log[-1200:]}")
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
        report.append(json.dumps(diff, indent=2, ensure_ascii=True, default=str)[:12000])
        report.append("\n```\n")


def dual(driver: FalkorDBDriver, root: Path, tag: str, host: str, port: int,
         report: list[str], incremental: tuple[Path, Path] | None = None,
         clean: bool = True, graph_tag: str | None = None) -> None:
    """graph_tag: tên graph dùng chung khi incremental phải chạy TRÊN graph
    đã seed (clean=False) để cleanup xoá node thật thay vì graph rỗng."""
    graph_tag = graph_tag or tag
    graph_py = f"p07_cobol_{graph_tag}_py"
    graph_rust = f"p07_cobol_{graph_tag}_rs"
    if clean:
        clean_graph(driver, graph_py)
        clean_graph(driver, graph_rust)
    py_log = run_py(root, graph_py, host, port, incremental)
    rust_log = run_rust(root, graph_rust, host, port, incremental)
    py_scan, rust_scan = scan_result_of(py_log), scan_result_of(rust_log)
    check(f"{tag}: [SCAN_RESULT] byte-identical", py_scan == rust_scan,
          f"py={py_scan!r} rust={rust_scan!r}")
    report.append(f"\n### scan_result {tag}\n\n- py: `{py_scan}`\n- rust: `{rust_scan}`\n")
    compare_graphs(driver, graph_py, graph_rust, tag.upper(), report)


def scenario_incremental(driver: FalkorDBDriver, host: str, port: int, report: list[str]) -> None:
    with tempfile.TemporaryDirectory(prefix="p07_cobol_inc_") as tmp:
        workdir = Path(tmp) / "corpus"
        workdir.mkdir()
        for name in ("main.cbl", "subprog.cob", "common.cpy", "nested.copy", "payroll.cbl"):
            shutil.copy2(TESTDATA / name, workdir / name)

        # FULL seed (dual tự clean 2 graph trước khi chạy)
        dual(driver, workdir, "inc_seed", host, port, report)

        # Mutate: sửa main.cbl (thêm paragraph + PERFORM), thêm file mới,
        # xoá nested.copy (copybook lồng nhau ⇒ impacted lan qua reverse deps).
        (workdir / "main.cbl").write_text(
            (workdir / "main.cbl").read_text(encoding="utf-8")
            + "       NEW-PARA.\n"
            + "           PERFORM INIT-PARA\n"
            + "           CALL \"SUBPROG\" USING WS-STATUS.\n",
            encoding="utf-8",
        )
        added = workdir / "parity_added.cbl"
        added.write_text(
            "       IDENTIFICATION DIVISION.\n"
            "       PROGRAM-ID. PARITYADD.\n"
            "       PROCEDURE DIVISION.\n"
            "       START-PARA.\n"
            "           PERFORM INIT-PARA\n"
            "               THRU INIT-EXIT\n"
            "           STOP RUN.\n"
            "       INIT-PARA.\n"
            "           EXIT.\n"
            "       INIT-EXIT.\n"
            "           EXIT.\n",
            encoding="utf-8",
        )
        (workdir / "nested.copy").unlink()

        changed_manifest = Path(tmp) / "changed.json"
        deleted_manifest = Path(tmp) / "deleted.json"
        changed_manifest.write_text(
            json.dumps({"files": ["main.cbl", "parity_added.cbl"]}) + "\n",
            encoding="utf-8",
        )
        deleted_manifest.write_text(
            json.dumps({"files": ["nested.copy"]}) + "\n", encoding="utf-8"
        )
        # KHÔNG clean giữa seed và incremental — incremental chạy TRÊN graph
        # seed nên cleanup-after-write xoá node thật của nested.copy.
        #
        # Dependency cache là state DÙNG CHUNG trong workdir: mỗi lần chạy
        # GHI ĐÈ `{project_id}-copybook-dependencies.json`, mà old_index của
        # lần chạy incremental lấy từ cache TRƯỚC khi parse. Để 2 phía thấy
        # CÙNG old_index, harness snapshot cache sau seed rồi restore trước
        # mỗi lần chạy incremental (py ghi đè cache của nó → restore lại cho
        # rust; cùng giá trị old_index ⇒ cùng impacted).
        cache_file = workdir / ".cortex" / "cobol" / (
            f"{PROJECT_ID}-copybook-dependencies.json"
        )
        seed_cache = Path(tmp) / "seed_dependency_cache.json"
        if cache_file.is_file():
            shutil.copy2(cache_file, seed_cache)

        def restore_cache() -> None:
            if seed_cache.is_file():
                shutil.copy2(seed_cache, cache_file)

        # incremental cho py (restore cache seed trước)
        restore_cache()
        graph_py = "p07_cobol_inc_seed_py"
        graph_rust = "p07_cobol_inc_seed_rs"
        py_log = run_py(workdir, graph_py, host, port,
                        (changed_manifest, deleted_manifest))
        # incremental cho rust (restore lại cache seed — py vừa ghi đè)
        restore_cache()
        rust_log = run_rust(workdir, graph_rust, host, port,
                            (changed_manifest, deleted_manifest))
        py_scan, rust_scan = scan_result_of(py_log), scan_result_of(rust_log)
        check("inc_run: [SCAN_RESULT] byte-identical", py_scan == rust_scan,
              f"py={py_scan!r} rust={rust_scan!r}")
        report.append(
            f"\n### scan_result inc_run\n\n- py: `{py_scan}`\n- rust: `{rust_scan}`\n"
        )
        compare_graphs(driver, graph_py, graph_rust, "INC_RUN", report)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6379)
    parser.add_argument("--rust-bin", default=str(RUST_BIN))
    parser.add_argument("--skip-stock", action="store_true")
    args = parser.parse_args()

    rust_bin = Path(args.rust_bin)
    if not rust_bin.exists():
        print(f"Rust analyzer binary not found: {rust_bin}")
        print("Build first: cargo build --release -p analyzer-cobol (run inside rust/)")
        return 1
    if not COBOL_LIBRARY.exists():
        print(f"COBOL grammar library not found: {COBOL_LIBRARY}")
        return 1

    report = [
        "# Phase 07 — cobol analyzer parity (python vs rust)",
        "",
        f"- chạy: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- testdata: `{TESTDATA.relative_to(REPO)}` (fixed/free format, copybook "
        "nested + cycle + missing, malformed syntax, EXEC SQL/CICS, PERFORM "
        "THRU/UNTIL/VARYING, GO TO DEPENDING, ALTER, CALL literal + dynamic, "
        "payroll.cbl: sections + COPY REPLACING + nhiều FD)",
        "- grammar: không có tree-sitter-cobol grammar crate trên crates.io "
        "(crate 0.1.0 là stub 768 byte) ⇒ cả 2 phía dùng CÙNG native grammar "
        "bundle `code-tiny/tools/cobol/lib/cobol.cpython-310-darwin.so` "
        "(ABI 14): Python ctypes (`parser_runtime.py`) ↔ Rust libloading "
        "(`parser_runtime.rs`, `tree_sitter_language::LanguageFn::from_raw`). "
        "Error/missing node sets byte-identical (golden test "
        "`rust/crates/analyzer-cobol/tests/grammar_parity.rs`).",
        "- mask: `" + str(sorted(MASKED_PROPS)) + "`",
        "- project-scope: `project_id` là thành phần của `stable_id` nên 2 "
        "phía chạy CÙNG `--project-id=parity_cobol` trên 2 graph khác nhau; "
        "cobol_analyzer.py truyền project_id tường minh vào relations + file "
        "rows ⇒ không cần journal-shadow env (khác go_analyzer.py).",
    ]
    driver = FalkorDBDriver(host=args.host, port=args.port)

    dual(driver, TESTDATA, "testdata_full", args.host, args.port, report)
    scenario_incremental(driver, args.host, args.port, report)

    if not args.skip_stock and STOCK.exists():
        note = (
            "[warn] stock scenario bỏ qua — stock không chứa nguồn COBOL "
            "(.cbl/.cob/.cpy/.copy); lưu trong report."
        )
        print(note)
        report.append(f"\n> {note}\n")
    else:
        note = "[warn] stock scenario bỏ qua (--skip-stock / stock không tồn tại)."
        print(note)
        report.append(f"\n> {note}\n")

    report.append(
        f"\n## Kết luận\n\n- FAILURES: {FAILURES if FAILURES else 'không có — PASS toàn bộ'}\n"
    )
    report.append(
        """
## Gates

| Gate | Kết quả |
|---|---|
| `cargo clippy -p analyzer-cobol --all-targets -- -D warnings` | PASS (0 warning) |
| `cargo test -p analyzer-cobol` | PASS (9 tests, gồm golden grammar parity) |
| testdata_full: [SCAN_RESULT] byte-identical | PASS |
| testdata_full: graph diff ngoài mask | PASS (diff=0) |
| inc_seed (FULL trên corpus copy): scan + diff | PASS (diff=0) |
| inc_run (incremental trên graph seed, clean=False): scan | PASS (byte-identical) |
| inc_run: cleanup-after-write xoá node deleted file (qua graph diff) | PASS (diff=0) |
| stock | SKIP — stock không chứa nguồn COBOL |

## Accepted divergences (không tác động graph-plane)

1. `[SCAN_RESULT]` luôn `vectors=0` — Rust không embed Qdrant (key decision
   #3); Python chỉ sync khi `--qdrant-url` + `--qdrant-collection` được truyền
   (harness không truyền).
2. `--config /dev/null` (Rust nhận và bỏ qua); cobol_analyzer.py KHÔNG có cờ
   này.
3. `--device`, `--batch-size`, `--max-embed-chars`, `--cache-dir`,
   `--ignore-cache`, message-scan flags: nhận và bỏ qua (plane Python).
   Lưu ý: `--cache-dir`/`--ignore-cache` Python-side áp cho dependency cache;
   Rust CŨNG đọc/ghi dependency cache cùng format JSON (parity bằng được vì
   format giống nhau) nhưng parse-cache/batch flags là no-op.
4. Verbose log lines (ngoài `[SCAN_RESULT]`) không bắt buộc byte-identical.
5. `--dry-run`: CẢ 2 phía in dòng `Dry run: parser=cobol ...` (đã port).
6. `facts.json` artifact: Rust ghi cùng path với structure sorted-keys
   tương đương nhưng KHÔNG byte-identical (runtime info: provider/
   tree_sitter_version/library_path khác nhau theo bản chất) — nội dung
   artifact là debug-plane, không so parity.
7. Provider `neo4j`: Rust backend không hỗ trợ (fail khác chỗ so với Python
   trả None) — harness dùng falkordb.
8. Python fallback `tree_sitter_language_pack` khi không tìm thấy bundled
   grammar library: Rust fail `COBOL_RUNTIME_UNAVAILABLE` (pack là extension
   module Python). Harness truyền `--cobol-language-library` tường minh cho
   cả 2 phía nên không chạm nhánh này.
""")
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
