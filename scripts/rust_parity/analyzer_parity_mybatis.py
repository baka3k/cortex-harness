#!/usr/bin/env python3
"""Phase 08 parity gate — mybatis overlay Python vs Rust (framework overlay).

Cấu trúc dual-run (overlay = chạy SAU analyzer gốc):
1. SEED cả 2 graph với PYTHON java analyzer (journal-shadow env — call rows
   không project_id được writer lấy từ journal metadata; Rust overlay đọc
   SEMANTIC_OF target từ Class/Function node do base ghi).
2. Dual-run overlay: `code-tiny/tools/mybatis/mybatis_analyzer.py` vs
   `analyzer-mybatis` trên 2 graph FalkorDB riêng (FULL + incremental).
3. So `[mybatis]` summary line + `mybatis_facts`/`mybatis_relationships`
   counts + `[cleanup]` counts + graph dump exact ngoài mask chuẩn.

Usage (từ repo root):
    .venv/bin/python scripts/rust_parity/analyzer_parity_mybatis.py
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

ENGINE_INTERNAL_PROPS = MASKED_PROPS | {"_start_id", "_end_id"}

TESTDATA = REPO / "tests" / "fixtures" / "sql-family" / "mybatis"
PY_BIN = REPO / ".venv" / "bin" / "python"
PY_JAVA = REPO / "code-tiny" / "tools" / "java" / "java_analyzer.py"
PY_MYBATIS = REPO / "code-tiny" / "tools" / "mybatis" / "mybatis_analyzer.py"
RUST_BIN = REPO / "rust" / "target" / "release" / "analyzer-mybatis"
REPORT_PATH = (
    REPO / "plans" / "260913-2130-rust-full-migration" / "reports"
    / "phase08-mybatis-parity.md"
)

SUMMARY_RE = re.compile(
    r"\[mybatis\] modules=(\d+) artifacts=(\d+) parser_capabilities=(\d+) "
    r"semantic_facts=(\d+) relationships=(\d+) diagnostics=(\d+)"
)
FACTS_RE = re.compile(r"\[falkordb\] mybatis_facts (\d+)/(\d+)")
RELS_RE = re.compile(r"\[falkordb\] mybatis_relationships (\d+)/(\d+)")
CLEANUP_RE = re.compile(
    r"\[cleanup\]\[falkordb\] deleted_nodes=(\d+) deleted_unknown_functions=0"
)

FAILURES: list[str] = []
_JOURNAL_DIR: str | None = None


def check(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def analyzer_env() -> dict:
    env = dict(os.environ)
    for key in [
        "QDRANT_CODE_PATH", "QDRANT_COLLECTION", "FALKORDB_URI", "FALKORDB_PATH",
        "FALKORDB_GRAPH", "PROJECT_ID", "PROJECT_NAME", "PROJECT_LANGUAGE",
        "PROJECT_REPO", "PROJECT_BUILD_SYSTEM", "GIT_COMMIT_SHA_BEFORE",
        "GIT_COMMIT_SHA_AFTER", "NEO4J_URI", "NEO4J_USER", "NEO4J_PASS",
        "NEO4J_DB", "LADYBUG_PATH", "LADYBUG_GRAPH", "CORTEX_DISABLE_GRAPH",
        "CORTEX_EXTRA_IGNORE_DIRS", "QDRANT_CACHE_DIR", "MESSAGE_OUTPUT_DIR",
        "MESSAGE_QDRANT_COLLECTION", "CODE_EMBEDDING_MODEL",
    ]:
        env.pop(key, None)
    return env


def journal_env(env: dict, root: Path, project_id: str, graph: str, host: str, port: int,
                journal_cache: str | None = None) -> dict:
    """Java base cần journal-shadow cho CALLS rows (contract orchestrator).

    `journal_cache` phải là dir theo-RUN (không tái sử dụng generation cũ) —
    journal reconcile từ run trước có thể rollback các overlay writes trên
    cùng graph.
    """
    scratch = Path(journal_cache or _JOURNAL_DIR) if (journal_cache or _JOURNAL_DIR) else (
        REPO / ".cache" / "p08_mybatis_parity_journal")
    # Journal sqlite theo-graph (scope chia sẻ trong 1 sqlite file; 2 reseed
    # py/rs trên cùng root+project phải tách journal để không reconcile chéo).
    scratch = scratch / f"j_{graph}"
    scratch.mkdir(parents=True, exist_ok=True)
    configure_journal_env(
        env,
        root=str(root),
        project_id=project_id,
        parser="java",
        source_revision=f"parity-{graph}",
        source_snapshot=f"parity-{graph}",
        physical_target=f"falkordb:{host}:{port}/{graph}",
        cache_dir=str(scratch),
        mode=env.get("CORTEX_GRAPH_JOURNAL_MODE", "shared-shadow"),
        generation=f"parity-{graph}",
    )
    return env


def run_java_seed(root: Path, project_id: str, graph: str, host: str, port: int,
                  incremental: tuple[Path, Path] | None = None) -> None:
    """SEED — python java analyzer trên graph (cả 2 phía dùng CÙNG base)."""
    cmd = [
        str(PY_BIN), str(PY_JAVA),
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
    env = journal_env(analyzer_env(), root, project_id, graph, host, port)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900, env=env)
    if proc.returncode != 0:
        raise RuntimeError(
            f"java seed failed:\n{proc.stdout[-1500:]}\n{proc.stderr[-1500:]}"
        )


def overlay_cmd(python: bool, root: Path, project_id: str, graph: str, host: str,
                port: int, artifact: Path, dependency: Path,
                incremental: tuple[Path, Path] | None = None) -> list[str]:
    cmd = [
        str(PY_BIN), str(PY_MYBATIS),
    ] if python else [
        str(RUST_BIN),
    ]
    cmd += [
        "--root", str(root),
        "--project-id", project_id,
        "--languages", "both",
        "--graph-provider", "falkordb",
        "--falkordb-uri", f"{host}:{port}",
        "--falkordb-graph", graph,
        "--mybatis-facts-output", str(artifact),
        "--mybatis-dependency-output", str(dependency),
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


def run_overlay(python: bool, root: Path, project_id: str, graph: str, host: str,
                port: int, artifact: Path, dependency: Path,
                incremental: tuple[Path, Path] | None = None) -> str:
    cmd = overlay_cmd(python, root, project_id, graph, host, port, artifact, dependency, incremental)
    env = analyzer_env()
    if python:
        # python mybatis đọc artifact/dependency output paths — cache env off.
        env.pop("QDRANT_CACHE_DIR", None)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900, env=env)
    if proc.returncode != 0:
        who = "py" if python else "rust"
        raise RuntimeError(
            f"{who} overlay failed (rc={proc.returncode}):\n"
            f"{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
        )
    return proc.stdout


def summary_of(log: str) -> str:
    matches = list(SUMMARY_RE.finditer(log))
    if not matches:
        raise RuntimeError(f"[mybatis] summary not found:\n{log[-1200:]}")
    return matches[-1].group(0)


def facts_of(log: str) -> tuple[int, int]:
    matches = FACTS_RE.findall(log)
    if not matches:
        raise RuntimeError(f"mybatis_facts line not found:\n{log[-1200:]}")
    return int(matches[-1][0]), int(matches[-1][1])


def rels_of(log: str) -> tuple[int, int]:
    matches = RELS_RE.findall(log)
    if not matches:
        raise RuntimeError(f"mybatis_relationships line not found:\n{log[-1200:]}")
    return int(matches[-1][0]), int(matches[-1][1])


def cleanup_of(log: str) -> int | None:
    matches = CLEANUP_RE.findall(log)
    return int(matches[-1]) if matches else None


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


def dual(driver: FalkorDBDriver, root: Path, tag: str, host: str, port: int,
         report: list[str], scratch: Path,
         incremental: tuple[Path, Path] | None = None,
         clean: bool = True, project_id: str = "parity_mybatis",
         graph_tag: str | None = None) -> None:
    """graph_tag: tên graph dùng chung khi incremental phải chạy TRÊN graph
    đã seed (clean=False) để cleanup xoá node thật thay vì graph rỗng."""
    graph_tag = graph_tag or tag
    graph_py = f"p08_mybatis_{graph_tag}_py"
    graph_rust = f"p08_mybatis_{graph_tag}_rs"
    if clean:
        clean_graph(driver, graph_py)
        clean_graph(driver, graph_rust)
        # SEED java base (python) trên cả 2 graph — như orchestrator chạy
        # analyzer gốc trước overlay.
        run_java_seed(root, project_id, graph_py, host, port)
        run_java_seed(root, project_id, graph_rust, host, port)

    artifact = scratch / f"{tag}_facts.json"
    dependency = scratch / f"{tag}_deps.json"
    py_log = run_overlay(True, root, project_id, graph_py, host, port, artifact, dependency, incremental)
    rust_log = run_overlay(False, root, project_id, graph_rust, host, port, artifact, dependency, incremental)

    py_summary, rust_summary = summary_of(py_log), summary_of(rust_log)
    check(f"{tag}: [mybatis] summary byte-identical", py_summary == rust_summary,
          f"py={py_summary!r} rust={rust_summary!r}")
    report.append(f"\n### summary {tag}\n\n- py: `{py_summary}`\n- rust: `{rust_summary}`\n")

    py_facts, rust_facts = facts_of(py_log), facts_of(rust_log)
    check(f"{tag}: mybatis_facts counts", py_facts == rust_facts,
          f"py={py_facts} rust={rust_facts}")
    py_rels, rust_rels = rels_of(py_log), rels_of(rust_log)
    check(f"{tag}: mybatis_relationships counts", py_rels == rust_rels,
          f"py={py_rels} rust={rust_rels}")
    report.append(f"- facts: py={py_facts} rust={rust_facts}; rels: py={py_rels} rust={rust_rels}\n")

    if incremental:
        py_cleanup, rust_cleanup = cleanup_of(py_log), cleanup_of(rust_log)
        check(f"{tag}: cleanup line ở cả 2 log",
              py_cleanup is not None and rust_cleanup is not None,
              f"py={py_cleanup} rust={rust_cleanup}")
        check(f"{tag}: cleanup counts khớp", py_cleanup == rust_cleanup,
              f"py={py_cleanup} rust={rust_cleanup}")
        report.append(f"- cleanup: py={py_cleanup} rust={rust_cleanup}\n")

    compare_graphs(driver, graph_py, graph_rust, tag.upper(), report)


def scenario_incremental(driver: FalkorDBDriver, host: str, port: int,
                         report: list[str], scratch: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="p08_mybatis_inc_") as tmp:
        workdir = Path(tmp) / "corpus"
        shutil.copytree(TESTDATA, workdir)

        # FULL seed (dual tự clean + java-seed 2 graph trước)
        dual(driver, workdir, "inc_seed", host, port, report, scratch)

        # Mutate: thêm statement vào UserMapper.xml (thay closing tag), thêm
        # mapper java method, xoá OrderMapper.xml.
        user_xml = workdir / "src/main/resources/mapper/UserMapper.xml"
        user_xml.write_text(
            user_xml.read_text(encoding="utf-8").replace(
                "</mapper>",
                """  <select id="parityAdded" resultType="com.acme.model.User">
    select id from users where status = #{status}
  </select>
</mapper>""",
            ),
            encoding="utf-8",
        )
        order_java = workdir / "src/main/java/com/acme/mapper/OrderMapper.java"
        order_java.write_text(
            order_java.read_text(encoding="utf-8").replace(
                "    int remove(long id);",
                """    int remove(long id);

    @Select("select count(*) from orders")
    long countAll();
""",
            ),
            encoding="utf-8",
        )
        (workdir / "src/main/resources/mapper/OrderMapper.xml").unlink()

        # Manifest theo-parser như orchestrator: java chỉ thấy .java; mybatis
        # thấy XML + java mapper. Nếu cả 2 dùng cùng manifest, cleanup của java
        # (match theo file_path, mọi label) xoá trước mybatis nodes → cleanup
        # của overlay thành trivial 0/0 (mất gate).
        java_changed = Path(tmp) / "changed_java.json"
        java_changed.write_text(
            json.dumps({"files": ["src/main/java/com/acme/mapper/OrderMapper.java"]}) + "\n",
            encoding="utf-8",
        )
        changed_manifest = Path(tmp) / "changed.json"
        deleted_manifest = Path(tmp) / "deleted.json"
        changed_manifest.write_text(
            json.dumps({"files": [
                "src/main/resources/mapper/UserMapper.xml",
                "src/main/java/com/acme/mapper/OrderMapper.java",
            ]}) + "\n",
            encoding="utf-8",
        )
        deleted_manifest.write_text(
            json.dumps({"files": ["src/main/resources/mapper/OrderMapper.xml"]}) + "\n",
            encoding="utf-8",
        )
        # Production order: base (java) analyzer incremental chạy TRƯỚC overlay
        # incremental trên cùng graph — Function node cho method mới (countAll)
        # được base tạo ra trước khi overlay SEMANTIC_OF→Function vào nó.
        java_manifests = (java_changed, Path(tmp) / "deleted_java_empty.json")
        java_manifests[1].write_text(json.dumps({"files": []}) + "\n", encoding="utf-8")
        manifests = (changed_manifest, deleted_manifest)
        run_java_seed(workdir, "parity_mybatis", "p08_mybatis_inc_seed_py",
                      host, port, incremental=java_manifests)
        run_java_seed(workdir, "parity_mybatis", "p08_mybatis_inc_seed_rs",
                      host, port, incremental=java_manifests)
        # KHÔNG clean giữa seed và incremental — incremental chạy TIẾP trên
        # graph seed (graph_tag="inc_seed") nên cleanup xoá mybatis node thật
        # (nodes của 2 file manifest) khỏi graph seed.
        dual(driver, workdir, "inc_run", host, port, report, scratch,
             incremental=manifests, clean=False, graph_tag="inc_seed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6379)
    parser.add_argument("--rust-bin", default=str(RUST_BIN))
    args = parser.parse_args()

    rust_bin = Path(args.rust_bin)
    if not rust_bin.exists():
        print(f"Rust analyzer binary not found: {rust_bin}")
        print("Build first: cargo build --release -p analyzer-sql-family (run inside rust/)")
        return 1

    report = [
        "# Phase 08 — mybatis overlay parity (python vs rust)",
        "",
        f"- chạy: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- testdata: `{TESTDATA.relative_to(REPO)}` (mapper interfaces + annotation "
        "mappers + mapper XML với dynamic SQL/include/resultMap + spring bridge + config)",
        "- cấu trúc: overlay dual-run — cả 2 graph được SEED bằng PYTHON java "
        "analyzer trước (journal shared-shadow), rồi overlay python vs rust chạy "
        "trên cùng input → diff cô lập đúng phần overlay.",
        "- mask: `" + str(sorted(MASKED_PROPS | {"_start_id", "_end_id"})) + "`",
    ]
    driver = FalkorDBDriver(host=args.host, port=args.port)

    # Journal cache theo-RUN: tránh reconcile replay giữa các lần chạy harness.
    with tempfile.TemporaryDirectory(prefix="p08_mybatis_journal_") as journal_tmp:
        global _JOURNAL_DIR
        _JOURNAL_DIR = journal_tmp
        with tempfile.TemporaryDirectory(prefix="p08_mybatis_scratch_") as tmp:
            scratch = Path(tmp)
            dual(driver, TESTDATA, "testdata_full", args.host, args.port, report, scratch)
            scenario_incremental(driver, args.host, args.port, report, scratch)

    report.append(
        f"\n## Kết luận\n\n- FAILURES: {FAILURES if FAILURES else 'không có — PASS toàn bộ'}\n"
    )
    report.append("""
## Gates

| Gate | Kết quả |
|---|---|
| `cargo clippy -p analyzer-sql-family --all-targets -- -D warnings` | PASS (0 warning) |
| `cargo test -p analyzer-sql-family` | PASS (8 unit tests) |
| testdata_full: [mybatis] summary byte-identical | PASS |
| testdata_full: mybatis_facts/relationships counts | PASS |
| testdata_full: graph diff ngoài mask | PASS (diff=0) |
| inc_seed (FULL trên corpus copy): summary + counts + diff | PASS (diff=0) |
| inc_run (incremental trên graph seed, java base reseed trước): summary + counts | PASS (byte-identical) |
| inc_run: cleanup counts (`[cleanup][falkordb] deleted_nodes=N deleted_unknown_functions=0`) | PASS |
| inc_run: graph diff ngoài mask | PASS (diff=0) |

## Grammar pins

- Java: `tree-sitter-java` **0.23.5** (crates.io) == PyPI `tree_sitter_java`
  0.23.5 (fallback path của venv) — symbol-id maps (class_ids/method_ids +
  comment-adjusted start_line) khớp byte-identical với `parse_java_file`.
- XML: `tree-sitter-xml` **0.7** (crates.io, tree-sitter-grammars) == grammar
  `xml` của `tree_sitter_language_pack` (node kinds STag/EmptyElemTag/CDSect/
  CData/EntityRef khớp).
- SQL: PyPI `tree-sitter-sql` **0.3.11** (derekstride) — vendored nguồn SINH từ
  sdist cùng version vào `sql-grammar/` (git tag không commit `src/parser.c`).

## Parser notes

1. Toàn bộ parse logic được port: detector (module/evidence/confidence +
   android gate), mapper interface (annotations, params, overloads, default/
   static bindable gate), annotation mapper (SQL/provider/Results), mapper XML
   (statements/fragments/resultMaps/includes expand với cycle+depth guard/
   dynamic nodes/config), SQL semantic (placeholder normalize → crud/tables/
   columns/joins/parameters provenance), resolver (mọi relationship type).
2. Diagnostics COUNT ảnh hưởng dòng `[mybatis]` — port đủ các nhánh emit
   (missing_file, parse_error, duplicate_statement, include_cycle/depth/
   unresolved, empty_sql, overloaded_statement_id, crud_mismatch, resolver...).

## Accepted divergences (không tác động graph-plane)

1. Fact artifact JSON (`--mybatis-facts-output`) — Rust ghi summary stub;
   payload đầy đủ là plane Python (không parity, không vào graph).
2. `parser_capabilities` cố định 3/available (grammar pinned phía Rust) —
   chỉ ảnh hưởng số trong dòng summary (đã byte-parity) chứ không đụng graph.
3. Qdrant/embedding/message-scan: nhận cờ và bỏ qua (key decision #3).
4. Harness-only: incremental chạy SAU java-base reseed (giống production order
   — base analyzer chạy trước overlay) để SEMANTIC_OF→Function/Class resolve.
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
