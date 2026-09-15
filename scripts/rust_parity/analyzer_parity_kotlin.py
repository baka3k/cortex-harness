#!/usr/bin/env python3
"""Phase 06 parity gate — Kotlin analyzer Python vs Rust.

Dual-run `tools/kotlin/kotlin_analyzer.py` và `analyzer-kotlin` trên 2 graph
FalkorDB riêng (FULL + incremental với manifests + cleanup counts + import
impact), dump và so exact ngoài mask chuẩn từ dual_write_diff.

Usage (từ repo root):
    .venv/bin/python scripts/rust_parity/analyzer_parity_kotlin.py

Ghi chú: stock corpus (/Users/hieplq1.aip/baka3k/stock) KHÔNG có file .kt
nên gate stock được skip (recorded trong report).

Grammar pin:
    - Python (PyPI venv): tree-sitter-kotlin 1.1.0 (fallback của
      `_get_kotlin_parser` vì tree_sitter_languages.get_parser TypeError với
      tree_sitter 0.26).
    - Rust: tree-sitter-kotlin-ng 1.1.0 + tree-sitter 0.25 — parse trees
      byte-identical với PyPI 1.1.0 (verify bằng diff sexp).
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
import dual_write_diff  # noqa: E402
from dual_write_diff import MASKED_PROPS, diff_dump, dump_graph  # noqa: E402

# `_start_id`/`_end_id` là row-id nội bộ FalkorDB (engine cấp theo lịch sử ghi
# của TỪNG graph — cùng bản chất với `_graph_id`/`_edge_id` đã có trong mask).
# Analyzer không sinh chúng; mask ở harness thay vì sửa dual_write_diff chung.
ENGINE_INTERNAL_PROPS = MASKED_PROPS | {"_start_id", "_end_id"}

STOCK = Path("/Users/hieplq1.aip/baka3k/stock")
TESTDATA = REPO / "tests" / "fixtures" / "kotlin-analyzer"
PY_BIN = REPO / ".venv" / "bin" / "python"
PY_ANALYZER = REPO / "code-tiny" / "tools" / "kotlin" / "kotlin_analyzer.py"
RUST_BIN = REPO / "rust" / "target" / "release" / "analyzer-kotlin"
REPORT_PATH = (
    REPO / "plans" / "260913-2130-rust-full-migration" / "reports"
    / "phase06-kotlin-parity.md"
)

SCAN_RE = re.compile(
    r"\[SCAN_RESULT\] parser=\S+ files=(\d+) functions=(\d+) classes=(\d+)"
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
        "FALKORDB_URI",
        "FALKORDB_GRAPH",
        "PROJECT_ID",
        "PROJECT_NAME",
        "PROJECT_LANGUAGE",
        "PROJECT_REPO",
        "PROJECT_BUILD_SYSTEM",
        "CODE_EMBEDDING_MODEL",
        "CODE_EMBEDDING_MODEL_PATH",
        "JINA_MODEL_PATH",
        "EMBED_DEVICE",
        "EMBED_BATCH_SIZE",
        "MAX_EMBED_CHARS",
        "NEO4J_URI",
        "NEO4J_USER",
        "NEO4J_PASS",
        "NEO4J_DB",
        "NEO4J_STATE_PATH",
        "LADYBUG_PATH",
        "LADYBUG_GRAPH",
        "CORTEX_DISABLE_GRAPH",
        "CORTEX_EXTRA_IGNORE_DIRS",
        "QDRANT_CACHE_DIR",
    ]:
        env.pop(key, None)
    return env


def run_py(root: Path, project_id: str, graph: str, host: str, port: int,
           incremental: tuple[Path, Path] | None = None) -> str:
    cmd = [
        str(PY_BIN), str(PY_ANALYZER),
        "--root", str(root),
        "--config", "/dev/null",
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
    env = analyzer_env()
    # kotlin_analyzer.py để call rows KHÔNG có project_id — scoping chạy qua
    # journal metadata như orchestrator (`incremental_sync.py` gọi
    # `configure_journal_env` trước khi spawn analyzer). Mode `shared-shadow`
    # là default lane non-cplus. Journal sqlite để dưới runtime cache của repo
    # (không đụng vào corpus).
    scratch = REPO / ".cache" / "p06_kotlin_parity_journal"
    scratch.mkdir(parents=True, exist_ok=True)
    configure_journal_env(
        env,
        root=str(root),
        project_id=project_id,
        parser="kotlin",
        source_revision=f"parity-{graph}",
        source_snapshot=f"parity-{graph}",
        physical_target=f"falkordb:{host}:{port}/{graph}",
        cache_dir=str(scratch),
        mode=env.get("CORTEX_GRAPH_JOURNAL_MODE", "shared-shadow"),
        generation=f"parity-{graph}",
    )
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900,
                          env=env)
    if proc.returncode != 0:
        raise RuntimeError(f"py analyzer failed:\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")
    return proc.stdout


def run_rust(root: Path, project_id: str, graph: str, host: str, port: int,
             incremental: tuple[Path, Path] | None = None) -> str:
    cmd = [
        str(RUST_BIN),
        "--root", str(root),
        "--config", "/dev/null",
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
        raise RuntimeError(f"rust analyzer failed:\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")
    return proc.stdout


def scan_result_of(log: str) -> str:
    matches = list(SCAN_RE.finditer(log))
    if not matches:
        raise RuntimeError(f"[SCAN_RESULT] not found:\n{log[-1500:]}")
    return matches[-1].group(0)


def cleanup_counts_of(log: str) -> tuple[int, int]:
    matches = CLEANUP_RE.findall(log)
    return tuple(int(v) for v in matches[-1]) if matches else (0, 0)  # type: ignore[return-value]


def clean_graph(driver: FalkorDBDriver, graph: str) -> None:
    import asyncio

    asyncio.run(driver.execute_query("MATCH (n) DETACH DELETE n", {}, graph))


def dump_graph_masked(driver: FalkorDBDriver, graph: str) -> dict:
    dump = dump_graph(driver, graph)

    def strip(props: dict) -> dict:
        return {k: v for k, v in props.items() if k not in ENGINE_INTERNAL_PROPS}

    return {
        "nodes": {k: strip(v) for k, v in dump["nodes"].items()},
        "edges": {k: strip(v) for k, v in dump["edges"].items()},
    }


def compare_graphs(driver: FalkorDBDriver, graph_py: str, graph_rust: str,
                   label: str, report: list[str]) -> None:
    py_dump = dump_graph_masked(driver, graph_py)
    rust_dump = dump_graph_masked(driver, graph_rust)
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
    return diff_total


def dual(driver: FalkorDBDriver, root: Path, tag: str, host: str, port: int,
         report: list[str],
         incremental: tuple[Path, Path] | None = None,
         clean: bool = True) -> int:
    graph_py = f"p06_kotlin_{tag}_py"
    graph_rust = f"p06_kotlin_{tag}_rs"
    if clean:
        clean_graph(driver, graph_py)
        clean_graph(driver, graph_rust)
    py_log = run_py(root, "parity_kotlin", graph_py, host, port, incremental)
    rust_log = run_rust(root, "parity_kotlin", graph_rust, host, port, incremental)
    py_scan, rust_scan = scan_result_of(py_log), scan_result_of(rust_log)
    check(f"{tag}: [SCAN_RESULT] byte-identical", py_scan == rust_scan,
          f"py={py_scan!r} rust={rust_scan!r}")
    report.append(f"\n### scan_result {tag}\n\n- py: `{py_scan}`\n- rust: `{rust_scan}`\n")
    if incremental:
        py_cleanup, rust_cleanup = cleanup_counts_of(py_log), cleanup_counts_of(rust_log)
        check(f"{tag}: cleanup counts khớp", py_cleanup == rust_cleanup,
              f"py={py_cleanup} rust={rust_cleanup}")
        report.append(f"- cleanup: py={py_cleanup} rust={rust_cleanup}\n")
    return compare_graphs(driver, graph_py, graph_rust, tag.upper(), report)


def scenario_incremental(driver: FalkorDBDriver, host: str, port: int, report: list[str]) -> None:
    with tempfile.TemporaryDirectory(prefix="p06_kotlin_inc_") as tmp:
        workdir = Path(tmp) / "corpus"
        shutil.copytree(TESTDATA, workdir)

        # FULL seed trên corpus copy.
        dual(driver, workdir, "inc_seed", host, port, report)

        # Mutate: sửa Shape.kt (thêm method), thêm Runner.kt (import
        # com.example.geom → import-impact BFS phải kéo Registry.kt vào
        # selection), xoá Util.kt.
        shape = workdir / "src" / "com" / "example" / "geom" / "Shape.kt"
        shape.write_text(
            shape.read_text(encoding="utf-8").replace(
                "    fun describe(): String {",
                "    fun perimeter(): Double {\n"
                "        return area() * 4\n"
                "    }\n\n"
                "    fun describe(): String {",
            ),
            encoding="utf-8",
        )
        runner = workdir / "src" / "com" / "example" / "Runner.kt"
        runner.write_text(
            "package com.example\n\n"
            "import com.example.geom.Circle\n\n"
            "/** Added by the parity scenario. */\n"
            "class Runner {\n"
            "    fun go(): Double {\n"
            "        val c = Circle(3.0)\n"
            "        return c.area()\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        (workdir / "src" / "com" / "example" / "Util.kt").unlink()

        changed_manifest = Path(tmp) / "changed.json"
        deleted_manifest = Path(tmp) / "deleted.json"
        changed_manifest.write_text(
            json.dumps({"files": [
                "src/com/example/geom/Shape.kt",
                "src/com/example/Runner.kt",
            ]}) + "\n",
            encoding="utf-8",
        )
        deleted_manifest.write_text(
            json.dumps({"files": ["src/com/example/Util.kt"]}) + "\n",
            encoding="utf-8",
        )
        # inc_run KHÔNG clean graph — chạy trên graph seed để cleanup đếm xoá
        # thật (nếu clean trước, deleted_nodes luôn 0). Diff vẫn phải rỗng.
        dual(driver, workdir, "inc_run", host, port, report,
             incremental=(changed_manifest, deleted_manifest), clean=False)


def stock_has_kotlin(stock: Path) -> bool:
    if not stock.exists():
        return False
    for _dirpath, dirnames, filenames in os.walk(stock):
        dirnames[:] = [d for d in dirnames if d not in {
            ".git", "node_modules", "build", "target", ".gradle", ".idea",
        }]
        for name in filenames:
            if name.endswith((".kt", ".kts")):
                return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6379)
    parser.add_argument("--rust-bin", default=str(RUST_BIN))
    args = parser.parse_args()

    rust_bin = Path(args.rust_bin)

    report = [
        "# Phase 06 — Kotlin analyzer parity (python vs rust)",
        "",
        f"- chạy: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- testdata: `{TESTDATA.relative_to(REPO)}` (src/com/example/...)",
        f"- rust bin: `{rust_bin}`",
        "- grammar pin: **tree-sitter-kotlin-ng 1.1.0** (Rust, crates.io) == "
        "**tree-sitter-kotlin 1.1.0** (PyPI venv; `_get_kotlin_parser` fallback "
        "vì tree_sitter_languages.get_parser TypeError với tree_sitter 0.26) — "
        "parse trees byte-identical (diff sexp trên corpus enum/data-class/"
        "companion/delegation/lambda).",
        "- tree-sitter pin: 0.25 (Rust) / 0.26.0 (PyPI venv) — FFI khác grammar "
        "không ảnh hưởng node kinds.",
    ]
    if stock_has_kotlin(STOCK):
        report.append("- stock corpus: CÓ file .kt")
        include_stock = True
    else:
        report.append("- stock corpus: **skip** — không có file .kt")
        include_stock = False

    report.extend([
        "",
        "## Ghi chú port",
        "",
        "- Qdrant/embedding, message scan, parse cache / neo4j resume state:"
        " backend Rust nhận cờ và bỏ qua (khớp contract phase 04+; message"
        " scan là plane Python).",
        "- Call rows: writer (`write_calls` → `_require_call_project_scope`,"
        " language_writer.py:137-147) bắt buộc project_id. Python analyzer"
        " gốc để rows KHÔNG project_id → harness set journal env như"
        " orchestrator (`configure_journal_env(..., mode=\"shared-shadow\")`)"
        " cho phía py; phía rust rows mang project_id tường minh (khớp lane"
        " java).",
        "- FunctionType/TAKES_FUNCTION: tree-sitter-kotlin v1.1.0 KHÔNG định"
        " nghĩa field `type` trên node `parameter` → nhánh này inert trên cả"
        " 2 phía (python `_extract_parameter_info` trả None). Port giữ nguyên"
        " logic cho fidelity.",
        "- `enum class` parse thành `class_declaration` + modifier `enum`"
        " (kind=\"class\") trên grammar 1.1.0 — `_class_kind` vẫn map"
        " `enum_class`/`enum_declaration` cho grammar cũ, khớp python.",
        "- inc_run KHÔNG clean graph seed → cleanup đếm xoá thật (nếu clean"
        " trước, deleted_nodes luôn 0).",
    ])

    driver = FalkorDBDriver(host=args.host, port=args.port)

    dual(driver, TESTDATA, "testdata_full", args.host, args.port, report)
    scenario_incremental(driver, args.host, args.port, report)
    if include_stock:
        dual(driver, STOCK, "stock_full", args.host, args.port, report)

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
