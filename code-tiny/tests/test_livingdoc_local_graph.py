from __future__ import annotations

import ast
import importlib.util
import os
import sys
from argparse import Namespace
from pathlib import Path

import pytest


CODE_ROOT = Path(__file__).resolve().parents[1]
LIVINGDOC = CODE_ROOT / "livingdoc"
if str(CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT))
if str(LIVINGDOC) not in sys.path:
    sys.path.insert(0, str(LIVINGDOC))

from graph_runtime import QuerySession, open_graph_session, prepare_graph_arguments
from tools.graph.core.base import GraphProvider


def _load_script(name: str):
    path = LIVINGDOC / name
    module_name = "test_" + name.replace("-", "_").replace(".py", "")
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _falkor_args(path: Path, graph: str = "livingdoc_test") -> Namespace:
    return Namespace(
        graph_provider="falkordb",
        falkordb_path=str(path),
        falkordb_graph=graph,
        project_id="project-a",
        neo4j_uri=None,
        neo4j_user=None,
        neo4j_password=None,
        neo4j_db=None,
    )


def test_default_graph_contract_derives_owner_scoped_local_path(tmp_path, monkeypatch):
    monkeypatch.setenv("CORTEX_DATA_HOME", str(tmp_path))
    args = _falkor_args(Path(""))
    args.falkordb_path = None

    prepare_graph_arguments(args)

    assert args.falkordb_path == str(
        tmp_path / "v1" / "instances" / "default" / "falkordb" / "code" / "data.rdb"
    )
    assert args.falkordb_graph == "livingdoc_test"


def test_explicit_neo4j_rollback_requires_credentials():
    args = _falkor_args(Path("unused"))
    args.graph_provider = "neo4j"
    with pytest.raises(ValueError, match="Explicit Neo4j rollback requires"):
        prepare_graph_arguments(args)


def test_supported_scripts_have_no_direct_neo4j_import_or_remote_default():
    names = [
        "living-doc-link.py",
        "living-doc-summarize.py",
        "living-doc-vectorize.py",
        "living-doc-louvain.py",
        "living-doc-summarize-infra.py",
        "living-doc-vectorize-infra.py",
        "living-doc-pipeline.py",
    ]
    for name in names:
        source = (LIVINGDOC / name).read_text(encoding="utf-8")
        tree = ast.parse(source)
        assert "bolt://localhost" not in source
        assert "args.NEO4J_PASS" not in source
        assert not any(
            isinstance(node, (ast.Import, ast.ImportFrom))
            and (
                getattr(node, "module", "") == "neo4j"
                or any(alias.name == "neo4j" for alias in getattr(node, "names", ()))
            )
            for node in ast.walk(tree)
        ), name


def test_pipeline_propagates_local_graph_contract_to_every_phase(tmp_path):
    pipeline = _load_script("living-doc-pipeline.py")
    args = _falkor_args(tmp_path / "code.rdb")
    graph_args = pipeline.graph_cli_args(args)
    assert graph_args == [
        "--graph-provider", "falkordb",
        "--falkordb-path", str(tmp_path / "code.rdb"),
        "--falkordb-graph", "livingdoc_test",
    ]
    source = (LIVINGDOC / "living-doc-pipeline.py").read_text(encoding="utf-8")
    for builder in (
        "build_summarize_cmd",
        "build_vectorize_cmd",
        "build_link_cmd",
        "build_louvain_cmd",
        "build_summarize_infra_cmd",
        "build_vectorize_infra_cmd",
    ):
        function = next(
            node for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef) and node.name == builder
        )
        assert any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "graph_cli_args"
            for node in ast.walk(function)
        ), builder


def test_local_louvain_round_trip_through_falkordb_lite(tmp_path):
    louvain = _load_script("living-doc-louvain.py")
    args = _falkor_args(tmp_path / "code.rdb")

    with open_graph_session(args) as session:
        assert session.provider == GraphProvider.FALKORDB
        session.run(
            """
            CREATE (:Function {id: 'f1', project_id: 'project-a'})
            CREATE (:Function {id: 'f2', project_id: 'project-a'})
            CREATE (:Function {id: 'f3', project_id: 'project-a'})
            CREATE (:Function {id: 'outside', project_id: 'project-b'})
            """
        )
        session.run(
            """
            MATCH (a:Function {id: 'f1'}), (b:Function {id: 'f2'})
            CREATE (a)-[:CALLS]->(b)
            """
        )

        result = louvain.run_local_louvain(
            session, "Function", "CALLS", "UNDIRECTED", "communityId", "project-a"
        )
        assignments = session.run(
            """
            MATCH (f:Function)
            WHERE f.project_id = $project_id
            RETURN f.id AS id, f.communityId AS community_id
            ORDER BY f.id
            """,
            {"project_id": "project-a"},
        )
        materialized = louvain.materialize_infra(
            session,
            "Function",
            "communityId",
            "InfraNode",
            "id",
            2,
            "pending_summary",
            "BELONGS_TO",
            "project-a",
        )
        infra_rows = session.run(
            "MATCH (n:InfraNode) RETURN n.id AS id, n.project_id AS project_id"
        )

    assert result["communityCount"] == 2
    by_id = {row["id"]: row["community_id"] for row in assignments}
    assert by_id["f1"] == by_id["f2"]
    assert by_id["f3"] != by_id["f1"]
    assert materialized["infra_nodes"] == 1
    assert len(infra_rows) == 1
    assert infra_rows[0]["project_id"] == "project-a"
