#!/usr/bin/env python3
"""Phase 02 parity gate — dart analyzer (mode=dart) Python vs Rust.

Dual-run `tools/flutter/flutter_analyzer.py --mode dart` và
`analyzer-dart --mode dart` trên 2 graph FalkorDB riêng (FULL + incremental
+ cleanup), dump và so exact ngoài mask chuẩn.

Standalone: analyzer Python cần graph-journal env shadow mode để writer
stamp `project_id` (call rows) — set CORTEX_GRAPH_JOURNAL_METADATA cho cả
hai phía, giá trị khớp `--project-id` ⇒ stamp giống nhau 2 bên.

Usage (từ repo root):
    .venv/bin/python scripts/rust_parity/analyzer_parity_dart.py
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
    / "phase02-dart-parity.md"
)

SCAN_RE = re.compile(
    r"\[SCAN_RESULT\] parser=dart files=(\d+) nodes=(\d+) edges=(\d+)"
    r" diagnostics=(\d+) graph=(\d+)"
)

FAILURES: list[str] = []
MODE = "dart"


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
            "parser": "dart",
            "parser_version": "py-ref",
            "schema_fingerprint": "parity-fp",
            "query_shape_version": "v1",
        },
        ensure_ascii=True,
    )


def run_py(root: Path, project_id: str, graph: str, host: str, port: int,
           incremental: tuple[Path, Path] | None = None,
           _noop_env: bool = False,
           facts_output: Path | None = None) -> str:
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
    if facts_output:
        cmd.extend(["--facts-output", str(facts_output)])
    cmd.append("--verbose")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900, env=env)
    if proc.returncode != 0:
        raise RuntimeError(
            f"py analyzer failed:\n{proc.stdout[-2500:]}\n{proc.stderr[-2500:]}"
        )
    return proc.stdout


def run_rust(root: Path, project_id: str, graph: str, host: str, port: int,
             incremental: tuple[Path, Path] | None = None,
             _noop_env: bool = False,
             facts_output: Path | None = None) -> str:
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
    if facts_output:
        cmd.extend(["--facts-output", str(facts_output)])
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
    """So byte-identical [SCAN_RESULT] line giữa 2 phía."""
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
    project_id = "parity-dart-inc"
    py_graph = f"p02_{label}_{MODE}_py"
    rust_graph = f"p02_{label}_{MODE}_rs"
    clean_graph(driver, py_graph)
    clean_graph(driver, rust_graph)

    # Seed full run trên cùng graph.
    py_log = run_py(root, project_id, py_graph, host, port)
    rust_log = run_rust(root, project_id, rust_graph, host, port)
    compare_artifacts(py_log, rust_log, f"{label}:seed", report)
    compare_graphs(driver, py_graph, rust_graph, f"{label}:seed:graph", report)

    # Mutate một file + xoá một file, chạy incremental trên cùng graph.
    main_path = root / "lib" / "main.dart"
    backup = main_path.read_text(encoding="utf-8")
    main_path.write_text(
        backup + "\n// parity delta: extra export marker\nclass ParityDelta {}\n",
        encoding="utf-8",
    )
    try:
        with tempfile.TemporaryDirectory(prefix="p02-dart-inc-") as tmp:
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
    parser.add_argument("--skip-stock", action="store_true")
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
        f"- mode: `{MODE}`",
        "- grammar pin: PyPI `tree-sitter-dart==0.1.0` ↔ Rust vendored"
        " `dart-grammar-vendored@0.1.0` (efrenbl/tree-sitter-dart, parser.c"
        " LANGUAGE_VERSION 15 — `rust/grammar-versions.toml [dart]`); runtime"
        " tree-sitter 0.26 (PyPI) ↔ 0.25 (Rust workspace).",
    ]

    if args.no_graph:
        # Artifact-only parity: bỏ qua FalkorDB, so [SCAN_RESULT] + JSON.
        for label in ("testdata_full",):
            py_graph = f"p02_{label}_{MODE}_py"
            rust_graph = f"p02_{label}_{MODE}_rs"
            _ = (py_graph, rust_graph)
            py_log = run_py(TESTDATA, "parity-dart", "noop_py",
                            args.host, args.port, _noop_env=True)
            rust_log = run_rust(TESTDATA, "parity-dart", "noop_rs",
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

    dual(driver, TESTDATA, "testdata_full", "parity-dart", args.host, args.port, report)
    scenario_incremental(driver, TESTDATA, args.host, args.port, report)
    if not args.skip_stock:
        print("[warn] stock repo không phải Flutter/Dart sources — skip như phase plan")

    report.append(
        f"\n## Kết luận\n\n- FAILURES: {FAILURES if FAILURES else 'không có — PASS toàn bộ'}\n"
    )
    report.append(
        "\n## Scope port\n\n"
        "- `tools/flutter/flutter_analyzer.py` (1,659 LOC) — toàn bộ pipeline\n"
        "  (parse `dart_parser.py` + overlay detector `detector.py` +\n"
        "  normalizer `normalizer.py` + dependency cache `cache.py`) được port\n"
        "  sang `rust/crates/analyzer-dart/` (mode `dart` + `flutter` ở\n"
        "  cùng binary). Python `flutter_analyzer.py` chạy `--mode all`;\n"
        "  Rust binary reject `--mode all` với exit 2 (entry criterion 3 —\n"
        "  orchestrator chỉ gọi từng mode).\n"
        "- Qdrant/embedding, message scan: nhận cờ và bỏ qua (vector plane\n"
        "  orchestrator-level phase-06; message plane Python-side phase-05).\n"
        "\n## Grammar pin (entry criterion 1)\n\n"
        "- crates.io `tree-sitter-dart` build từ grammar KHÁC (official Dart\n"
        "  spec) ⇒ node types không khớp PyPI ⇒ không dùng được.\n"
        "- Vendor grammar từ sdist PyPI `tree-sitter-dart==0.1.0` (nguồn\n"
        "  efrenbl/tree-sitter-dart, parser.c @LANGUAGE_VERSION 15) vào crate\n"
        "  `dart-grammar-vendored`. AST golden test\n"
        "  `dart-grammar::tests::ast_shape_matches_python_reference_dump` so\n"
        "  dump 6 node kinds đầu tiên cùng nguồn ↔ PyPI — PASS.\n"
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
