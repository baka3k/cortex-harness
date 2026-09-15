#!/usr/bin/env python3
"""Phase 04 parity gate — project_topology analyzer Python vs Rust.

Dual-run trên cùng một prepared graph seed (foreign facts của analyzer khác:
Class/Function public API, ApiEndpoint, AndroidManifest) — topology Python chạy
trên graph `_py`, topology Rust trên graph `_rs` (FalkorDB 127.0.0.1:6379).

Gate:
1. `[project_topology]` summary line byte-identical (summary artifact + stdout).
2. Graph dump diff rỗng ngoài mask (dual_write_diff) — nodes/rels topology.
3. Incremental leg: re-run sau thay đổi nhỏ (thêm module, xoá descriptor) —
   summary + graph diff khớp.

Full-stock parity (topology chạy cuối sync trên graph đầy đủ sau toàn bộ
parsers) — PENDING, chạy qua `sync_orchestrator_parity.py` opt-in khi merge
(red-team C6 orchestrator leg).

Usage (từ repo root):
    .venv/bin/python scripts/rust_parity/analyzer_parity_topology.py
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
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "code-tiny"))
sys.path.insert(0, str(REPO / "scripts" / "rust_parity"))

import dual_write_diff  # noqa: E402
from dual_write_diff import MASKED_PROPS, diff_dump  # noqa: E402
from tools.graph.driver.falkordb_driver import FalkorDBDriver  # noqa: E402

PY_BIN = REPO / ".venv" / "bin" / "python"
PY_ANALYZER = REPO / "code-tiny" / "tools" / "project_topology" / "topology_analyzer.py"
RUST_BIN = REPO / "rust" / "target" / "release" / "analyzer-topology"
FIXTURE = REPO / "tests" / "fixtures" / "project-topology"
SCRATCH_ROOT = REPO / ".cache" / "p04_topology"
HOST, PORT = "127.0.0.1", 6379
PROJECT_ID = "p04topo"
PROJECT_KEY = PROJECT_ID.lower()  # project_id_lookup_key
REPORTS = REPO / "plans" / "260915-analyzer-layer-rust-cutover" / "reports"

SUMMARY_RE = re.compile(r"\[project_topology\] (\{.*\})")
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)
    return ok


def analyzer_env() -> dict:
    env = dict(os.environ)
    for key in [
        "FALKORDB_URI", "FALKORDB_GRAPH", "FALKORDB_DATABASE", "FALKORDB_PATH",
        "FALKORDB_PASSWORD", "FALKORDB_SSL", "LADYBUG_PATH", "LADYBUG_GRAPH",
        "CORTEX_DISABLE_GRAPH", "CORTEX_GRAPH_PROVIDER", "GRAPH_PROVIDER",
        "CODE_GRAPH_PROVIDER", "PROJECT_ID", "PROJECT_NAME", "NEO4J_URI",
        "NEO4J_USER", "NEO4J_PASS", "NEO4J_DB", "CORTEX_EXTRA_IGNORE_DIRS",
        "ANALYZER_TOPOLOGY_DUMP_RESULT",
    ]:
        env.pop(key, None)
    env["PYTHONPATH"] = str(REPO / "code-tiny")
    return env


async def clean_graph(driver: FalkorDBDriver, graph: str) -> None:
    await driver.execute_query("MATCH (n) DETACH DELETE n", {}, graph)


async def seed_foreign_nodes(driver: FalkorDBDriver, graph: str) -> None:
    """Seed 'foreign' facts (analyzer khác sở hữu) để link queries của writer
    (EXPOSES_API / EXISTING_ENDPOINT_LINK / ANDROID_FACT_LINK) có đối tượng."""
    seeds = [
        # symbol public API trong module app (file_path dưới app/)
        (
            "MERGE (s:Class {id: $id}) "
            "SET s.project_id_normalized = $key, s.is_public_api = true, "
            "    s.file_path = $path, s.name = $name, s.language = 'kotlin'",
            {"id": "sym:Bank", "path": "app/src/Bank.kt", "name": "Bank"},
        ),
        # symbol nằm sâu hơn trong app (substring prefix rule)
        (
            "MERGE (s:Function {id: $id}) "
            "SET s.project_id_normalized = $key, s.is_public_api = true, "
            "    s.file_path = $path, s.name = $name, s.language = 'kotlin'",
            {"id": "sym:transfer", "path": "app/src/feature/Transfer.kt", "name": "transfer"},
        ),
        # endpoint của analyzer khác (HTTP) trong api/
        (
            "MERGE (e:HttpEndpoint {id: $id}) "
            "SET e.project_id_normalized = $key, e.file_path = $path, "
            "    e.method = 'GET', e.path = '/legacy'",
            {"id": "ep:legacy", "path": "api/legacy.http"},
        ),
        # android manifest fact (khớp nhánh project_id thường, không normalized)
        (
            "MERGE (m:AndroidManifest {id: $id}) "
            "SET m.project_id = $pid, m.file_path = $path, m.package = 'com.legacy'",
            {"id": "mf:legacy", "pid": PROJECT_ID, "path": "app/src/main/AndroidManifest.xml"},
        ),
        # android resource fact trong library/
        (
            "MERGE (r:AndroidResource {id: $id}) "
            "SET r.project_id_normalized = $key, r.file_path = $path",
            {"id": "res:legacy", "path": "library/src/main/res/values/colors.xml"},
        ),
    ]
    for query, params in seeds:
        params = dict(params, key=PROJECT_KEY)
        await driver.execute_query(query, params, graph)


def run_analyzer(root: Path, graph: str, program: list[str], extra: list[str]) -> str:
    cmd = [
        *program,
        "--root", str(root),
        "--project-id", PROJECT_ID,
        "--project-name", "p04topo-parity",
        "--graph-provider", "falkordb",
        "--falkordb-uri", f"{HOST}:{PORT}",
        "--falkordb-graph", graph,
        "--disable-message-scan",
        *extra,
    ]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, timeout=600, env=analyzer_env(),
        cwd=str(REPO),
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"analyzer failed ({cmd[0]}):\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
        )
    if proc.stderr.strip():
        print(f"[warn] {cmd[0]} stderr:\n{proc.stderr[-2000:]}")
    return proc.stdout


def py_program() -> list[str]:
    return [str(PY_BIN), str(PY_ANALYZER)]


def rust_program() -> list[str]:
    return [str(RUST_BIN)]


async def dump_graph_masked(driver: FalkorDBDriver, graph: str) -> dict:
    """Mirror `dual_write_diff.dump_graph` (mask volatile + engine ids) nhưng
    async-native để dùng chung event loop."""
    node_records, _, _ = await driver.execute_query("MATCH (n) RETURN n", {}, graph)
    edge_records, _, _ = await driver.execute_query(
        "MATCH (a)-[r]->(b) RETURN a, r, b", {}, graph
    )
    keep = MASKED_PROPS | {"_start_id", "_end_id"}

    def strip(props: dict, extra: set[str] = frozenset()) -> dict:
        return {k: v for k, v in props.items() if k not in keep and k not in extra}

    nodes: dict[tuple[str, str], dict] = {}
    for record in node_records:
        node = record.get("n") or {}
        label = str(node.get("_label", ""))
        identity = dual_write_diff.node_identity(node, label)
        nodes[(label, identity)] = strip(node, dual_write_diff._NODE_IDENTITY_KEYS)

    edges: dict[tuple, dict] = {}
    for record in edge_records:
        source = record.get("a") or {}
        rel = record.get("r") or {}
        target = record.get("b") or {}
        key = (
            *dual_write_diff.node_identity(source, str(source.get("_label", ""))),
            str(rel.get("_type", "")),
            *dual_write_diff.node_identity(target, str(target.get("_label", ""))),
        )
        edges[key] = strip(rel, {"_type"})

    return {
        "nodes": {f"{label}|{identity}": props for (label, identity), props in nodes.items()},
        "edges": {
            f"{k[0]}|{k[1]} -[{k[2]}]-> {k[3]}|{k[4]}": props
            for k, props in edges.items()
        },
    }


def compare_summary_lines(log_py: str, log_rs: str, label: str, report: list[str]) -> None:
    match_py = SUMMARY_RE.findall(log_py)
    match_rs = SUMMARY_RE.findall(log_rs)
    ok = bool(match_py) and bool(match_rs) and match_py[-1] == match_rs[-1]
    check(f"{label}: [project_topology] summary byte-identical", ok)
    report.append(f"- {label} summary: {'PASS' if ok else 'FAIL'}\n")
    if not ok and match_py and match_rs:
        try:
            py_obj = json.loads(match_py[-1])
            rs_obj = json.loads(match_rs[-1])
            report.append(
                "```json\n"
                + json.dumps({"py": py_obj, "rs": rs_obj}, indent=2, sort_keys=True)[:8000]
                + "\n```\n"
            )
        except json.JSONDecodeError:
            report.append(f"- py: `{match_py[-1][:400]}`\n- rs: `{match_rs[-1][:400]}`\n")
    if ok and match_py:
        payload = json.loads(match_py[-1])
        report.append(
            f"  - counts: modules={payload['modules']} descriptors={payload['descriptors']} "
            f"dependencies={payload['dependencies']} endpoints={payload['endpoints']} "
            f"frameworks={payload['frameworks']} diagnostics={len(payload['diagnostics'])}\n"
        )


async def compare_graphs(driver: FalkorDBDriver, graph_py: str, graph_rs: str, label: str,
                         report: list[str]) -> None:
    py_dump = await dump_graph_masked(driver, graph_py)
    rs_dump = await dump_graph_masked(driver, graph_rs)
    diff = diff_dump(py_dump, rs_dump)
    diff_total = sum(len(v) for k, v in diff.items() if not k.startswith("_"))
    check(
        f"{label}: graph diff rỗng ngoài mask",
        diff_total == 0,
        f"nodes py={len(py_dump['nodes'])} rs={len(rs_dump['nodes'])} diff={diff_total}",
    )
    report.append(
        f"- {label}: nodes py={len(py_dump['nodes'])} rs={len(rs_dump['nodes'])}, "
        f"edges py={len(py_dump['edges'])} rs={len(rs_dump['edges'])}, "
        f"diff_total=**{diff_total}**\n"
    )
    if diff_total:
        report.append("```json\n")
        report.append(json.dumps(diff, indent=2, ensure_ascii=True, default=str)[:16000])
        report.append("\n```\n")
    # topology-owned assertions
    topology_nodes = [n for n in py_dump["nodes"].values() if n.get("topology_owned")]
    report.append(f"  - topology-owned nodes (py): {len(topology_nodes)}\n")


async def dual_leg(driver: FalkorDBDriver, scratch: Path, tag: str, report: list[str],
                   incremental: bool = False) -> None:
    graph_py = f"p04topo_{tag}_py"
    graph_rs = f"p04topo_{tag}_rs"
    for graph in (graph_py, graph_rs):
        await clean_graph(driver, graph)
        await seed_foreign_nodes(driver, graph)

    extra = []
    if incremental:
        changed_manifest = scratch / "changed.json"
        deleted_manifest = scratch / "deleted.txt"
        changed_manifest.write_text(
            json.dumps({"files": ["feature2/build.gradle", "app/build.gradle.kts"]}) + "\n"
        )
        deleted_manifest.write_text("library/build.gradle\n")
        extra = [
            "--incremental",
            "--changed-files-manifest", str(changed_manifest),
            "--deleted-files-manifest", str(deleted_manifest),
        ]

    log_py = run_analyzer(scratch, graph_py, py_program(), extra)
    log_rs = run_analyzer(scratch, graph_rs, rust_program(), extra)
    compare_summary_lines(log_py, log_rs, f"leg {tag}", report)
    await compare_graphs(driver, graph_py, graph_rs, f"leg {tag}", report)


def prepare_scratch() -> Path:
    if SCRATCH_ROOT.exists():
        shutil.rmtree(SCRATCH_ROOT)
    scratch = SCRATCH_ROOT / f"run-{time.strftime('%Y%m%d-%H%M%S')}"
    scratch.mkdir(parents=True)
    shutil.copytree(FIXTURE, scratch / "repo")
    return scratch / "repo"


def apply_incremental_change(repo: Path) -> None:
    feature2 = repo / "feature2"
    feature2.mkdir()
    (feature2 / "build.gradle").write_text(
        "plugins { id 'java-library' }\n"
        "dependencies { implementation project(':app') }\n"
        "dependencies { implementation 'com.squareup.okhttp3:okhttp:4.12.0' }\n"
    )
    (repo / "library").mkdir(exist_ok=True)
    (repo / "library" / "build.gradle").unlink()


async def main_async(args: argparse.Namespace) -> int:
    driver = FalkorDBDriver(host=HOST, port=PORT)
    report: list[str] = []
    header = [
        "# Phase 04 — analyzer-topology parity (python vs rust)",
        "",
        f"- chạy: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- fixture: `{FIXTURE.relative_to(REPO)}` (copy tại `.cache/p04_topology/`)",
        f"- rust bin: `{RUST_BIN}`",
        f"- falkordb: {HOST}:{PORT}, graphs `p04topo_*_py` / `p04topo_*_rs`",
        f"- seed: foreign facts (Class/Function public API, HttpEndpoint, "
        f"AndroidManifest, AndroidResource) giống hệt vào cả 2 graph",
        f"- mask: `{sorted(MASKED_PROPS)}` + `_start_id`/`_end_id`",
        "",
    ]

    repo = prepare_scratch()
    await dual_leg(driver, repo, "full", report)
    if not args.skip_incremental:
        apply_incremental_change(repo)
        await dual_leg(driver, repo, "incr", report, incremental=True)

    # summary artifacts (byte-identical file check)
    print()
    check(f"failures: {len(FAILURES)}", not FAILURES)
    report_text = "".join(header + report) + (
        f"\n## Kết luận\n\n- FAILURES: {FAILURES if FAILURES else 'none — PASS'}\n"
    )
    if args.report:
        args.report.write_text(report_text)
        print(f"report → {args.report}")
    return 0 if not FAILURES else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-incremental", action="store_true")
    parser.add_argument(
        "--report", type=Path, default=REPORTS / "phase04-topology-parity-dualrun.md"
    )
    args = parser.parse_args()
    if not RUST_BIN.exists():
        print(f"rust binary missing: {RUST_BIN}", file=sys.stderr)
        print("build with: cargo build --release -p analyzer-topology", file=sys.stderr)
        return 2
    import asyncio

    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
