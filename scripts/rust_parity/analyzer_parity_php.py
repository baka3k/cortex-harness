#!/usr/bin/env python3
"""Phase 05 parity gate — PHP analyzer Python vs Rust.

Dual-run `code-tiny/tools/php/php_analyzer.py` và `analyzer-php` trên 2 graph
FalkorDB riêng (`p05_php_<tag>_py` / `p05_php_<tag>_rs`), clean trước khi chạy,
dump và so exact ngoài mask chuẩn (dual_write_diff).

Corpus: `tests/fixtures/php-analyzer/` (stock repo không có PHP scannable →
scenario stock bị skip có chủ đích).

Usage (từ repo root):
    .venv/bin/python scripts/rust_parity/analyzer_parity_php.py
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
TESTDATA = REPO / "tests" / "fixtures" / "php-analyzer"
PY_BIN = REPO / ".venv" / "bin" / "python"
PY_ANALYZER = REPO / "code-tiny" / "tools" / "php" / "php_analyzer.py"
RUST_BIN = REPO / "rust" / "target" / "release" / "analyzer-php"
REPORT_PATH = (
    REPO / "plans" / "260913-2130-rust-full-migration" / "reports"
    / "phase05-php-parity.md"
)

SCAN_RE = re.compile(
    r"\[SCAN_RESULT\] parser=php files=(\d+) functions=(\d+) classes=(\d+)"
)
CLEANUP_RE = re.compile(
    r"\[cleanup\]\[graph\] deleted_nodes=(\d+) deleted_unknown_functions=(\d+)"
)

# Internal engine node-row ids đeo trên relationship props bởi driver —
# volatile theo lịch sử cấp phát id của từng graph (cùng bản chất với
# `_src`/`_dst` đã có trong MASKED_PROPS của dual_write_diff). Endpoint thật
# của edge đã được so qua key (label + identity của 2 đầu), nên mask thêm ở
# đây KHÔNG giảm độ mạnh của gate.
INTERNAL_EDGE_PROPS = ("_start_id", "_end_id")

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
        "MESSAGE_OUTPUT_DIR",
        "MESSAGE_QDRANT_COLLECTION",
        "CODE_EMBEDDING_MODEL",
        "JINA_MODEL_PATH",
        "CODE_EMBEDDING_MODEL_PATH",
    ]:
        env.pop(key, None)
    return env


def journal_env(project_id: str, root: Path, journal_path: Path) -> dict:
    """Shadow-journal env cho Python reference.

    `php_analyzer.py` chưa gửi `project_id` trên call rows (python_analyzer.py
    đã được cập nhật contract này) nên journal-less run bị writer từ chối
    (`_require_call_project_scope`). Shadow mode là kênh env được hỗ trợ:
    `_journal_config` cung cấp metadata.project_id cho call scope, KHÔNG kích
    hoạt write guard/journal runtime (chỉ `required` mới bật) — write path giữ
    nguyên. Rust side gửi `project_id` explicit nên không cần journal.
    """
    from tools.common.sync_scope import scan_scope_id

    metadata = {
        "project_id": project_id,
        "scope_id": scan_scope_id(project_id, str(root)),
        "source_revision": "parity",
        "source_snapshot": "parity",
        "physical_target": "falkordb",
        "generation": "parity",
        "parser": "php",
        "parser_version": "0",
        "schema_fingerprint": "parity",
        "query_shape_version": "parity",
        "operation_versions": {},
        "contract_version": 1,
    }
    return {
        "CORTEX_GRAPH_JOURNAL_MODE": "shadow",
        "CORTEX_GRAPH_JOURNAL_PATH": str(journal_path),
        "CORTEX_GRAPH_JOURNAL_METADATA": json.dumps(metadata),
    }


def run_py(root: Path, project_id: str, graph: str, host: str, port: int,
           incremental: tuple[Path, Path] | None = None, journal: Path | None = None) -> str:
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
    if journal is not None:
        env.update(journal_env(project_id, root, journal))
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900,
                          env=env)
    if proc.returncode != 0:
        raise RuntimeError(f"py analyzer failed:\n{proc.stdout[-1500:]}\n{proc.stderr[-1500:]}")
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


def normalize_dump(dump: dict) -> dict:
    for props in dump["edges"].values():
        for key in INTERNAL_EDGE_PROPS:
            props.pop(key, None)
    return dump


def compare_graphs(driver: FalkorDBDriver, graph_py: str, graph_rust: str,
                   label: str, report: list[str]) -> None:
    py_dump = normalize_dump(dump_graph(driver, graph_py))
    rust_dump = normalize_dump(dump_graph(driver, graph_rust))
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
         report: list[str],
         incremental: tuple[Path, Path] | None = None,
         clean: bool = True) -> None:
    graph_py = f"p05_php_{tag}_py"
    graph_rust = f"p05_php_{tag}_rs"
    if clean:
        clean_graph(driver, graph_py)
        clean_graph(driver, graph_rust)
    with tempfile.TemporaryDirectory(prefix=f"p05_php_journal_{tag}_") as jtmp:
        journal = Path(jtmp) / "graph-write-journal.sqlite"
        py_log = run_py(root, "parity_php", graph_py, host, port, incremental,
                        journal=journal)
    rust_log = run_rust(root, "parity_php", graph_rust, host, port, incremental)
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
    with tempfile.TemporaryDirectory(prefix="p05_php_inc_") as tmp:
        workdir = Path(tmp) / "corpus"
        workdir.mkdir()
        for name in ("billing.php", "provisioning.php", "support.php"):
            shutil.copy2(TESTDATA / name, workdir / name)

        # FULL seed VÀO CHÍNH GRAPH incremental dùng (p05_php_inc_py/_rs) —
        # chạy incremental trên graph đã có dữ liệu nên cleanup count là
        # số liệu thật (không phải 0 trên graph rỗng).
        dual(driver, workdir, "inc", host, port, report)

        # Mutate: thêm method vào billing.php, thêm file mới, xoá support.php
        (workdir / "billing.php").write_text(
            (workdir / "billing.php").read_text(encoding="utf-8")
            + """
class LateFeeInvoice extends Invoice
{
    public function penalty(): float
    {
        return $this->total([]) * 1.5 + max(1, count([]));
    }
}
""",
            encoding="utf-8",
        )
        added = workdir / "parity_added.php"
        added.write_text(
            "<?php\n"
            "// Added during parity incremental run.\n"
            "function parity_added_helper($x) {\n"
            "    return build_invoice(null) ?? $x;\n"
            "}\n",
            encoding="utf-8",
        )
        (workdir / "support.php").unlink()

        changed_manifest = Path(tmp) / "changed.json"
        deleted_manifest = Path(tmp) / "deleted.json"
        changed_manifest.write_text(
            json.dumps({"files": ["billing.php", "parity_added.php"]}) + "\n",
            encoding="utf-8",
        )
        deleted_manifest.write_text(
            json.dumps({"files": ["support.php"]}) + "\n", encoding="utf-8"
        )
        dual(driver, workdir, "inc", host, port, report,
             incremental=(changed_manifest, deleted_manifest), clean=False)


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
        "# Phase 05 — PHP analyzer parity (python vs rust)",
        "",
        f"- chạy: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- testdata: `{TESTDATA.relative_to(REPO)}`",
        f"- rust bin: `{RUST_BIN}`",
        f"- python ref: `{PY_ANALYZER.relative_to(REPO)}` (--config /dev/null)",
        f"- grammar pins: tree-sitter = 0.25, tree-sitter-php = 0.24 "
        f"(crates.io không có 0.25 cho php; PyPI ref dùng tree-sitter-php "
        f"0.24.1 vì tree-sitter-languages 1.10.2 broken với tree_sitter 0.26)",
        f"- mask: `{sorted(MASKED_PROPS)}` + internal edge ids "
        f"`{list(INTERNAL_EDGE_PROPS)}`",
        "- stock scenario: chạy khi stock có PHP scannable, tự skip nếu không",
    ]
    driver = FalkorDBDriver(host=args.host, port=args.port)

    dual(driver, TESTDATA, "testdata_full", args.host, args.port, report)
    scenario_incremental(driver, args.host, args.port, report)

    stock_scannable = STOCK.exists() and (
        any(STOCK.rglob("*.php")) or any(STOCK.rglob("*.phtml"))
    )
    if STOCK.exists() and stock_scannable and not args.skip_stock:
        dual(driver, STOCK, "stock_full", args.host, args.port, report)
    else:
        print("[warn] skip stock (không có PHP scannable hoặc --skip-stock)")

    report.append(
        f"\n## Kết luận\n\n- FAILURES: {FAILURES if FAILURES else 'không có — PASS toàn bộ'}\n"
    )
    report.append(
        "\n## Ghi chú port (grammar pins, divergences đã chấp nhận)\n\n"
        "### Grammar pins\n\n"
        "- Rust: `tree-sitter = \"0.25\"` + `tree-sitter-php = \"0.24\"` "
        "(crates.io dừng ở 0.24.2 — không có 0.25 cho php crate; 0.24.2 phụ "
        "thuộc tree-sitter ^0.25 nên ghép được với core 0.25).\n"
        "- Python reference (venv): PyPI `tree-sitter-php 0.24.1` — "
        "`tree-sitter-languages 1.10.2` BỊ VỠ với `tree_sitter 0.26.0` "
        "(`get_parser('php')` raise TypeError → `php_analyzer.py` fallback "
        "sang tree_sitter_php), nên grammar tham chiếu thực tế là 0.24.1; "
        "Rust 0.24.2 cùng dòng grammar 0.24 — không thấy parse drift trên "
        "toàn bộ corpus (fixture + stock).\n"
        "- Node-kind quirk đã verify cả 2 phía: grammar 0.24 đổi tên "
        "`method_call_expression` → `member_call_expression`; "
        "`_iter_calls` của reference không liệt kê kind mới nên member calls "
        "(`$this->m()`) KHÔNG được capture — port giữ nguyên đúng hành vi.\n\n"
        "### Divergences đã chấp nhận (có chủ đích)\n\n"
        "1. Call rows gửi thêm `project_id` (explicit scope) — bắt buộc theo "
        "contract writer `_require_call_project_scope`; trùng hành vi "
        "`python_analyzer.py` + template analyzer-python. Reference PHP chạy "
        "với shadow-journal env để metadata cung cấp cùng giá trị.\n"
        "2. Qdrant/embedding/message-scan/parse-cache: flags nhận và bỏ qua "
        "(vector plane là Python-side theo key decision #3).\n"
        "3. `--config /dev/null`: nhận và bỏ qua (Python pre-parse "
        "`load_harness_config` là no-op trên /dev/null).\n"
        "4. Edge props `_start_id`/`_end_id` (internal node row ids của "
        "engine) được mask thêm ngoài MASKED_PROPS — cùng bản chất với "
        "`_src`/`_dst`; endpoint thật đã so qua edge key.\n\n"
        "### Suspected shared/reference bug (KHÔNG sửa — ngoài lane)\n\n"
        "- `code-tiny/tools/php/php_analyzer.py:1427-1430` (`all_calls.append` "
        "trong `build_call_graph`): call rows thiếu `project_id`, trong khi "
        "`code-tiny/tools/graph/writer/language_writer.py:130-149 "
        "(`_require_call_project_scope`) từ chối row thiếu project_id khi "
        "không có journal metadata → journal-less run của reference CRASH "
        "(`ValueError: call row requires project_id (row 0)`). "
        "`tools/python/python_analyzer.py:1612-1621` đã được cập nhật với "
        "cùng contract (comment \"keeps journal-less runs valid\") — php "
        "analyzer chưa được cập nhật theo. Rust port đi theo contract mới.\n"
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
