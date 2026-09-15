#!/usr/bin/env python3
"""Phase 02 parity gate — flutter analyzer (mode=flutter) Python vs Rust.

Mirror `analyzer_parity_dart.py` cho mode `flutter` (overlay trên cùng
binary `analyzer-dart`). Cùng fixture `tests/fixtures/flutter-app`
(pubspec.yaml có `dependencies.flutter.sdk: flutter` ⇒ detector cả 2 phía
không skip).

Usage (từ repo root):
    .venv/bin/python scripts/rust_parity/analyzer_parity_flutter.py
"""

from __future__ import annotations

import argparse
import json
import os
import re
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

TESTDATA = REPO / "tests" / "fixtures" / "flutter-app"
PY_BIN = REPO / ".venv" / "bin" / "python"
PY_ANALYZER = REPO / "code-tiny" / "tools" / "flutter" / "flutter_analyzer.py"
RUST_BIN = REPO / "rust" / "target" / "release" / "analyzer-dart"
REPORT_PATH = (
    REPO / "plans" / "260915-analyzer-layer-rust-cutover" / "reports"
    / "phase02-flutter-parity.md"
)

# `parser=flutter` — overlay trên cùng `analyzer-dart` binary.
SCAN_RE = re.compile(
    r"\[SCAN_RESULT\] parser=flutter files=(\d+) nodes=(\d+) edges=(\d+)"
    r" diagnostics=(\d+) graph=(\d+)"
)

FAILURES: list[str] = []
MODE = "flutter"


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
        "FALKORDB_PATH",
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
        "REQUIRE_NEO4J",
    ]:
        env.pop(key, None)
    env["PYTHONPATH"] = str(REPO / "code-tiny")
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
            "parser": "flutter",
            "parser_version": "py-ref",
            "schema_fingerprint": "parity-fp",
            "query_shape_version": "v1",
        },
        ensure_ascii=True,
    )


def run_py(root: Path, project_id: str, graph: str, host: str, port: int,
           incremental: tuple[Path, Path] | None = None,
           _noop_env: bool = False) -> str:
    env = analyzer_env()
    env["CORTEX_GRAPH_JOURNAL_MODE"] = "shadow"
    env["CORTEX_GRAPH_JOURNAL_PATH"] = str(env.pop("_JOURNAL_DIR", "/tmp")) + "/journal"
    env["CORTEX_GRAPH_JOURNAL_METADATA"] = journal_metadata(project_id, graph)
    if _noop_env:
        env["CORTEX_DISABLE_GRAPH"] = "1"
    cmd = [
        str(PY_BIN), str(PY_ANALYZER),
        "--mode", MODE,
        "--root", str(root),
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
             incremental: tuple[Path, Path] | None = None,
             _noop_env: bool = False) -> str:
    env = analyzer_env() if _noop_env else None
    if env is not None:
        env["CORTEX_DISABLE_GRAPH"] = "1"
    cmd = [
        str(RUST_BIN),
        "--mode", MODE,
        "--root", str(root),
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
            f"rust analyzer failed:\n{proc.stdout[-2500:]}\n{proc.stderr[-2500:]}"
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
        report.append(json.dumps(diff, indent=2, ensure_ascii=True, default=str)[:16000])
        report.append("\n```\n")


def compare_artifacts(py_log: str, rust_log: str, label: str,
                      report: list[str]) -> None:
    py_scan = scan_result_of(py_log)
    rust_scan = scan_result_of(rust_log)
    check(f"{label}: [SCAN_RESULT] byte-identical", py_scan == rust_scan,
          f"py={py_scan!r} rust={rust_scan!r}")
    report.append(
        f"\n### {label} — [SCAN_RESULT]\n\n- py: `{py_scan}`\n- rust: `{rust_scan}`\n"
    )


def dual(driver: FalkorDBDriver, root: Path, label: str, project_id: str,
         host: str, port: int, report: list[str],
         incremental: tuple[Path, Path] | None = None) -> None:
    py_graph = f"p02_{label}_{MODE}_py"
    rust_graph = f"p02_{label}_{MODE}_rs"
    clean_graph(driver, py_graph)
    clean_graph(driver, rust_graph)
    py_log = run_py(root, project_id, py_graph, host, port, incremental=incremental)
    rust_log = run_rust(root, project_id, rust_graph, host, port, incremental=incremental)
    compare_artifacts(py_log, rust_log, f"{label}", report)
    compare_graphs(driver, py_graph, rust_graph, f"{label}:graph", report)


def scenario_incremental(driver: FalkorDBDriver, root: Path, host: str,
                         port: int, report: list[str]) -> None:
    """Incremental chạy trên graph đã seed (production flow)."""
    label = "testdata_inc"
    project_id = "parity-flutter-inc"
    py_graph = f"p02_{label}_{MODE}_py"
    rust_graph = f"p02_{label}_{MODE}_rs"
    clean_graph(driver, py_graph)
    clean_graph(driver, rust_graph)

    py_log = run_py(root, project_id, py_graph, host, port)
    rust_log = run_rust(root, project_id, rust_graph, host, port)
    compare_artifacts(py_log, rust_log, f"{label}:seed", report)
    compare_graphs(driver, py_graph, rust_graph, f"{label}:seed:graph", report)

    main_path = root / "lib" / "main.dart"
    backup = main_path.read_text(encoding="utf-8")
    main_path.write_text(
        backup + "\n// parity delta: extra export marker\nclass ParityDelta {}\n",
        encoding="utf-8",
    )
    try:
        with tempfile.TemporaryDirectory(prefix="p02-flutter-inc-") as tmp:
            changed_manifest = Path(tmp) / "changed.json"
            deleted_manifest = Path(tmp) / "deleted.json"
            changed_manifest.write_text(
                json.dumps({"files": ["lib/main.dart"]}) + "\n", encoding="utf-8"
            )
            deleted_manifest.write_text(
                json.dumps({"files": ["lib/broken.dart"]}) + "\n", encoding="utf-8"
            )
            manifests = (changed_manifest, deleted_manifest)
            py_log = run_py(root, project_id, py_graph, host, port, incremental=manifests)
            rust_log = run_rust(root, project_id, rust_graph, host, port, incremental=manifests)
            compare_artifacts(py_log, rust_log, f"{label}:inc", report)
            compare_graphs(driver, py_graph, rust_graph, f"{label}:inc:graph", report)
    finally:
        main_path.write_text(backup, encoding="utf-8")


def main() -> int:
    global RUST_BIN

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6379)
    parser.add_argument("--rust-bin", default=str(RUST_BIN))
    parser.add_argument("--no-graph", action="store_true",
                        help="skip FalkorDB graph diff — chỉ so `[SCAN_RESULT]`"
                             " + artifact JSON (CORTEX_DISABLE_GRAPH=1 cả 2 phía).")
    args = parser.parse_args()

    RUST_BIN = Path(args.rust_bin)

    report = [
        f"# Phase 02 — {MODE} analyzer parity (python vs rust)",
        "",
        f"- chạy: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- testdata: `{TESTDATA.relative_to(REPO)}`",
        f"- mask: `{sorted(MASKED_PROPS)}`",
        f"- mode: `{MODE}` (overlay; analyzer-dart binary chia sẻ với `dart`)",
        "- grammar pin: PyPI `tree-sitter-dart==0.1.0` ↔ Rust vendored"
        " `dart-grammar-vendored@0.1.0` (cùng sdist efrenbl/tree-sitter-dart).",
    ]

    if args.no_graph:
        for label in ("testdata_full",):
            py_graph = f"p02_{label}_{MODE}_py"
            rust_graph = f"p02_{label}_{MODE}_rs"
            _ = (py_graph, rust_graph)
            py_log = run_py(TESTDATA, "parity-flutter", "noop_py",
                            args.host, args.port, _noop_env=True)
            rust_log = run_rust(TESTDATA, "parity-flutter", "noop_rs",
                                args.host, args.port, _noop_env=True)
            compare_artifacts(py_log, rust_log, label, report)
        report.append("\n## Note\n\n- Chạy `--no-graph`: graph diff skipped"
                      " (FalkorDB unavailable). `[SCAN_RESULT]` byte-identical ="
                      " artifact gate PASS. Composition + graph leg ở phase-07.\n")
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text("\n".join(report) + "\n", encoding="utf-8")
        print(f"\nreport → {REPORT_PATH.relative_to(REPO)}")
        if FAILURES:
            print(f"FAILED: {FAILURES}")
            return 1
        print("ALL GATES PASS (artifact-only)")
        return 0

    driver = FalkorDBDriver(host=args.host, port=args.port)

    dual(driver, TESTDATA, "testdata_full", "parity-flutter", args.host, args.port, report)
    scenario_incremental(driver, TESTDATA, args.host, args.port, report)

    report.append(
        f"\n## Kết luận\n\n- FAILURES: {FAILURES if FAILURES else 'không có — PASS toàn bộ'}\n"
    )
    report.append(
        "\n## Scope port\n\n"
        "- Cùng crate `analyzer-dart` như mode `dart`, chỉ thay đổi flag\n"
        "  `--mode flutter` (entry criterion 3). Trước flag, binary gọi\n"
        "  `detect_flutter_project(root)` — pubspec không có\n"
        "  `dependencies.flutter.sdk: flutter` thì in skip line + exit 0\n"
        "  (orchestrator chấp nhận); nếu có thì tiếp tục pipeline\n"
        "  `analyze_project` giống mode `dart`.\n"
        "- `flutter` framework map sang cùng binary trong registry:\n"
        "  `framework_rust_binaries()[\"flutter\"] = \"analyzer-dart\"`\n"
        "  (`rust/crates/cortex-sync/src/registry.rs`). Phase-01 comment cũ\n"
        "  \"flutter must stay unmapped until analyzer-dart exists\" được\n"
        "  cập nhật trong test `framework_map_entries_and_shared_database_schema`.\n"
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
