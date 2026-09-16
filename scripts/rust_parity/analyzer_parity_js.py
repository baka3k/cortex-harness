#!/usr/bin/env python3
"""Phase 05 parity gate — js analyzer Python vs Rust.

Dual-run `tools/js/js_analyzer.py` và `analyzer-js` trên 2 graph FalkorDB
riêng (FULL + incremental với changed/deleted manifests + import-graph
impacted expansion), dump và so exact ngoài mask chuẩn.

Usage (từ repo root):
    .venv/bin/python scripts/rust_parity/analyzer_parity_js.py [--skip-stock]
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
from dual_write_diff import MASKED_PROPS, diff_dump, dump_graph  # noqa: E402

STOCK = Path("/Users/user/baka3k/stock")
TESTDATA = REPO / "tests" / "fixtures" / "js-analyzer"
PY_BIN = REPO / ".venv" / "bin" / "python"
PY_ANALYZER = REPO / "code-tiny" / "tools" / "js" / "js_analyzer.py"
RUST_BIN = REPO / "rust" / "target" / "release" / "analyzer-js"
REPORT_PATH = (
    REPO / "plans" / "260913-2130-rust-full-migration" / "reports"
    / "phase05-js-parity.md"
)

SCAN_RE = re.compile(
    r"\[SCAN_RESULT\] parser=(\S+) files=(\d+) functions=(\d+) classes=(\d+)"
)
CLEANUP_RE = re.compile(
    r"\[cleanup\]\[graph\] deleted_nodes=(\d+) deleted_unknown_functions=(\d+)"
)

# `_SCAN_SKIP_DIRS` của js_analyzer — dùng để đếm corpus js của stock.
JS_SKIP_DIRS = {
    ".git", ".hg", ".svn",
    "node_modules",
    "dist", "build", "out", ".next", ".nuxt", ".output",
    ".cache", ".parcel-cache", ".eslintcache", ".stylelintcache", "__pycache__",
    "coverage", ".nyc_output", "test-results", ".test-results",
    ".idea", ".vscode",
    "tmp", "temp", ".tmp", "tmpdir",
    ".DS_Store", "Thumbs.db",
    "target", ".serverless",
    ".env", ".env.local",
    "vendor", "bower_components", ".venv", "venv", "env", "bin", "obj",
    ".cortext-harness",
}
JS_EXTS = (".js", ".jsx", ".mjs", ".cjs")

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
    ]:
        env.pop(key, None)
    return env


def journal_env(env: dict, root: Path, project_id: str, graph: str, host: str, port: int) -> dict:
    """CALLS project-scope contract: writer `write_calls` yêu cầu project_id
    trên call rows (hoặc journal metadata fallback). js_analyzer.py dựng rows
    không project_id nên CHỈ chạy được qua đúng đường invocation production:
    orchestrator cấp journal env cho child process (`configure_journal_env`).
    Mode `shadow` ⇒ `required=False`: KHÔNG có journal runtime/write/guard —
    writer chỉ đọc `metadata.project_id` làm fallback cho call rows, cho ra
    đúng graph như khi rows mang project_id tường minh (Rust supply trực tiếp
    giá trị này).
    """
    journal_cache = Path(tempfile.mkdtemp(prefix="p05_js_journal_"))
    configure_journal_env(
        env,
        root=str(root),
        project_id=project_id,
        parser="js",
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
    env = journal_env(analyzer_env(), root, project_id, graph, host, port)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900, env=env)
    if proc.returncode != 0:
        raise RuntimeError(f"py analyzer failed:\n{proc.stdout[-1500:]}\n{proc.stderr[-1500:]}")
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
         report: list[str], incremental: tuple[Path, Path] | None = None,
         clean: bool = True) -> None:
    graph_py = f"p05_js_{tag}_py"
    graph_rust = f"p05_js_{tag}_rs"
    if clean:
        clean_graph(driver, graph_py)
        clean_graph(driver, graph_rust)
    py_log = run_py(root, "parity_js", graph_py, host, port, incremental)
    rust_log = run_rust(root, "parity_js", graph_rust, host, port, incremental)
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
    with tempfile.TemporaryDirectory(prefix="p05_js_inc_") as tmp:
        workdir = Path(tmp) / "corpus"
        workdir.mkdir()
        for name in ("store.js", "money.js", "cart.js", "legacy-helpers.js"):
            shutil.copy2(TESTDATA / name, workdir / name)

        # FULL seed (dual tự clean 2 graph trước khi chạy)
        dual(driver, workdir, "inc_seed", host, port, report)

        # Mutate: sửa money.js (thêm function), thêm file, xoá legacy-helpers.js.
        # store.js import './money.js' ⇒ impacted expansion phải kéo store.js;
        # cart.js gọi deepClone từ legacy-helpers ⇒ cleanup phải sever CALLS.
        (workdir / "money.js").write_text(
            (workdir / "money.js").read_text(encoding="utf-8")
            + "\nexport function appendedParityHelper(values) {\n"
            + "  return values.reduce((a, b) => a + b, 0);\n"
            + "}\n",
            encoding="utf-8",
        )
        added = workdir / "parity_added.js"
        added.write_text(
            "// added for parity incremental test\n"
            "import { formatPrice } from './money.js';\n"
            "export function brandNewFunction(payload) {\n"
            "  return formatPrice(payload);\n"
            "}\n",
            encoding="utf-8",
        )
        (workdir / "legacy-helpers.js").unlink()

        changed_manifest = Path(tmp) / "changed.json"
        deleted_manifest = Path(tmp) / "deleted.json"
        changed_manifest.write_text(
            json.dumps({"files": ["money.js", "parity_added.js"]}) + "\n",
            encoding="utf-8",
        )
        deleted_manifest.write_text(
            json.dumps({"files": ["legacy-helpers.js"]}) + "\n", encoding="utf-8"
        )
        # KHÔNG clean giữa seed và incremental — cleanup phải xoá node thật
        # (File + functions của legacy-helpers.js) khỏi graph seed.
        dual(driver, workdir, "inc_run", host, port, report,
             incremental=(changed_manifest, deleted_manifest), clean=False)


def count_stock_js(stock: Path) -> int:
    count = 0
    for dirpath, dirnames, filenames in os.walk(stock):
        dirnames[:] = [name for name in dirnames if name not in JS_SKIP_DIRS]
        for name in filenames:
            if name.endswith(JS_EXTS):
                count += 1
    return count


def main() -> int:
    global RUST_BIN

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6379)
    parser.add_argument("--rust-bin", default=str(RUST_BIN))
    parser.add_argument("--skip-stock", action="store_true")
    args = parser.parse_args()

    RUST_BIN = Path(args.rust_bin)
    if not RUST_BIN.exists():
        print(f"Rust analyzer binary not found: {RUST_BIN}")
        print("Build first: cargo build --release -p analyzer-js (run inside rust/)")
        return 1

    report = [
        "# Phase 05 — js analyzer parity (python vs rust)",
        "",
        f"- chạy: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- testdata: `{TESTDATA.relative_to(REPO)}` (js parity corpus, jsx-free)",
        f"- mask: `{sorted(MASKED_PROPS)}`",
    ]
    driver = FalkorDBDriver(host=args.host, port=args.port)

    dual(driver, TESTDATA, "testdata_full", args.host, args.port, report)
    scenario_incremental(driver, args.host, args.port, report)

    if not args.skip_stock and STOCK.exists():
        stock_js = count_stock_js(STOCK)
        if stock_js >= 3:
            print(f"[stock] {stock_js} js files scanned (excluding skip dirs)")
            dual(driver, STOCK, "stock_full", args.host, args.port, report)
        else:
            note = (
                f"[warn] skip stock: chỉ {stock_js} file js dưới stock "
                "(ngoài node_modules/skip dirs) < 3"
            )
            print(note)
            report.append(f"\n> {note} — stock scenario bỏ qua.\n")
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
