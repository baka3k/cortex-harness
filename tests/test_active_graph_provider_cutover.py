from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import types
from pathlib import Path
from unittest import mock

import pytest


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))


SCRIPT_PATHS = (
    "scripts/ingest_workflows.py",
    "scripts/cleanup_repo_graph.py",
    "scripts/link_project_repos.py",
    "scripts/migrate_repo_file_edges.py",
)


def _load_script(relative_path: str):
    path = CODE_TINY / relative_path
    module_name = "test_cutover_" + path.stem
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_impact_service():
    package = types.ModuleType("cutover_mcp")
    package.__path__ = [str(CODE_TINY / "mcp")]
    services = types.ModuleType("cutover_mcp.services")
    services.__path__ = [str(CODE_TINY / "mcp" / "services")]
    sys.modules.setdefault("cutover_mcp", package)
    sys.modules.setdefault("cutover_mcp.services", services)
    utils = types.ModuleType("cutover_mcp.utils")
    utils.fetch_node_annotations = lambda _database, _node_ids: {}
    graph_service = types.ModuleType("cutover_mcp.services.graph_service")
    graph_service.graph_query_service = object()
    sys.modules.setdefault("cutover_mcp.utils", utils)
    sys.modules.setdefault("cutover_mcp.services.graph_service", graph_service)
    name = "cutover_mcp.services.impact_service"
    if name in sys.modules:
        return sys.modules[name]
    path = CODE_TINY / "mcp" / "services" / "impact_service.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return module


class _FakeDriver:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.closed = False

    def execute_query_sync(self, query, parameters=None, database=None):
        self.calls.append((query, parameters or {}, database))
        records = self.responses.pop(0) if self.responses else []
        return records, list(records[0]) if records else [], None

    def close(self):
        self.closed = True


def test_active_scripts_use_provider_neutral_factory_without_direct_neo4j():
    for relative_path in SCRIPT_PATHS:
        source = (CODE_TINY / relative_path).read_text(encoding="utf-8")
        assert "add_graph_provider_args" in source, relative_path
        assert "create_graph_driver_from_args" in source, relative_path
        assert "from neo4j" not in source, relative_path
        assert "import neo4j" not in source, relative_path
        assert "GraphDatabase.driver" not in source, relative_path


def test_cleanup_defaults_to_local_provider_and_preserves_scope():
    module = _load_script("scripts/cleanup_repo_graph.py")
    driver = _FakeDriver(
        [[{"deleted_nodes": 3}], [{"deleted_unknown": 1}]]
    )

    with mock.patch.object(module, "env_graph_provider", return_value="falkordb"), mock.patch.object(
        module,
        "create_graph_driver_from_args",
        new=mock.AsyncMock(return_value=driver),
    ) as create_driver:
        result = module.cleanup_repo_graph(
            project_id="project-a",
            repo_name="org/repo",
            neo4j_uri="",
            neo4j_user="",
            neo4j_password="",  # sensitive-guard:allow -- local default neo4j credential
            neo4j_db="project-a-graph",
        )

    assert result == (3, 1)
    args = create_driver.await_args.args[0]
    assert args.graph_provider == "falkordb"
    assert driver.calls[0][1] == {
        "project_id": "project-a",
        "repo_name": "org/repo",
    }
    assert driver.calls[0][2] == "project-a-graph"
    assert driver.closed is True


def test_cleanup_neo4j_rollback_mode_requires_credentials():
    module = _load_script("scripts/cleanup_repo_graph.py")
    with pytest.raises(RuntimeError, match="requires URI, user, and password"):
        module.cleanup_repo_graph(
            project_id="project-a",
            repo_name="org/repo",
            neo4j_uri="bolt://localhost:7687",
            neo4j_user="neo4j",
            neo4j_password="",  # sensitive-guard:allow -- local default neo4j credential
            neo4j_db="neo4j",
            graph_provider="neo4j",
        )


def test_impact_scorer_uses_shared_local_driver_and_is_database_scoped():
    module = _load_impact_service()
    analyzer = module.ImpactAnalyzer()
    driver = object()

    env = {
        "GRAPH_PROVIDER": "falkordb",
        "FALKORDB_PATH": "/tmp/cortex-impact-test.rdb",
    }
    with mock.patch.dict(os.environ, env, clear=False), mock.patch(
        "tools.graph.core.shared_runtime.get_shared_graph_driver",
        new=mock.AsyncMock(return_value=driver),
    ) as get_driver:
        first = asyncio.run(analyzer._get_workflow_scorer("project-a"))
        same = asyncio.run(analyzer._get_workflow_scorer("project-a"))
        second = asyncio.run(analyzer._get_workflow_scorer("project-b"))

    assert first is same
    assert first is not second
    assert first._driver is driver
    assert first._database == "project-a"
    assert second._database == "project-b"
    assert get_driver.await_count == 2
    provider, config = get_driver.await_args_list[0].args
    assert provider.value == "falkordb"
    assert config["path"] == "/tmp/cortex-impact-test.rdb"


def test_impact_scorer_neo4j_rollback_mode_fails_closed_without_credentials():
    module = _load_impact_service()
    analyzer = module.ImpactAnalyzer()
    env = {
        "GRAPH_PROVIDER": "neo4j",
        "NEO4J_URI": "bolt://localhost:7687",
        "NEO4J_USER": "neo4j",
        "NEO4J_PASS": "",
        "NEO4J_PASSWORD": "",
    }
    with mock.patch.dict(os.environ, env, clear=False), mock.patch(
        "tools.graph.core.shared_runtime.get_shared_graph_driver",
        new=mock.AsyncMock(),
    ) as get_driver:
        assert asyncio.run(analyzer._get_workflow_scorer("neo4j")) is None
    get_driver.assert_not_awaited()
