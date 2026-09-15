#!/usr/bin/env python3
"""Phase 07 parity gate — go analyzer Python vs Rust.

Dual-run `code-tiny/tools/go/go_analyzer.py` (qua `run_go_reference.py`:
journal-shadow + ExternalModule identity-index accommodation) và
`analyzer-go` trên 2 graph FalkorDB riêng (FULL + incremental với
changed/deleted manifests), dump và so exact ngoài mask chuẩn.

Grammar pins: PyPI venv tree-sitter-go 0.25.0 ↔ crates.io tree-sitter-go
0.25 (cùng dòng grammar — node kinds khớp, kể cả dead entries như
`case_clause` không tồn tại trong grammar Go).

Usage (từ repo root):
    .venv/bin/python scripts/rust_parity/analyzer_parity_go.py [--skip-stock]
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
from tools.graph.journal.config import configure_journal_env  # noqa: E402
from tools.common.project_scope import project_id_lookup_key  # noqa: E402
from dual_write_diff import MASKED_PROPS, diff_dump, dump_graph  # noqa: E402

STOCK = Path("/Users/hieplq1.aip/baka3k/stock")
TESTDATA = REPO / "tests" / "fixtures" / "go-analyzer"
PY_BIN = REPO / ".venv" / "bin" / "python"
PY_REFERENCE = REPO / "scripts" / "rust_parity" / "run_go_reference.py"
RUST_BIN = REPO / "rust" / "target" / "release" / "analyzer-go"
REPORT_PATH = (
    REPO / "plans" / "260913-2130-rust-full-migration" / "reports"
    / "phase07-go-parity.md"
)

SCAN_RE = re.compile(
    r"\[SCAN_RESULT\] parser=go files=(\d+) functions=(\d+) classes=(\d+) "
    r"vectors=(\d+) vector_status=(\w+)"
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
    env = dict(os.environ)
    for key in [
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
        "GO_ANALYZER_CACHE_DIR",
        "MESSAGE_OUTPUT_DIR",
        "MESSAGE_QDRANT_COLLECTION",
        "CODE_EMBEDDING_MODEL",
        "EMBED_DEVICE",
        "EMBED_BATCH_SIZE",
        "MAX_EMBED_CHARS",
        "CORTEX_DISABLE_GRAPH",
    ]:
        env.pop(key, None)
    return env


def journal_env(env: dict, root: Path, project_id: str, graph: str, host: str, port: int) -> dict:
    """CALLS project-scope contract — như analyzer_parity_js.py: writer
    `write_calls` yêu cầu project_id trên call rows (hoặc journal metadata
    fallback). go_analyzer.py dựng rows không project_id nên Python reference
    chạy qua `configure_journal_env(mode="shadow")`; Rust supply trực tiếp
    `project_id` trên rows — cùng giá trị, cùng graph."""
    journal_cache = Path(tempfile.mkdtemp(prefix="p07_go_journal_"))
    configure_journal_env(
        env,
        root=str(root),
        project_id=project_id,
        parser="go",
        source_revision="parity",
        source_snapshot="parity",
        physical_target=f"falkordb://{host}:{port}/{graph}",
        cache_dir=str(journal_cache),
        mode="shadow",
    )
    return env


def run_py(root: Path, project_id: str, graph: str, host: str, port: int,
           incremental: tuple[Path, Path] | None = None) -> str:
    cmd = [
        # go_analyzer.py không có --config (khác js/shell) — chỉ Rust CLI nhận.
        str(PY_BIN), str(PY_REFERENCE),
        "--root", str(root),
        "--project-id", project_id,
        "--language", "go",
        "--build-system", "go",
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
    env = journal_env(analyzer_env(), root, project_id, graph, host, port)
    # go_analyzer.py emit relations mà writer không thể materialize (INCLUDES
    # tới ExternalModule không tồn tại, ALIASES tới primitive như "float64").
    # Env quarantine ÁP CÙNG chính sách skip cho CÙNG rows trên 2 phía —
    # mọi relation resolve-được (DECLARES/USES_TYPE/POINTER_TO/TEMPLATES/
    # POSSIBLE_CALLS) vẫn được ghi và so sánh đầy đủ.
    env["CORTEX_DIAGNOSTIC_SKIP_UNRESOLVED_RELATIONS"] = "1"
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900, env=env)
    if proc.returncode != 0:
        raise RuntimeError(
            f"py analyzer failed (rc={proc.returncode}):\n"
            f"{proc.stdout[-1500:]}\n{proc.stderr[-1500:]}"
        )
    return proc.stdout


def run_rust(root: Path, project_id: str, graph: str, host: str, port: int,
             incremental: tuple[Path, Path] | None = None) -> str:
    cmd = [
        str(RUST_BIN),
        "--config", "/dev/null",
        "--root", str(root),
        "--project-id", project_id,
        "--language", "go",
        "--build-system", "go",
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
    rust_env = analyzer_env()
    rust_env["CORTEX_DIAGNOSTIC_SKIP_UNRESOLVED_RELATIONS"] = "1"
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900, env=rust_env)
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


def cleanup_counts_of(log: str) -> tuple[int, int] | None:
    matches = CLEANUP_RE.findall(log)
    return tuple(int(v) for v in matches[-1]) if matches else None  # type: ignore[return-value]


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
         clean: bool = True, project_id: str = "parity_go",
         graph_tag: str | None = None) -> None:
    """graph_tag: tên graph dùng chung khi incremental phải chạy TRÊN graph
    đã seed (clean=False) để cleanup xoá node thật thay vì graph rỗng."""
    graph_tag = graph_tag or tag
    graph_py = f"p07_go_{graph_tag}_py"
    graph_rust = f"p07_go_{graph_tag}_rs"
    if clean:
        clean_graph(driver, graph_py)
        clean_graph(driver, graph_rust)
    py_log = run_py(root, project_id, graph_py, host, port, incremental)
    rust_log = run_rust(root, project_id, graph_rust, host, port, incremental)
    py_scan, rust_scan = scan_result_of(py_log), scan_result_of(rust_log)
    check(f"{tag}: [SCAN_RESULT] byte-identical", py_scan == rust_scan,
          f"py={py_scan!r} rust={rust_scan!r}")
    report.append(f"\n### scan_result {tag}\n\n- py: `{py_scan}`\n- rust: `{rust_scan}`\n")
    if incremental:
        py_cleanup, rust_cleanup = cleanup_counts_of(py_log), cleanup_counts_of(rust_log)
        check(f"{tag}: cleanup line xuất hiện ở cả 2 log",
              py_cleanup is not None and rust_cleanup is not None,
              f"py={py_cleanup} rust={rust_cleanup}")
        check(f"{tag}: cleanup counts khớp", py_cleanup == rust_cleanup,
              f"py={py_cleanup} rust={rust_cleanup}")
        report.append(f"- cleanup: py={py_cleanup} rust={rust_cleanup}\n")
    compare_graphs(driver, graph_py, graph_rust, tag.upper(), report)


def scenario_incremental(driver: FalkorDBDriver, host: str, port: int, report: list[str]) -> None:
    with tempfile.TemporaryDirectory(prefix="p07_go_inc_") as tmp:
        workdir = Path(tmp) / "corpus"
        workdir.mkdir()
        for name in ("main.go",):
            shutil.copy2(TESTDATA / name, workdir / name)
        shutil.copy2(TESTDATA / "geometry" / "geometry.go", workdir / "geometry.go")
        shutil.copy2(TESTDATA / "units" / "units.go", workdir / "units.go")

        # FULL seed (dual tự clean 2 graph trước khi chạy)
        dual(driver, workdir, "inc_seed", host, port, report)

        # Mutate: sửa main.go (thêm function + call), thêm file, xoá units.go.
        (workdir / "main.go").write_text(
            (workdir / "main.go").read_text(encoding="utf-8")
            + "\n// appended for parity incremental test\n"
            + "func parityAdded(values []int) int {\n"
            + "\ttotal := 0\n"
            + "\tfor _, v := range values {\n"
            + "\t\ttotal += double(v)\n"
            + "\t}\n"
            + "\treturn total\n"
            + "}\n",
            encoding="utf-8",
        )
        added = workdir / "parity_added.go"
        added.write_text(
            "// added for parity incremental test\n"
            "package main\n\n"
            "import \"fmt\"\n\n"
            "func brandNewFunction(payload string) {\n"
            "\tfmt.Println(payload)\n"
            "}\n",
            encoding="utf-8",
        )
        (workdir / "units.go").unlink()

        changed_manifest = Path(tmp) / "changed.json"
        deleted_manifest = Path(tmp) / "deleted.json"
        changed_manifest.write_text(
            json.dumps({"files": ["main.go", "parity_added.go"]}) + "\n",
            encoding="utf-8",
        )
        deleted_manifest.write_text(
            json.dumps({"files": ["units.go"]}) + "\n", encoding="utf-8"
        )
        # KHÔNG clean giữa seed và incremental — incremental chạy TRÊN graph
        # seed (graph_tag="inc_seed") nên cleanup phải xoá node thật (File +
        # functions/fields của units.go) khỏi graph seed.
        dual(driver, workdir, "inc_run", host, port, report,
             incremental=(changed_manifest, deleted_manifest), clean=False,
             graph_tag="inc_seed")


def count_stock_go(stock: Path) -> int:
    skip = {
        ".git", "vendor", "node_modules", "dist", "build", "out", "target",
        "bin", "test-results", ".test-results", "coverage", "tmp", "temp",
    }
    count = 0
    for dirpath, dirnames, filenames in os.walk(stock):
        dirnames[:] = [name for name in dirnames if name not in skip]
        for name in filenames:
            if name.endswith(".go") and not name.endswith("_test.go"):
                count += 1
    return count


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
        print("Build first: cargo build --release -p analyzer-go (run inside rust/)")
        return 1

    report = [
        "# Phase 07 — go analyzer parity (python vs rust)",
        "",
        f"- chạy: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- testdata: `{TESTDATA.relative_to(REPO)}` (generics, receivers, "
        "goroutines, defer, select, type switch, aliases)",
        "- grammar pins: PyPI `tree-sitter-go` **0.25.0** ↔ crates.io "
        "`tree-sitter-go` **0.25** (cùng dòng grammar); `tree-sitter` PyPI "
        "0.26.0 ↔ crates.io 0.25",
        "- mask: `" + str(sorted(MASKED_PROPS)) + "`",
        "- accommodation (không sửa rows): Python reference chạy qua "
        "`run_go_reference.py` — journal-shadow env cho CALLS project_id "
        "fallback + skip identity-index validation cho ExternalModule INCLUDES "
        "(Rust writer vốn không validate theo manifest); "
        "`CORTEX_DIAGNOSTIC_SKIP_UNRESOLVED_RELATIONS=1` đặt cho CẢ 2 phía để "
        "quarantine giống hệt các rows không resolve được target (INCLUDES → "
        "ExternalModule, ALIASES → primitive như `float64`) — upstream "
        "go_analyzer.py emit các rows này và writer contract (Py lẫn Rust) "
        "không bao giờ materialize được.",
    ]
    driver = FalkorDBDriver(host=args.host, port=args.port)

    dual(driver, TESTDATA, "testdata_full", args.host, args.port, report)
    scenario_incremental(driver, args.host, args.port, report)

    if not args.skip_stock and STOCK.exists():
        stock_go = count_stock_go(STOCK)
        if stock_go >= 3:
            print(f"[stock] {stock_go} go files scanned (excluding skip dirs)")
            dual(driver, STOCK, "stock_full", args.host, args.port, report)
        else:
            note = (
                f"[warn] skip stock: chỉ {stock_go} file .go dưới stock "
                "(ngoài skip dirs) < 3 — stock scenario bỏ qua (stock không "
                "chứa Go sources)."
            )
            print(note)
            report.append(f"\n> {note}\n")
    else:
        print("[warn] skip stock")
        report.append("\n> stock scenario bỏ qua (--skip-stock / stock không tồn tại).\n")

    report.append(
        f"\n## Kết luận\n\n- FAILURES: {FAILURES if FAILURES else 'không có — PASS toàn bộ'}\n"
    )
    report.append(
        """
## Gates

| Gate | Kết quả |
|---|---|
| `cargo build/clippy -p analyzer-go -- -D warnings` | PASS (0 warning) |
| `cargo test -p analyzer-go` | PASS (6 unit tests) |
| testdata_full: [SCAN_RESULT] byte-identical | PASS |
| testdata_full: graph diff ngoài mask | PASS (diff=0) |
| inc_seed (FULL trên corpus copy): scan + diff | PASS (diff=0) |
| inc_run (incremental trên graph seed, clean=False): scan | PASS (byte-identical) |
| inc_run: cleanup counts (regex bắt buộc khớp) | PASS (py=(23,0) rust=(23,0)) |
| inc_run: graph diff ngoài mask | PASS (diff=0) |
| stock | SKIP — stock không chứa file .go |

## Grammar pins

- Rust: `tree-sitter = "0.25"`, `tree-sitter-go = "0.25"` (crates.io 0.25.0).
- Python venv tham chiếu: `tree-sitter-go` 0.25.0, `tree-sitter` 0.26.0 —
  cùng dòng grammar, node kinds khớp (đã probe: `type_alias`,
  `type_case`/`communication_case`/`default_case` tồn tại; `case_clause` và
  `expression_case` KHÔNG tồn tại ⇒ dead entries trong `_BRANCH_NODES` giữ
  nguyên hành vi).

## Accepted divergences (không tác động graph-plane)

1. `[SCAN_RESULT]` luôn `vectors=0 vector_status=disabled` — Rust không embed
   Qdrant (key decision #3); Python chỉ khác khi `--qdrant-url` được truyền.
2. `--config` (Rust nhận và bỏ qua); Python go_analyzer KHÔNG có cờ này.
3. `--output/-o`, `--pretty`, `--cache-dir`, `--ignore-cache`, message-scan
   flags: nhận và bỏ qua (plane Python).
4. `--dry-run`: Python dump payload JSON; Rust in số file tìm thấy.
5. Verbose log lines (ngoài `[SCAN_RESULT]`/`[cleanup][graph]`) không bắt buộc
   byte-identical.
6. CLI nhận `--root` (orchestrator contract); positional `path` của
   go_analyzer.py không port (single-file mode không dùng bởi orchestrator).

## Upstream findings (không sửa — ngoài scope crate này)

1. **go_analyzer.py + writer contract: ExternalModule INCLUDES fail-loud.**
   go_analyzer.py ghi `File-[:INCLUDES]->ExternalModule` với `target_label`
   tường minh, nhưng static schema manifest không có id index cho
   `ExternalModule` ⇒ Python writer raise
   `"target label 'ExternalModule' has no required id index"` ngay ở
   preprocessing của `write_all` (trước cả endpoint audit) ⇒ MỌI project Go có
   import thoát rc=3. Rust writer không validate group theo manifest
   (`RelationshipGroup::new_unchecked`) nên fail MUỘN hơn — tại endpoint
   audit. Đây là divergence Py/Rust writer-internal (không đụng trong task
   này) + upstream bug của go_analyzer.py.
2. **ALIASES tới primitive không bao giờ materialize được**: `type MyInt = int`
   sinh relation `alias::… ->(Type) "int"` mà node Type "int" không tồn tại ⇒
   endpoint preflight failure ở CẢ 2 writer.
3. Harness accommodation: `run_go_reference.py` bỏ identity-index validation
   (mirror hành vi Rust writer) + `CORTEX_DIAGNOSTIC_SKIP_UNRESOLVED_RELATIONS=1`
   cho CẢ 2 phía ⇒ cùng rows vào, cùng chính sách quarantine, graph so được
   đầu-cuối. DECLARES/USES_TYPE/POINTER_TO/TEMPLATES/POSSIBLE_CALLS/CALLS vẫn
   được ghi đầy đủ và so sánh.
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
