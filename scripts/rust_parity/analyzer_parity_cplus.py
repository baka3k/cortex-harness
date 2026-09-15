#!/usr/bin/env python3
"""Phase 07 parity gate — C/C++ analyzer (cplus) Python vs Rust.

Dual-run `tools/cplus/cplus_analyzer.py` và `analyzer-cplus` trên 2 graph
FalkorDB riêng (FULL + incremental với manifests + cleanup), dump và so exact
ngoài mask chuẩn từ dual_write_diff.

Usage (từ repo root):
    .venv/bin/python scripts/rust_parity/analyzer_parity_cplus.py

Ghi chú:
- Fixture chạy ở tree-sitter fallback mode (không compile_commands.json, không
  bootstrap clang) — cả 2 bên đều fallback mode (parse-quality default `report`
  tự set --disable-compile-db-bootstrap).
- Python reference cần journal env (shadow) vì call rows không có project_id
  tường minh — writer lấy từ journal metadata như orchestrator (lane cplus chạy
  `shared-required` ở production; parity dùng `shared-shadow` để loại plane
  Project/Repository setup khỏi diff — contract đó đã gate ở phase 03).
- Stock corpus: skip (không có corpus C/C++ phù hợp trong stock path này).
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

ENGINE_INTERNAL_PROPS = MASKED_PROPS | {"_start_id", "_end_id"}

TESTDATA = REPO / "tests" / "fixtures" / "cplus-analyzer"
PY_BIN = REPO / ".venv" / "bin" / "python"
PY_ANALYZER = REPO / "code-tiny" / "tools" / "cplus" / "cplus_analyzer.py"
RUST_BIN = REPO / "rust" / "target" / "release" / "analyzer-cplus"
REPORT_PATH = (
    REPO / "plans" / "260913-2130-rust-full-migration" / "reports"
    / "phase07-cplus-parity.md"
)

SCAN_RE = re.compile(
    r"\[SCAN_RESULT\] parser=cplus files=(\d+) functions=(\d+) "
    r"classes=(\d+) resources=(\d+)"
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
        "--project-name", project_id,
        "--repo", f"repo-{project_id}",
        "--graph-provider", "falkordb",
        "--falkordb-uri", f"{host}:{port}",
        "--falkordb-graph", graph,
        "--cache-dir", str(REPO / ".cache" / "p07_cplus_parity_cache"),
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
    # cplus call rows KHÔNG có project_id — writer lấy từ journal metadata.
    # Harness mô phỏng orchestrator (mode shadow để không ghi plane
    # Project/Repository setup của lane required — đã gate ở phase 03).
    scratch = REPO / ".cache" / "p07_cplus_parity_journal"
    scratch.mkdir(parents=True, exist_ok=True)
    configure_journal_env(
        env,
        root=str(root),
        project_id=project_id,
        parser="cplus",
        source_revision=f"parity-{graph}",
        source_snapshot=f"parity-{graph}",
        physical_target=f"falkordb:{host}:{port}/{graph}",
        cache_dir=str(scratch),
        mode=env.get("CORTEX_GRAPH_JOURNAL_MODE", "shared-shadow"),
        generation=f"parity-{graph}",
    )
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1200,
                          env=env)
    if proc.returncode != 0:
        raise RuntimeError(f"py analyzer failed:\n{proc.stdout[-3000:]}\n{proc.stderr[-3000:]}")
    return proc.stdout


def run_rust(root: Path, project_id: str, graph: str, host: str, port: int,
             incremental: tuple[Path, Path] | None = None) -> str:
    cmd = [
        str(RUST_BIN),
        "--root", str(root),
        "--config", "/dev/null",
        "--project-id", project_id,
        "--project-name", project_id,
        "--repo", f"repo-{project_id}",
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
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
    if proc.returncode != 0:
        raise RuntimeError(f"rust analyzer failed:\n{proc.stdout[-3000:]}\n{proc.stderr[-3000:]}")
    return proc.stdout


def scan_result_of(log: str) -> str:
    matches = list(SCAN_RE.finditer(log))
    if not matches:
        raise RuntimeError(f"[SCAN_RESULT] not found:\n{log[-1500:]}")
    return matches[-1].group(0)


def cleanup_counts_of(log: str) -> tuple[int, int]:
    """Sum matched counts của cleanup batches trong log writer (verbose)."""
    file_total = orphan_total = 0
    for match in re.finditer(
        r"\[falkordb\] cplus:incremental_file_cleanup:\S* batch_finished .*matched=(\d+)",
        log,
    ):
        file_total += int(match.group(1))
    for match in re.finditer(
        r"\[falkordb\] cplus:incremental_orphan_cleanup batch_finished .*matched=(\d+)",
        log,
    ):
        orphan_total += int(match.group(1))
    return (file_total, orphan_total)


def clean_graph(driver: FalkorDBDriver, graph: str) -> None:
    import asyncio

    asyncio.run(driver.execute_query("MATCH (n) DETACH DELETE n", {}, graph))


def dump_graph_masked(driver: FalkorDBDriver, graph: str) -> dict:
    dump = dump_graph(driver, graph)

    def strip(props: dict) -> dict:
        return {k: v for k, v in props.items() if k not in ENGINE_INTERNAL_PROPS}

    # Volatile bổ sung cho cplus: ParseRun.id chứa timestamp+uuid (mỗi run
    # random ở CẢ HAI bên — node không re-MERGE được giữa các run) và
    # parse_run_id trong props của UNKNOWN_CALL/POSSIBLE_CALLS.
    nodes = {
        key: strip(props)
        for key, props in dump["nodes"].items()
        if not key.startswith("ParseRun|")
    }
    edges = {}
    for key, props in dump["edges"].items():
        props = {k: v for k, v in props.items() if k != "parse_run_id"}
        edges[key] = props
    return {"nodes": nodes, "edges": edges}


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


def dual(driver: FalkorDBDriver, root: Path, tag: str, host: str, port: int,
         report: list[str],
         incremental: tuple[Path, Path] | None = None,
         clean: bool = True) -> None:
    graph_py = f"p07_cplus_{tag}_py"
    graph_rust = f"p07_cplus_{tag}_rs"
    if clean:
        clean_graph(driver, graph_py)
        clean_graph(driver, graph_rust)
    py_log = run_py(root, "parity_cplus", graph_py, host, port, incremental)
    rust_log = run_rust(root, "parity_cplus", graph_rust, host, port, incremental)
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
    with tempfile.TemporaryDirectory(prefix="p07_cplus_inc_") as tmp:
        workdir = Path(tmp) / "corpus"
        shutil.copytree(TESTDATA, workdir)

        # FULL seed trên corpus copy — incremental run CHẠY TIẾP trên cùng
        # graph (không clean) như sync thật: File node của file không nằm trong
        # selection phải tồn tại sẵn cho INCLUDES endpoint audit.
        dual(driver, workdir, "inc", host, port, report)

        # Mutate: sửa geometry.cpp (thêm free function), thêm probe.cpp mới
        # include geometry.hpp (include-impact phải kéo geometry.hpp dependents
        # ... và file mới vào selection), xoá logger.cpp.
        geometry = workdir / "src" / "alice" / "geometry.cpp"
        geometry.write_text(
            geometry.read_text(encoding="utf-8").replace(
                "int main(int argc, char** argv) {",
                "double doubled_radius(const alice::math::Circle& circle) {\n"
                "    return 2.0 * circle.radius();\n"
                "}\n\n"
                "int main(int argc, char** argv) {",
            ),
            encoding="utf-8",
        )
        (workdir / "src" / "alice" / "probe.cpp").write_text(
            '// probe.cpp — added by the parity scenario.\n'
            '#include "alice/math/geometry.hpp"\n'
            '\n'
            'double probe_area(const alice::math::Circle& circle) {\n'
            '    return circle.area();\n'
            '}\n',
            encoding="utf-8",
        )
        (workdir / "src" / "alice" / "logger.cpp").unlink()

        changed_manifest = Path(tmp) / "changed.json"
        deleted_manifest = Path(tmp) / "deleted.json"
        changed_manifest.write_text(
            json.dumps({"files": [
                "src/alice/geometry.cpp",
                "src/alice/probe.cpp",
            ]}) + "\n",
            encoding="utf-8",
        )
        deleted_manifest.write_text(
            json.dumps({"files": ["src/alice/logger.cpp"]}) + "\n",
            encoding="utf-8",
        )
        dual(driver, workdir, "inc", host, port, report,
             incremental=(changed_manifest, deleted_manifest), clean=False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6379)
    parser.add_argument("--rust-bin", default=str(RUST_BIN))
    args = parser.parse_args()

    rust_bin = Path(args.rust_bin)

    report = [
        "# Phase 07 — C/C++ (cplus) analyzer parity (python vs rust)",
        "",
        f"- chạy: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- testdata: `{TESTDATA.relative_to(REPO)}` (include/alice + src/alice + res)",
        f"- rust bin: `{rust_bin}`",
        "- grammar pin: tree-sitter-c **0.24.2** + tree-sitter-cpp **0.23.4** "
        "(Rust) == tree-sitter-c 0.24.2 + tree-sitter-cpp 0.23.4 (PyPI venv; "
        "`tree_sitter_languages.get_parser` raise TypeError với tree_sitter "
        "0.26 nên Python dùng fallback binding trực tiếp — verify bằng import)",
        "- mode: tree-sitter fallback (không compile_commands.json, không "
        "bootstrap clang) trên CẢ HAI bên",
        "- stock corpus: **skip**",
        f"- mask: `{sorted(MASKED_PROPS)}`",
    ]
    driver = FalkorDBDriver(host=args.host, port=args.port)

    dual(driver, TESTDATA, "testdata_full", args.host, args.port, report)
    scenario_incremental(driver, args.host, args.port, report)

    report.append(
        f"\n## Kết luận\n\n- FAILURES: {FAILURES if FAILURES else 'không có — PASS toàn bộ'}\n"
    )
    report.append(r"""
## Ghi chú parity (phase 07)

- **Grammar pin**: `tree-sitter = "0.25"` + `tree-sitter-c = "=0.24.2"` +
  `tree-sitter-cpp = "=0.23.4"`. Venv Python: tree-sitter 0.26.0 +
  tree-sitter-c 0.24.2 + tree-sitter-cpp 0.23.4;
  `tree_sitter_languages.get_parser("cpp"/"c")` raise
  `TypeError __init__() takes exactly 1 argument (2 given)` →
  `cplus_analyzer.py` rơi vào fallback import `tree_sitter_cpp`/`tree_sitter_c`
  trực tiếp. Cùng grammar version ⇒ parse tree byte-identical (verify golden
  test `tests/golden_parse.rs` khớp probe Python).
- **Fallback mode**: harness KHÔNG bootstrap clang — `--parse-quality` default
  `report` tự set `disable_compile_db_bootstrap` ở cả 2 bên; fixture không có
  compile_commands.json nên cả hai chạy extension/content heuristics
  (`_is_cpp_file`: sibling check + cpp-hint regex).
- **Journal env (Python reference)**: call rows của cplus_analyzer.py không có
  `project_id` tường minh — `_require_call_project_scope` lấy từ journal
  metadata. Harness `configure_journal_env(..., mode="shared-shadow")` như
  orchestrator; lane production của cplus là `shared-required` (plane
  Project/Repository setup đã gate ở phase-03 writer parity, không lặp lại
  trong diff này). Rust bù `project_id` tường minh vào call/evidence rows.
- **Quirks được tái tạo chính xác** (khớp hành vi Python, có golden test):
  `_walk_tree` copy using-state per-child ⇒ `using_namespaces`/`using_imports`
  của payload luôn rỗng; `_extract_function_name` dùng `_first_identifier`
  (identifier ĐẦU tiên trong declarator, gồm cả phần scope của qualified name
  — out-of-class `int Derived::run(){}` cho name `Derived`); alias name/target
  lấy identifier đầu/từ `typedef` text.
- **Header alternate-grammar retry**: port đủ (`.h` có ERROR → reparse với
  grammar flipped, chọn theo `candidate_is_strictly_better`).
- **Subprocess boundary (key decision #8)**: clang semantic-evidence plane
  (clang_worker.py/libclang 18.1.1, parse_recovery, semantic_worker) GIỮ
  Python. Rust nhận `--parse-quality` (off/report/repair) — `repair` chỉ có
  ý nghĩa khi recovery plane Python chạy; backend Rust không invoke subprocess
  trong parity này (fallback mode + report). `evidence_merge.py` là consumer
  MCP-side (`mcp/cplus/cplus_mcp.py`) — không nằm trong analyzer path, skip.
- **Không port** (documented): `clang_parser.py` (dead code, không ai import),
  `semantic_context/semantic_shadow/semantic_worker` (plane MCP/semantic, không
  ảnh hưởng graph write chuẩn), `pilot_rollout/proc_manifest` (ops tooling).
  `rc_parser.py` + `windows_resource_parser.py` PORT đầy đủ (fixture có
  res/app.rc: DIALOGEX + STRINGTABLE + ICON + UIControl relations).
- **Accepted divergence**: Pro*C plane (.pc/.pcc — proc_analyzer +
  proc_source_map masking + guarded_publication) chưa port; .pc vẫn được scan
  và parse structure bằng grammar C nhưng không sinh proc_nodes/SQL relations.
  Fixture parity không chứa .pc nên gate không bị ảnh hưởng. `--parse-quality
  repair` artifact + parse-quality JSON đầy đủ (parser_version strings của
  Python runtime) — artifact-only fields, không ảnh hưởng graph.
- **Parse cache / resume**: Python-only, accept-and-ignore ở Rust
  (transparent với graph).
- **Mask bổ sung**: `_start_id`/`_end_id` (row-id nội bộ FalkorDB) — strip ở
  harness như phase 06.

## Files

- `rust/crates/analyzer-cplus/` — crate mới (bin `analyzer-cplus`): `cparse.rs`
  (tree-sitter walk + parse_c_family_file), `cscan.rs` (scan + cpp heuristics +
  encodings + include graph), `identity.rs` (function_identity), `quality.rs`
  (parse_quality), `rcparse.rs` (rc_parser), `validate.rs`
  (payload_validation), `analyzer.rs` (pipeline + writer planes), `position.rs`
  (os.path helpers), lib+bin split.
- `tests/fixtures/cplus-analyzer/` — corpus: namespaces lồng, class hierarchy
  + override + pure-virtual, templates, enum, typedef/using, out-of-class
  definitions, free functions, extern "C", function-pointer fields, macros,
  include references chéo file, control-flow calls (if/for/ternary), .rc
  resource script + resource.h.
- `scripts/rust_parity/analyzer_parity_cplus.py` — harness dual-graph
  `p07_cplus_<tag>_py`/`_rs`.
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
