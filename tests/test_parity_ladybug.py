"""LadybugDB parity harness (opt-in, marker ``ladybug``).

Ingests one small sample into BOTH providers — falkordblite and ladybug —
using two isolated storage roots, then compares:

* node count per label and relationship count per type via the
  provider-neutral driver APIs (``list_labels`` / ``list_relationship_types``
  plus COUNT queries),
* the output of representative MCP-style queries (by-id lookup, 2-hop
  traverse, CONTAINS search) as normalized JSON.

Run with::

    python -m pytest tests/test_parity_ladybug.py -m ladybug

Skips automatically when either embedded backend is not installed
(ladybug wheels: macOS 15+, manylinux, win_amd64/win_arm64).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

ladybug = pytest.importorskip("ladybug", reason="ladybug package not installed")

from tools.graph.driver.falkordb_driver import FalkorDBDriver  # noqa: E402
from tools.graph.driver.ladybug_driver import LadybugDriver  # noqa: E402

pytestmark = pytest.mark.ladybug

# A tiny representative slice of the code graph: files containing functions
# that call each other, with project scoping properties.
SAMPLE_NODES = [
    {"label": "File", "id": "file:app.py", "path": "src/app.py", "file_path": "src/app.py"},
    {"label": "File", "id": "file:util.py", "path": "src/util.py", "file_path": "src/util.py"},
    {"label": "Function", "id": "fn:app:main", "name": "main", "file_path": "src/app.py", "code": "def main(): run_worker()"},
    {"label": "Function", "id": "fn:app:run_worker", "name": "run_worker", "file_path": "src/app.py", "code": "def run_worker(): helper()"},
    {"label": "Function", "id": "fn:util:helper", "name": "helper", "file_path": "src/util.py", "code": "def helper(): return 42"},
]

SAMPLE_EDGES = [
    {"type": "CONTAINS", "source": "file:app.py", "target": "fn:app:main"},
    {"type": "CONTAINS", "source": "file:app.py", "target": "fn:app:run_worker"},
    {"type": "CONTAINS", "source": "file:util.py", "target": "fn:util:helper"},
    {"type": "CALLS", "source": "fn:app:main", "target": "fn:app:run_worker"},
    {"type": "CALLS", "source": "fn:app:run_worker", "target": "fn:util:helper"},
]


async def _seed(driver) -> None:
    for node in SAMPLE_NODES:
        label = node["label"]
        props = {k: v for k, v in node.items() if k != "label"}
        assignments = ", ".join(f"n.{k} = ${k}" for k in props if k != "id")
        params = dict(props)
        query = f"CREATE (n:{label} {{id: $id}}"
        if assignments:
            query += f") SET {assignments}"
        query += " RETURN count(n) AS count"
        driver.execute_query_sync(query, params)

    for edge in SAMPLE_EDGES:
        # Typed endpoints, matching how analyzers write relationships; the
        # auto-DDL path needs them to resolve the rel-table schema.
        node_labels = {node["id"]: node["label"] for node in SAMPLE_NODES}
        src_label = node_labels[edge["source"]]
        dst_label = node_labels[edge["target"]]
        driver.execute_query_sync(
            f"MATCH (s:{src_label} {{id: $source}}) "
            f"MATCH (t:{dst_label} {{id: $target}}) "
            f"MERGE (s)-[r:{edge['type']}]->(t) RETURN count(r) AS count",
            {"source": edge["source"], "target": edge["target"]},
        )


def _snapshot(driver) -> dict:
    # Node/rel counts: quote identifiers — the manifest label ``Table``
    # collides with a Ladybug reserved keyword when left bare.
    labels = sorted(asyncio.run(driver.list_labels()))
    rel_types = sorted(asyncio.run(driver.list_relationship_types()))
    node_counts = {
        label: driver.execute_query_sync(
            f"MATCH (n:`{label}`) RETURN count(n) AS count"
        )[0][0]["count"]
        for label in labels
    }
    rel_counts = {
        rel: driver.execute_query_sync(
            f"MATCH ()-[r:`{rel}`]->() RETURN count(r) AS count"
        )[0][0]["count"]
        for rel in rel_types
    }
    return {"labels": labels, "rel_types": rel_types, "node_counts": node_counts, "rel_counts": rel_counts}


def _query_outputs(driver) -> dict:
    outputs = {}

    # 1. by-id lookup
    records, _, _ = driver.execute_query_sync(
        "MATCH (n) WHERE n.id = $id RETURN n", {"id": "fn:util:helper"}
    )
    outputs["by_id"] = records[0]["n"] if records else None

    # 2. two-hop traverse from main along CALLS
    records, _, _ = driver.execute_query_sync(
        "MATCH p=(a:Function {id: $start})-[:CALLS*1..2]->(b:Function) "
        "RETURN b.id AS id ORDER BY id",
        {"start": "fn:app:main"},
    )
    outputs["traverse"] = sorted(r["id"] for r in records)

    # 3. portable CONTAINS search
    records, _, _ = driver.execute_query_sync(
        "MATCH (n:Function) WHERE toLower(n.code) CONTAINS toLower($q) "
        "RETURN n.id AS id ORDER BY id",
        {"q": "helper"},
    )
    outputs["contains_search"] = sorted(r["id"] for r in records)

    return outputs


def _expected_traverse() -> list:
    return ["fn:app:run_worker", "fn:util:helper"]


def _fresh_driver(provider_name: str, tmp: Path):
    if provider_name == "falkordb":
        return FalkorDBDriver(path=tmp / "falkordb" / "data.rdb", graph="parity")
    return LadybugDriver(path=tmp / "ladybug" / "parity.lbug", graph="parity")


@pytest.fixture()
def providers(tmp_path):
    pytest.importorskip("redislite", reason="falkordblite (redislite) not installed")
    drivers = {}
    try:
        # One event loop per driver instance: the bounded query lane binds
        # asyncio primitives to the first loop that awaits it, mirroring the
        # one-loop-per-process production model.
        for name in ("falkordb", "ladybug"):
            driver = _fresh_driver(name, tmp_path)
            asyncio.run(_seed(driver))
            drivers[name] = driver
        yield drivers
    finally:
        for driver in drivers.values():
            driver.close()


def test_parity_node_and_rel_counts(providers) -> None:
    falk_snapshot = _snapshot(providers["falkordb"])
    lady_snapshot = _snapshot(providers["ladybug"])
    # The ladybug store carries the bootstrapped manifest labels; restrict
    # the comparison to the labels the sample actually used.
    sample_labels = {node["label"] for node in SAMPLE_NODES}
    sample_rels = {edge["type"] for edge in SAMPLE_EDGES}
    assert {
        label: count
        for label, count in lady_snapshot["node_counts"].items()
        if label in sample_labels
    } == {
        label: count
        for label, count in falk_snapshot["node_counts"].items()
        if label in sample_labels
    }
    assert {
        rel: count for rel, count in lady_snapshot["rel_counts"].items() if rel in sample_rels
    } == {
        rel: count for rel, count in falk_snapshot["rel_counts"].items() if rel in sample_rels
    }


def test_parity_query_outputs(providers) -> None:
    falk_outputs = _query_outputs(providers["falkordb"])
    lady_outputs = _query_outputs(providers["ladybug"])
    assert lady_outputs["by_id"]["id"] == falk_outputs["by_id"]["id"] == "fn:util:helper"
    assert lady_outputs["by_id"]["_label"] == falk_outputs["by_id"]["_label"]
    assert lady_outputs["traverse"] == falk_outputs["traverse"] == _expected_traverse()
    # Both providers must return the same set; ``run_worker`` legitimately
    # matches too because its source calls ``helper()``.
    assert lady_outputs["contains_search"] == falk_outputs["contains_search"] == [
        "fn:app:run_worker",
        "fn:util:helper",
    ]


def test_parity_traverse_depth_two_finds_transitive_call(providers) -> None:
    for driver in providers.values():
        records, _, _ = driver.execute_query_sync(
            "MATCH p=(a:Function {id: $start})-[:CALLS*2..2]->(b:Function) RETURN b.id AS id",
            {"start": "fn:app:main"},
        )
        assert [r["id"] for r in records] == ["fn:util:helper"]
