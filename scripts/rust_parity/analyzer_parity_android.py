#!/usr/bin/env python3
"""Phase 06 parity gate — android analyzer (kotlin) Python vs Rust.

Dual-run `tools/android/android_kotlin_analyzer.py` và `analyzer-android` trên
2 graph FalkorDB riêng (FULL + incremental + cleanup), dump và so exact ngoài
mask chuẩn.

Lưu ý chạy Python standalone: analyzer không gắn `project_id` vào call rows
(write_calls_with_site fail-closed) — parity harness set graph-journal env
mode=shadow (đúng contract orchestrator) để writer lấy project_id từ journal
metadata, cùng giá trị với `--project-id` ⇒ stamp như nhau 2 bên.

Stock repo không phải Android sources (không .kt) ⇒ bỏ qua, như phase plan.

Usage (từ repo root):
    .venv/bin/python scripts/rust_parity/analyzer_parity_android.py
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

TESTDATA = REPO / "tests" / "fixtures" / "android-analyzer"
PY_BIN = REPO / ".venv" / "bin" / "python"
PY_ANALYZER = REPO / "code-tiny" / "tools" / "android" / "android_kotlin_analyzer.py"
RUST_BIN = REPO / "rust" / "target" / "release" / "analyzer-android"
REPORT_PATH = (
    REPO / "plans" / "260913-2130-rust-full-migration" / "reports"
    / "phase06-android-parity.md"
)

SCAN_RE = re.compile(
    r"\[SCAN_RESULT\] parser=android-kotlin files=(\d+) functions=(\d+) classes=(\d+)"
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
    """Sanitized env cho analyzer (copy pattern analyzer_parity_shell.py)."""
    env = dict(os.environ)
    for key in [
        "QDRANT_CODE_PATH",
        "QDRANT_COLLECTION",
        "FALKORDB_URI",
        "FALKORDB_GRAPH",
        "PROJECT_ID",
        "CORTEX_EXTRA_IGNORE_DIRS",
    ]:
        env.pop(key, None)
    # Graph-journal shadow mode — writer cần project_id cho call rows; giá trị
    # trùng --project-id nên Python và Rust stamp giống hệt nhau.
    env["CORTEX_GRAPH_JOURNAL_MODE"] = "shadow"
    env["CORTEX_GRAPH_JOURNAL_PATH"] = str(env.pop("_JOURNAL_DIR", "/tmp")) + "/journal"
    return env


def journal_metadata(project_id: str, graph: str) -> str:
    return json.dumps(
        {
            "project_id": project_id,
            "scope_id": f"{project_id}-scope",
            "source_revision": "HEAD",
            "source_snapshot": "parity",
            "physical_target": f"falkordb://127.0.0.1:6379/{graph}",
            "generation": "g1",
            "parser": "android",
            "parser_version": "py-ref",
            "schema_fingerprint": "parity-fp",
            "query_shape_version": "v1",
        },
        ensure_ascii=True,
    )


def run_py(root: Path, project_id: str, graph: str, host: str, port: int,
           incremental: tuple[Path, Path] | None = None) -> str:
    env = analyzer_env()
    env["CORTEX_GRAPH_JOURNAL_METADATA"] = journal_metadata(project_id, graph)
    cmd = [
        str(PY_BIN), str(PY_ANALYZER),
        "--root", str(root),
        "--config", "/dev/null",
        "--project-id", project_id,
        "--graph-provider", "falkordb",
        "--falkordb-uri", f"{host}:{port}",
        "--falkordb-graph", graph,
        "--disable-message-scan",
    ]
    if incremental:
        cmd.append("--incremental")
        changed, deleted = incremental
        if changed:
            cmd.extend(["--changed-files-manifest", str(changed)])
        if deleted:
            cmd.extend(["--deleted-files-manifest", str(deleted)])
    cmd.append("--verbose")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900, env=env)
    if proc.returncode != 0:
        raise RuntimeError(
            f"py analyzer failed:\n{proc.stdout[-2500:]}\n{proc.stderr[-2500:]}"
        )
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
    ]
    if incremental:
        cmd.append("--incremental")
        changed, deleted = incremental
        if changed:
            cmd.extend(["--changed-files-manifest", str(changed)])
        if deleted:
            cmd.extend(["--deleted-files-manifest", str(deleted)])
    cmd.append("--verbose")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if proc.returncode != 0:
        raise RuntimeError(
            f"rust analyzer failed:\n{proc.stdout[-2500:]}\n{proc.stderr[-2500:]}"
        )
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
        report.append(json.dumps(diff, indent=2, ensure_ascii=True, default=str)[:16000])
        report.append("\n```\n")


def run_py_settled(root: Path, project_id: str, graph: str, host: str, port: int,
                   incremental: tuple[Path, Path] | None = None) -> str:
    """Chạy Python với settle sau clean + retry 1 lần.

    FalkorDB đôi khi trả `target_matches=0` trên endpoint audit cho node vừa
    MERGE ngay sau DETACH DELETE (flaky visibility, không reproduce khi chạy
    standalone). Gate là port — retry-on-infra là chấp nhận được; fail 2 lần
    liên tiếp vẫn raise.
    """
    last_error: Exception | None = None
    for attempt in range(2):
        time.sleep(1.0 if attempt == 0 else 3.0)
        try:
            return run_py(root, project_id, graph, host, port, incremental)
        except RuntimeError as error:
            last_error = error
            clean_graph(FalkorDBDriver(host=host, port=port), graph)
    assert last_error is not None
    raise last_error


def run_rust_settled(root: Path, project_id: str, graph: str, host: str, port: int,
                     incremental: tuple[Path, Path] | None = None) -> str:
    """Tương tự run_py_settled cho binary Rust (cùng endpoint audit)."""
    last_error: Exception | None = None
    for attempt in range(2):
        time.sleep(1.0 if attempt == 0 else 3.0)
        try:
            return run_rust(root, project_id, graph, host, port, incremental)
        except RuntimeError as error:
            last_error = error
            clean_graph(FalkorDBDriver(host=host, port=port), graph)
    assert last_error is not None
    raise last_error


def dual(driver: FalkorDBDriver, root: Path, tag: str, host: str, port: int,
         report: list[str], incremental: tuple[Path, Path] | None = None,
         clean: bool = True, graph_tag: str | None = None) -> None:
    # graph_tag: graph-name override — incremental chạy TIẾP trên graph đã seed
    # (production flow), nên label report và graph name tách biệt.
    graph_tag = graph_tag or tag
    graph_py = f"p06_android_{graph_tag}_py"
    graph_rust = f"p06_android_{graph_tag}_rs"
    if clean:
        clean_graph(driver, graph_py)
        clean_graph(driver, graph_rust)
    py_log = run_py_settled(root, "parity_android", graph_py, host, port, incremental)
    rust_log = run_rust_settled(root, "parity_android", graph_rust, host, port, incremental)
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
    with tempfile.TemporaryDirectory(prefix="p06_android_inc_") as tmp:
        workdir = Path(tmp) / "corpus"
        shutil.copytree(TESTDATA, workdir)

        # FULL seed trên graph pair `inc_seed` (như production: full trước,
        # incremental CHẠY TIẾP trên graph đã có dữ liệu — analyzer infer
        # component từ EXTENDS của mọi file, nên incremental trên graph rỗng
        # làm Python fail-closed ở endpoint preflight).
        dual(driver, workdir, "inc_seed", host, port, report)

        # Mutate: sửa NavGraph.kt (thêm composable route + function),
        # thêm file mới import package data (impacted-by-imports),
        # xoá SyncService.kt.
        (workdir / "app/src/main/kotlin/com/paritydemo/ui/NavGraph.kt").write_text(
            '''package com.paritydemo.ui

import androidx.compose.runtime.Composable
import com.paritydemo.data.UserRepository
import com.paritydemo.data.SyncState

@Composable
fun ParityNavHost(startRoute: String) {
    wireRoutes(startRoute)
}

private fun wireRoutes(startRoute: String) {
    routeTable("home", content = { HomeScreen() })
    routeTable("profile", content = { ProfileScreen() })
}

@Composable
fun HomeScreen() {
    headline()
    UserRepository().displayName("7")
}

@Composable
fun ProfileScreen() {
    headline()
    SyncState.IDLE
}

@Composable
fun SettingsScreen() {
    headline()
}

@Composable
private fun headline() {
    androidcompose.Text("parity")
}

fun extraRouteHelper() {
    wireRoutes("extra")
}
''',
            encoding="utf-8",
        )
        added = workdir / "app/src/main/kotlin/com/paritydemo/ui/NewScreen.kt"
        added.write_text(
            '''package com.paritydemo.ui

import com.paritydemo.data.UserRepository

fun newScreenGreet(): String {
    val repository = UserRepository()
    return repository.displayName("42")
}
''',
            encoding="utf-8",
        )
        (workdir / "app/src/main/kotlin/com/paritydemo/data/SyncService.kt").unlink()

        changed_manifest = Path(tmp) / "changed.json"
        deleted_manifest = Path(tmp) / "deleted.json"
        changed_manifest.write_text(
            json.dumps({"files": [
                "app/src/main/kotlin/com/paritydemo/ui/NavGraph.kt",
                "app/src/main/kotlin/com/paritydemo/ui/NewScreen.kt",
            ]})
            + "\n",
            encoding="utf-8",
        )
        deleted_manifest.write_text(
            json.dumps({"files": [
                "app/src/main/kotlin/com/paritydemo/data/SyncService.kt",
            ]})
            + "\n",
            encoding="utf-8",
        )
        dual(driver, workdir, "inc_run", host, port, report,
             incremental=(changed_manifest, deleted_manifest), clean=False,
             graph_tag="inc_seed")


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
        "# Phase 06 — android analyzer parity (python vs rust)",
        "",
        f"- chạy: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- testdata: `{TESTDATA.relative_to(REPO)}`",
        f"- mask: `{sorted(MASKED_PROPS)}`",
        "- grammar pins: PyPI tree-sitter-kotlin 1.1.0 ↔ crate `tree-sitter-kotlin-ng` 1.1.0"
        " (cùng repo tree-sitter-grammars/tree-sitter-kotlin tag v1.1.0); runtime"
        " tree-sitter 0.26.0 (PyPI) ↔ 0.25 (Rust workspace)",
    ]
    driver = FalkorDBDriver(host=args.host, port=args.port)

    dual(driver, TESTDATA, "testdata_full", args.host, args.port, report)
    scenario_incremental(driver, args.host, args.port, report)
    if not args.skip_stock:
        print("[warn] stock repo không phải Android sources (0 .kt) — skip như phase plan")

    report.append(
        f"\n## Kết luận\n\n- FAILURES: {FAILURES if FAILURES else 'không có — PASS toàn bộ'}\n"
    )
    report.append(
        "\n## Scope port\n\n"
        "- `tools/android/android_kotlin_analyzer.py` (4.6k, entry `android` trong"
        " ANALYZERS) toàn bộ parse + row assembly + writer calls"
        " (`write_nodes_batch` per label + `write_relations_typed` +"
        " `write_calls_with_site`, node queries copy nguyên chữ).\n"
        "- `tools/android/android_common.py` — phần được analyzer dùng: symbol-id"
        " helpers, `_parse_android_manifest` (DOM + serializer khớp"
        " `ET.tostring`), `_normalize_rel_path`, directory nodes/relations."
        " `_extract_resource_ids_from_xml` không có hiệu ứng graph (regex broken"
        " phía Python ⇒ luôn rỗng).\n"
        "- `android_java_analyzer.py` / `android_mixed_analyzer.py` KHÔNG thuộc"
        " entry này: orchestrator chỉ đăng ký `android_kotlin_analyzer.py` cho"
        " parser `android` và bản kotlin không import 2 file kia (mixed là"
        " script orchestrator riêng, chạy java+kotlin bằng subprocess).\n"
        "- Qdrant/embedding, message scan, parse cache, neo4j resume: nhận cờ"
        " và bỏ qua (embedding/message là plane Python; quyết kiến trúc phase 04).\n"
        "\n## Grammar pins\n\n"
        "- PyPI `tree-sitter-kotlin==1.1.0` (repo tree-sitter-grammars/"
        "tree-sitter-kotlin; `_get_kotlin_parser` fallback sang grammar crate vì"
        " tree-sitter-languages 1.10.2 raise TypeError với tree-sitter ≥0.25)"
        " ↔ crates.io `tree-sitter-kotlin-ng==1.1.0` (cùng tag v1.1.0)."
        " crates.io `tree-sitter-kotlin` (fwcd) là grammar KHÁC — không dùng.\n"
        "- Runtime: PyPI `tree-sitter==0.26.0` ↔ Rust `tree-sitter@0.25`"
        " (workspace pin, như analyzer-java với `tree-sitter-java@0.23.5`).\n"
        "\n## Divergence đã ghi nhận (không ảnh hưởng graph parity)\n\n"
        "1. **Call rows gắn `project_id` tường minh (Rust)** — bản Python build"
        " rows không có `project_id` và chỉ chạy được khi orchestrator set"
        " graph-journal env (writer stamp từ journal metadata). Rust gắn trực"
        " tiếp cùng giá trị; writer stamp như nhau 2 bên (props CALLS edge giống"
        " hệt). Không có journal env, Python crash ở `write_calls_with_site`"
        " (fail-closed) — parity harness set journal shadow mode cho cả 2."
        " Limitation phía analyzer Python, không phải shared crate.\n"
        "2. **Regex double-backslash của Python giữ nguyên semantics** — nhiều"
        " pattern trong analyzer gốc là raw-string \\\\s (backslash literal +"
        " `s*`) nên thực tế không match text thường: `_parse_gradle_file`"
        " (namespace/applicationId/dependency), `_extract_resource_refs`,"
        " `_extract_resource_ids_from_xml`, kotlin `_extract_class_refs`,"
        " `_extract_handler_tokens`, `_extract_intentfilter_*`,"
        " `_extract_component_name_target`, intent-var regexes,"
        " `_extract_route_from_args` nhánh 1, `startDestination`"
        " (⇒ `start_routes` luôn rỗng). Port copy từng chữ.\n"
        "3. **Python crash khi `composable(\"route\") { … }` trailing-lambda** —"
        " `_extract_compose_routes_from_tree` compile pattern lỗi cú pháp"
        " (unbalanced group, `re.error`) khi composable không extract được"
        " target từ lambda; trailing lambda không nằm trong subtree"
        " `call_expression` của grammar ⇒ targets rỗng ⇒ crash. Fixtures dùng"
        " dạng `composable(route=…, content = { … })` (Python chạy được); Rust"
        " treat pattern như no-match và tiếp tục (Python crash ⇒ không có graph"
        " để so).\n"
        "4. **Incremental phải chạy trên graph đã seed** — analyzer infer"
        " AndroidComponent từ EXTENDS của mọi file (index_payloads) nhưng Class"
        " node chỉ ghi cho file selected; incremental trên graph rỗng làm cả"
        " Python lẫn Rust fail-closed ở endpoint preflight (hành vi giống nhau)."
        " Harness cho chạy full-seed trước rồi incremental trên cùng graph"
        " (production flow).\n"
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
