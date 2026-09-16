#!/usr/bin/env python3
"""Phase 07 parity gate — jp1 analyzer Python vs Rust.

Dual-run `code-tiny/tools/jp1/jp1_analyzer.py` và `analyzer-jp1` trên 2 graph
FalkorDB riêng (FULL + incremental với changed/deleted manifests + cleanup
trên graph đã seed), dump và so exact ngoài mask chuẩn.

Parser: line-based regex (unit=/ty|cm|te=/ar=) — KHÔNG dùng grammar nên
không có rủi ro tree-sitter version skew. Decode legacy (utf-16/utf-8-sig/
utf-8/cp932/cp1252-replace) port byte-exact (cp932 bảng đầy đủ sinh từ
CPython).

Accommodation (không sửa rows): jp1_analyzer.py emit
`Jp1Unit-[:CALLS]->ShellScript` mà KHÔNG có writer nào tạo node ShellScript ⇒
endpoint preflight fail-loud ở CẢ 2 writer (upstream bug, cùng nhóm với
go_analyzer ExternalModule INCLUDES). Env quarantine
`CORTEX_DIAGNOSTIC_SKIP_UNRESOLVED_RELATIONS=1` đặt cho CẢ 2 phía —
INCLUDES/NEXT (Jp1Unit→Jp1Unit) vẫn ghi đầy đủ và so sánh.

Usage (từ repo root):
    .venv/bin/python scripts/rust_parity/analyzer_parity_jp1.py
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

TESTDATA = REPO / "tests" / "fixtures" / "jp1-analyzer"
PY_BIN = REPO / ".venv" / "bin" / "python"
PY_REFERENCE = REPO / "code-tiny" / "tools" / "jp1" / "jp1_analyzer.py"
RUST_BIN = REPO / "rust" / "target" / "release" / "analyzer-jp1"
REPORT_PATH = (
    REPO / "plans" / "260913-2130-rust-full-migration" / "reports"
    / "phase07-jp1-parity.md"
)
STOCK = Path("/Users/user/baka3k/stock")
PROJECT_ID = "parity_jp1"

SCAN_RE = re.compile(
    r"\[SCAN_RESULT\] parser=jp1 files=(\d+) functions=0 "
    r"vectors=(\d+) vector_status=(\w+)"
)
CLEANUP_PLAN_RE = re.compile(r"\[cleanup\]\[graph\] deleting graph data for (\d+) files")
CLEANUP_COUNT_RE = re.compile(
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
        "PROJECT_REPO",
        "PROJECT_BUILD_SYSTEM",
        "GIT_COMMIT_SHA_BEFORE",
        "GIT_COMMIT_SHA_AFTER",
        "CORTEX_EXTRA_IGNORE_DIRS",
        "MESSAGE_OUTPUT_DIR",
        "MESSAGE_QDRANT_COLLECTION",
        "CODE_EMBEDDING_MODEL",
        "EMBED_DEVICE",
        "EMBED_BATCH_SIZE",
        "MAX_EMBED_CHARS",
        "CORTEX_DISABLE_GRAPH",
    ]:
        env.pop(key, None)
    # CALLS→ShellScript không bao giờ materialize được (không có node
    # ShellScript) — quarantine CÙNG các rows đó trên CẢ 2 phía.
    env["CORTEX_DIAGNOSTIC_SKIP_UNRESOLVED_RELATIONS"] = "1"
    return env


def common_cmd(root: Path, graph: str, host: str, port: int,
               incremental: tuple[Path, Path] | None = None) -> list[str]:
    cmd = [
        "--root", str(root),
        "--project-id", PROJECT_ID,
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
    # jp1_analyzer.py không có --config — chỉ Rust CLI nhận (accept-and-ignore).
    cmd = [
        str(RUST_BIN),
        "--config", "/dev/null",
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


def cleanup_counts_of(log: str) -> tuple[int, int] | None:
    matches = CLEANUP_COUNT_RE.findall(log)
    return tuple(int(v) for v in matches[-1]) if matches else None  # type: ignore[return-value]


def cleanup_plan_of(log: str) -> int | None:
    matches = CLEANUP_PLAN_RE.findall(log)
    return int(matches[-1]) if matches else None


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
    graph_tag = graph_tag or tag
    graph_py = f"p07_jp1_{graph_tag}_py"
    graph_rust = f"p07_jp1_{graph_tag}_rs"
    if clean:
        clean_graph(driver, graph_py)
        clean_graph(driver, graph_rust)
    py_log = run_py(root, graph_py, host, port, incremental)
    rust_log = run_rust(root, graph_rust, host, port, incremental)
    py_scan, rust_scan = scan_result_of(py_log), scan_result_of(rust_log)
    check(f"{tag}: [SCAN_RESULT] byte-identical", py_scan == rust_scan,
          f"py={py_scan!r} rust={rust_scan!r}")
    report.append(f"\n### scan_result {tag}\n\n- py: `{py_scan}`\n- rust: `{rust_scan}`\n")
    if incremental:
        py_plan, rust_plan = cleanup_plan_of(py_log), cleanup_plan_of(rust_log)
        py_cleanup, rust_cleanup = cleanup_counts_of(py_log), cleanup_counts_of(rust_log)
        check(f"{tag}: cleanup lines xuất hiện ở cả 2 log",
              py_cleanup is not None and rust_cleanup is not None
              and py_plan is not None and rust_plan is not None,
              f"py=({py_plan}, {py_cleanup}) rust=({rust_plan}, {rust_cleanup})")
        check(f"{tag}: cleanup counts khớp", (py_plan, py_cleanup) == (rust_plan, rust_cleanup),
              f"py=({py_plan}, {py_cleanup}) rust=({rust_plan}, {rust_cleanup})")
        report.append(
            f"- cleanup: py=({py_plan}, {py_cleanup}) rust=({rust_plan}, {rust_cleanup})\n"
        )
    compare_graphs(driver, graph_py, graph_rust, tag.upper(), report)


def scenario_incremental(driver: FalkorDBDriver, host: str, port: int, report: list[str]) -> None:
    with tempfile.TemporaryDirectory(prefix="p07_jp1_inc_") as tmp:
        workdir = Path(tmp) / "corpus"
        workdir.mkdir()
        for name in ("JC00NIGHT.txt", "JC00KANJI.txt", "run.sh"):
            shutil.copy2(TESTDATA / name, workdir / name)
        shutil.copytree(TESTDATA / "scripts", workdir / "scripts")

        # FULL seed (dual tự clean 2 graph trước khi chạy)
        dual(driver, workdir, "inc_seed", host, port, report)

        # Mutate: sửa JC00NIGHT.txt (thêm unit + ar), thêm file mới,
        # xoá JC00KANJI.txt (cleanup phải xoá units của file đã xoá).
        (workdir / "JC00NIGHT.txt").write_text(
            (workdir / "JC00NIGHT.txt").read_text(encoding="utf-8")
            + "unit=ARCHIVE,,NT001,;\n"
            + "{\n"
            + "ty=j;\n"
            + "te=\"run.sh\";\n"
            + "}\n"
            + "ar=(f=LOAD,t=ARCHIVE,seq);\n"
            + "ar=(f=FETCH,t=ARCHIVE,seq);\n",
            encoding="utf-8",
        )
        added = workdir / "NEWNET.txt"
        added.write_text(
            "unit=NEWROOT,,NT003,;\n"
            "{\n"
            "ty=n;\n"
            "cm=\"added for parity incremental test\";\n"
            "unit=NEWSTEP,,NT003,;\n"
            "{\n"
            "ty=j;\n"
            "te=\"../run.sh\";\n"
            "}\n"
            "}\n",
            encoding="utf-8",
        )
        (workdir / "JC00KANJI.txt").unlink()

        changed_manifest = Path(tmp) / "changed.json"
        deleted_manifest = Path(tmp) / "deleted.json"
        changed_manifest.write_text(
            json.dumps({"files": ["JC00NIGHT.txt", "NEWNET.txt"]}) + "\n",
            encoding="utf-8",
        )
        deleted_manifest.write_text(
            json.dumps({"files": ["JC00KANJI.txt"]}) + "\n", encoding="utf-8"
        )
        # KHÔNG clean giữa seed và incremental — incremental chạy TRÊN graph
        # seed (graph_tag="inc_seed") nên cleanup-before-write xoá node thật.
        dual(driver, workdir, "inc_run", host, port, report,
             incremental=(changed_manifest, deleted_manifest), clean=False,
             graph_tag="inc_seed")


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
        print("Build first: cargo build --release -p analyzer-jp1 (run inside rust/)")
        return 1
    if not TESTDATA.is_dir():
        print(f"JP1 testdata not found: {TESTDATA}")
        return 1

    report = [
        "# Phase 07 — jp1 analyzer parity (python vs rust)",
        "",
        f"- chạy: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- testdata: `{TESTDATA.relative_to(REPO)}` — 2 file sample khó: "
        "JC00NIGHT.txt (nested jobnet 2 tầng, ar= multi-scope, 1 arc không "
        "resolve, queue unit không exec target, mixed-case props Ty/TE) và "
        "JC00KANJI.txt (cp932/Shift_JIS với comment Nhật — exercise legacy "
        "decode path); kèm scripts/ để exec target resolve được.",
        "- parser: line-based regex (`^unit=`, `^(ty|cm|te)=`, `ar=(f=..,t=..)`), "
        "KHÔNG dùng grammar ⇒ không có tree-sitter version skew. Decode legacy "
        "port byte-exact: BOM utf-16/utf-8-sig → NUL heuristic → utf-8 → "
        "cp932 (bảng full sinh từ CPython: 18381 pair + byte đơn) → cp1252 "
        "errors=replace (5 byte undefined ⇒ U+FFFD).",
        "- mask: `" + str(sorted(MASKED_PROPS)) + "`",
        "- accommodation (không sửa rows): `CORTEX_DIAGNOSTIC_SKIP_UNRESOLVED_"
        "RELATIONS=1` cho CẢ 2 phía — jp1_analyzer.py emit "
        "`Jp1Unit-[:CALLS]->ShellScript` mà không writer nào tạo node "
        "ShellScript ⇒ endpoint preflight fail-loud (upstream). INCLUDES/NEXT "
        "giữa Jp1Unit vẫn ghi đầy đủ và so sánh.",
        "- project-scope: `--project-id=parity_jp1` cho cả 2 phía (unit_id có "
        "file_path rel nên 2 phía dùng chung root corpus tạm cho từng run).",
    ]
    driver = FalkorDBDriver(host=args.host, port=args.port)

    dual(driver, TESTDATA, "testdata_full", args.host, args.port, report)
    scenario_incremental(driver, args.host, args.port, report)

    if not args.skip_stock and STOCK.exists():
        note = (
            "[warn] stock scenario bỏ qua — stock không chứa file .txt dạng "
            "JP1 jobnet export; lưu trong report."
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
| `cargo clippy -p analyzer-jp1 --all-targets -- -D warnings` | PASS (0 warning) |
| `cargo test -p analyzer-jp1` | PASS (4 golden tests) |
| testdata_full: [SCAN_RESULT] byte-identical | PASS |
| testdata_full: graph diff ngoài mask | PASS (diff=0) |
| inc_seed (FULL trên corpus copy): scan + diff | PASS (diff=0) |
| inc_run (incremental trên graph seed, clean=False): scan | PASS (byte-identical) |
| inc_run: cleanup-before-write counts (regex bắt buộc khớp) | PASS |
| inc_run: graph diff ngoài mask | PASS (diff=0) |
| stock | SKIP — stock không chứa nguồn JP1 |

## Accepted divergences (không tác động graph-plane)

1. `[SCAN_RESULT]` luôn `vectors=0 vector_status=disabled` — Rust không embed
   Qdrant (key decision #3); Python chỉ sync khi `--qdrant-url` được truyền
   (harness không truyền).
2. `--config /dev/null` (Rust nhận và bỏ qua); jp1_analyzer.py KHÔNG có cờ này.
3. `--output/-o`, `--pretty`, `--dry-run` payload JSON, `--cache-dir`,
   `--ignore-cache`, message-scan flags: nhận và bỏ qua (plane Python/debug).
   Python in JSON payload khi `--dry-run`/`--pretty`; Rust không in (debug
   plane, không so parity).
4. Verbose log lines (ngoài `[SCAN_RESULT]`/`[cleanup][graph]`) không bắt buộc
   byte-identical.
5. CLI: positional `path` của jp1_analyzer.py không port làm positional độc
   lập (orchestrator luôn truyền `--root`); `--root` bắt buộc ở Rust (clap).
6. `cp932` decode: bảng sinh từ CPython nên strict-identical (kể cả các
   mapping 2 ký tự PUA của lead 0xA0..=0xDF/0xFD..0xFF). `cp1252` replace:
   5 byte undefined (81 8D 8F 90 9D) ⇒ U+FFFD như CPython (khác WHATWG).
7. Provider `neo4j`: Rust backend không hỗ trợ (fail khác chỗ so với Python
   trả None ⇒ RuntimeError) — harness dùng falkordb.

## Upstream findings (không sửa — ngoài scope crate này)

1. **jp1_analyzer.py + writer contract: CALLS→ShellScript fail-loud.**
   jp1_analyzer.py emit `Jp1Unit-[:CALLS]->ShellScript` (exec_target) nhưng
   không node ShellScript nào được tạo ⇒ endpoint preflight raise ở CẢ Python
   lẫn Rust writer khi graph chưa có sẵn target. Harness quarantine các rows
   này qua `CORTEX_DIAGNOSTIC_SKIP_UNRESOLVED_RELATIONS=1` cho cả 2 phía;
   INCLUDES/NEXT ghi đầy đủ.
2. `_clean` của jp1 strip `"` cả hai đầu sau khi rstrip `;` — hành vi giữ
   nguyên trong port (unit name/comment có thể mất quote bao ngoài).
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
