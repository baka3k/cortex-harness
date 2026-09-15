#!/usr/bin/env python3
"""Phase 03 parity gate — dual-write graph diff.

Chạy writer Python (implementation tham chiếu: LanguageCodeWriter +
FalkorDBDriver) trên graph `stock_pw` và writer Rust (cortex-graph-writer,
example `dual_write`) trên graph `stock_rw` với CÙNG fixture rows, sau đó
dump cả 2 graph (nodes + edges + properties) và so exact — khác biệt duy
nhất cho phép: timestamp fields (updated_at/created_at/...) và internal ids.

Usage (từ repo root):
    .venv/bin/python scripts/rust_parity/dual_write_diff.py \
        [--host 127.0.0.1] [--port 6379] \
        [--graph-py stock_pw] [--graph-rust stock_rw] \
        [--spike-bin rust/target/debug/examples/dual_write]

Exit code 0 = PASS (diff rỗng ngoài mask); 1 = FAIL (in diff).
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
import asyncio
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "code-tiny"))
sys.path.insert(0, str(REPO))

from tools.graph.driver.falkordb_driver import FalkorDBDriver  # noqa: E402
from tools.graph.writer.language_writer import LanguageCodeWriter  # noqa: E402

FIXTURE = (
    REPO
    / "rust"
    / "crates"
    / "cortex-graph-writer"
    / "tests"
    / "fixtures"
    / "writer_rows.json"
)
REPORT_PATH = REPO / "plans/260913-2130-rust-full-migration/reports/phase03-dual-write-diff.md"

# Timestamp / volatile fields được mask khi so properties.
MASKED_PROPS = {
    "updated_at",
    "created_at",
    "last_updated",
    "summary_updated_at",
}
# Internal ids do engine cấp — so bằng identity tự nhiên (label + key).
MASKED_PROPS |= {"_graph_id", "_edge_id", "_src", "_dst"}


def rows_of(fixture: dict, key: str) -> list[dict]:
    return fixture.get(key) or []


def clean_graph(driver: FalkorDBDriver, graph: str) -> None:
    asyncio.run(driver.execute_query("MATCH (n) DETACH DELETE n", {}, graph))


def run_python_writer(driver: FalkorDBDriver, graph: str, fixture: dict) -> None:
    """Thứ tự calls GIỐNG HỆT example `dual_write.rs`."""
    writer = LanguageCodeWriter(driver, database=graph, batch_size=1000, verbose=False)

    async def run() -> None:
        await writer.ensure_schema()
        project = fixture.get("project")
        if project:
            await writer.write_projects([project])
        setup = fixture.get("repository_setup")
        if setup:
            await writer.write_project_repository_setup(
                project_id=setup["project_id"],
                project_name=setup["project_name"],
                project_slug=setup["project_slug"],
                repository_name=setup["repository_name"],
            )
        if rows_of(fixture, "packages"):
            await writer.write_packages_full(rows_of(fixture, "packages"))
        if rows_of(fixture, "namespaces"):
            await writer.write_namespaces_full(rows_of(fixture, "namespaces"))
        files = rows_of(fixture, "files")
        if files:
            await writer.write_files(files)
            await writer.write_repo_file_edges(files)
        if rows_of(fixture, "classes"):
            await writer.write_classes_full(rows_of(fixture, "classes"))
        if rows_of(fixture, "types_pass1"):
            await writer.write_types_full(rows_of(fixture, "types_pass1"))
        if rows_of(fixture, "types_pass2"):
            await writer.write_types_full(rows_of(fixture, "types_pass2"))
        if rows_of(fixture, "functions"):
            await writer.write_functions_full(rows_of(fixture, "functions"))
        if rows_of(fixture, "navigators"):
            await writer.write_navigators(rows_of(fixture, "navigators"))
        if rows_of(fixture, "has_routes"):
            await writer.write_has_routes(rows_of(fixture, "has_routes"))
        if rows_of(fixture, "param_lists"):
            await writer.write_param_lists(rows_of(fixture, "param_lists"))
        if rows_of(fixture, "relations"):
            await writer.write_relations_typed(
                rows_of(fixture, "relations"), project_id="stock"
            )
        if rows_of(fixture, "calls"):
            await writer.write_calls(rows_of(fixture, "calls"))
        if rows_of(fixture, "calls_with_site"):
            await writer.write_calls_with_site(rows_of(fixture, "calls_with_site"))
        if rows_of(fixture, "call_evidence_sites"):
            await writer.write_call_evidence_sites(
                rows_of(fixture, "call_evidence_sites")
            )
        if rows_of(fixture, "call_evidence_observations"):
            await writer.write_call_evidence_observations(
                rows_of(fixture, "call_evidence_observations")
            )
        if rows_of(fixture, "build_configurations"):
            await writer.write_build_configurations(
                rows_of(fixture, "build_configurations")
            )
        if rows_of(fixture, "semantic_coverage"):
            await writer.write_semantic_coverage(rows_of(fixture, "semantic_coverage"))
        if rows_of(fixture, "proc_function_joins") or rows_of(
            fixture, "proc_host_declarations"
        ):
            await writer.write_proc_evidence_joins(
                rows_of(fixture, "proc_function_joins"),
                rows_of(fixture, "proc_host_declarations"),
            )
        if rows_of(fixture, "workflows"):
            await writer.write_workflows(rows_of(fixture, "workflows"))
        if rows_of(fixture, "workflow_steps"):
            await writer.write_workflow_steps(rows_of(fixture, "workflow_steps"))
        if fixture.get("topology"):
            from types import SimpleNamespace

            from tools.graph.writer.project_topology_writer import (
                ProjectTopologyWriter,
            )

            class _FactShim:
                """Expose đúng surface ProjectTopologyWriter.read trên fact
                object: ``to_dict()`` + vài attribute identity."""

                def __init__(self, payload: dict, **attrs: object) -> None:
                    self._payload = payload
                    for key, value in attrs.items():
                        setattr(self, key, value)

                def to_dict(self) -> dict:
                    return dict(self._payload)

            topology_payload = fixture["topology"]
            result = SimpleNamespace(
                project_id=topology_payload["project_id"],
                modules=[
                    _FactShim(
                        module,
                        module_path=module["module_path"],
                        id=module["id"],
                        frameworks=list(module.get("frameworks", ())),
                    )
                    for module in topology_payload["modules"]
                ],
                descriptors=[
                    _FactShim(
                        descriptor,
                        module_path=descriptor["module_path"],
                        path=descriptor["path"],
                        id=descriptor["id"],
                    )
                    for descriptor in topology_payload["descriptors"]
                ],
                dependencies=[
                    _FactShim(
                        dependency,
                        source_module_path=dependency["source_module_path"],
                        internal=dependency["internal"],
                        target_module_path=dependency.get("target_module_path"),
                        target=dependency["target"],
                    )
                    for dependency in topology_payload["dependencies"]
                ],
                endpoints=[
                    _FactShim(
                        endpoint,
                        module_id=endpoint["module_id"],
                        id=endpoint["id"],
                        service=endpoint.get("service", ""),
                        framework=endpoint.get("framework", ""),
                        file_path=endpoint.get("file_path", ""),
                    )
                    for endpoint in topology_payload.get("endpoints", [])
                ],
                frameworks=[
                    _FactShim(
                        framework,
                        module_id=framework["module_id"],
                        id=framework["id"],
                    )
                    for framework in topology_payload["frameworks"]
                ],
            )
            topology_writer = ProjectTopologyWriter(driver, database=graph)
            counts = await topology_writer.write(result)
            print(f"[py] topology={counts}")

    asyncio.run(run())


_NODE_IDENTITY_KEYS = (
    "id",
    "site_id",
    "config_fingerprint",
    "fingerprint",
    "project_id",
    "name",
    "workflow_id",
)


def node_identity(props: dict, label: str) -> tuple[str, str]:
    for key in _NODE_IDENTITY_KEYS:
        value = props.get(key)
        if isinstance(value, str) and value:
            return label, value
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return label, str(value)
    return label, ""


def dump_graph(driver: FalkorDBDriver, graph: str) -> dict:
    """Dump nodes + edges về dạng so-sánh được (props đã mask volatile)."""

    async def run() -> tuple[list[dict], list[dict]]:
        node_records, _, _ = await driver.execute_query("MATCH (n) RETURN n", {}, graph)
        edge_records, _, _ = await driver.execute_query(
            "MATCH (a)-[r]->(b) RETURN a, r, b", {}, graph
        )
        return node_records, edge_records

    node_records, edge_records = asyncio.run(run())

    nodes: dict[tuple[str, str], dict] = {}
    for record in node_records:
        node = record.get("n") or {}
        label = str(node.get("_label", ""))
        identity = node_identity(node, label)
        props = {
            key: value
            for key, value in node.items()
            if key not in MASKED_PROPS and key not in _NODE_IDENTITY_KEYS
        }
        nodes[identity] = props

    edges: dict[tuple[str, str, str, str, str], dict] = {}
    duplicates = 0
    for record in edge_records:
        source = record.get("a") or {}
        rel = record.get("r") or {}
        target = record.get("b") or {}
        source_label = str(source.get("_label", ""))
        target_label = str(target.get("_label", ""))
        key = (
            *node_identity(source, source_label),
            str(rel.get("_type", "")),
            *node_identity(target, target_label),
        )
        props = {
            key_: value
            for key_, value in rel.items()
            if key_ not in MASKED_PROPS and key_ != "_type"
        }
        if key in edges and edges[key] != props:
            raise RuntimeError(f"edge key collision với props khác nhau: {key}")
        if key in edges:
            duplicates += 1
            continue
        edges[key] = props

    return {
        "nodes": {f"{label}|{identity}": props for (label, identity), props in nodes.items()},
        "edges": {
            f"{k[0]}|{k[1]} -[{k[2]}]-> {k[3]}|{k[4]}": props
            for k, props in edges.items()
        },
        "_duplicates_skipped": duplicates,
    }


def diff_dump(py_dump: dict, rust_dump: dict) -> dict:
    diff: dict = {"nodes_only_py": {}, "nodes_only_rust": {}, "edges_only_py": {}, "edges_only_rust": {}, "props_differ": {}}
    for key, props in py_dump["nodes"].items():
        if key not in rust_dump["nodes"]:
            diff["nodes_only_py"][key] = props
        elif rust_dump["nodes"][key] != props:
            diff["props_differ"][f"node {key}"] = {
                "py": props,
                "rust": rust_dump["nodes"][key],
            }
    for key, props in rust_dump["nodes"].items():
        if key not in py_dump["nodes"]:
            diff["nodes_only_rust"][key] = props
    for key, props in py_dump["edges"].items():
        if key not in rust_dump["edges"]:
            diff["edges_only_py"][key] = props
        elif rust_dump["edges"][key] != props:
            diff["props_differ"][f"edge {key}"] = {
                "py": props,
                "rust": rust_dump["edges"][key],
            }
    for key, props in rust_dump["edges"].items():
        if key not in py_dump["edges"]:
            diff["edges_only_rust"][key] = props
    return diff


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6379)
    parser.add_argument("--graph-py", default="stock_pw")
    parser.add_argument("--graph-rust", default="stock_rw")
    parser.add_argument(
        "--spike-bin", default=str(REPO / "rust/target/debug/examples/dual_write")
    )
    parser.add_argument("--fixture", default=str(FIXTURE))
    args = parser.parse_args()

    fixture = json.loads(Path(args.fixture).read_text(encoding="utf-8"))

    driver = FalkorDBDriver(host=args.host, port=args.port)

    print(f"[1/4] Python writer → graph {args.graph_py}")
    clean_graph(driver, args.graph_py)
    run_python_writer(driver, args.graph_py, fixture)

    print(f"[2/4] Rust writer → graph {args.graph_rust}")
    clean_graph(driver, args.graph_rust)
    proc = subprocess.run(
        [
            args.spike_bin,
            "--fixture",
            args.fixture,
            "--host",
            args.host,
            "--port",
            str(args.port),
            "--graph",
            args.graph_rust,
        ],
        capture_output=True,
        text=True,
        timeout=900,
    )
    if proc.returncode != 0:
        print("Rust writer FAILED:")
        print(proc.stdout[-4000:])
        print(proc.stderr[-4000:])
        return 1
    print(proc.stdout.strip()[-2000:])

    print("[3/4] Dump + diff")
    py_dump = dump_graph(driver, args.graph_py)
    rust_dump = dump_graph(driver, args.graph_rust)
    diff = diff_dump(py_dump, rust_dump)

    total_diff = sum(len(v) for v in diff.values())
    print(f"[4/4] nodes py={len(py_dump['nodes'])} rust={len(rust_dump['nodes'])}; "
          f"edges py={len(py_dump['edges'])} rust={len(rust_dump['edges'])}; "
          f"diff_total={total_diff}")

    lines = [
        "# Phase 03 — dual-write graph diff",
        "",
        f"- fixture: `{args.fixture}`",
        f"- graph Python: `{args.graph_py}` / graph Rust: `{args.graph_rust}`",
        f"- nodes: py={len(py_dump['nodes'])} rust={len(rust_dump['nodes'])}",
        f"- edges: py={len(py_dump['edges'])} rust={len(rust_dump['edges'])}",
        f"- diff_total (ngoài mask {sorted(MASKED_PROPS)}): **{total_diff}**",
        "",
    ]
    if total_diff:
        lines.append("```json")
        lines.append(json.dumps(diff, ensure_ascii=False, indent=2)[:20000])
        lines.append("```")
        REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"report → {REPORT_PATH}")
        return 1
    lines.append("**PASS** — diff rỗng ngoài mask.")
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"report → {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
