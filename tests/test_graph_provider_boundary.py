from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))


def _run_probe(source: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(CODE_TINY)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(source)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_falkor_import_path_never_loads_neo4j_boundaries() -> None:
    completed = _run_probe(
        """
        import builtins
        import sys

        original_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            forbidden = {
                "neo4j",
                "tools.graph.core.require_neo4j",
                "tools.graph.driver.neo4j_driver",
            }
            if name in forbidden:
                raise AssertionError(f"Falkor import crossed provider boundary: {name}")
            return original_import(name, *args, **kwargs)

        builtins.__import__ = guarded_import

        from tools.graph import GraphDriverFactory, GraphProvider
        from tools.graph.core import factory
        from tools.graph.driver.falkordb_driver import FalkorDBDriver

        assert GraphProvider.FALKORDB.value == "falkordb"
        assert GraphDriverFactory is factory.GraphDriverFactory
        assert all(base.__name__ != "Neo4jDriver" for base in FalkorDBDriver.__mro__)
        assert "neo4j" not in sys.modules
        assert "tools.graph.core.require_neo4j" not in sys.modules
        assert "tools.graph.driver.neo4j_driver" not in sys.modules
        """
    )
    assert completed.returncode == 0, completed.stderr


def test_factory_import_does_not_load_any_concrete_provider() -> None:
    completed = _run_probe(
        """
        import sys
        from tools.graph.core.factory import GraphDriverFactory

        assert GraphDriverFactory is not None
        assert "tools.graph.driver.falkordb_driver" not in sys.modules
        assert "tools.graph.driver.neo4j_driver" not in sys.modules
        assert "neo4j" not in sys.modules
        """
    )
    assert completed.returncode == 0, completed.stderr


def test_explicit_neo4j_selection_reports_optional_extra() -> None:
    completed = _run_probe(
        """
        import asyncio
        import builtins

        from tools.graph.core.base import GraphProvider
        from tools.graph.core.factory import GraphDriverFactory

        original_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            if name == "neo4j":
                raise ModuleNotFoundError("simulated missing Neo4j extra")
            return original_import(name, *args, **kwargs)

        builtins.__import__ = guarded_import

        async def main():
            try:
                await GraphDriverFactory.create_driver(
                    GraphProvider.NEO4J,
                    {"uri": "bolt://localhost:7687", "user": "neo4j", "password": "pw"},
                )
            except ImportError as exc:
                assert "cortex-harness[neo4j]" in str(exc)
            else:
                raise AssertionError("Neo4j construction unexpectedly succeeded")

        asyncio.run(main())
        """
    )
    assert completed.returncode == 0, completed.stderr


def test_provider_normalization_is_canonical_and_fail_closed() -> None:
    from tools.graph.core.base import GraphProvider
    from tools.graph.core.provider_contract import (
        normalize_graph_provider,
        normalize_graph_provider_name,
    )

    assert normalize_graph_provider_name(None) == "falkordb"
    assert normalize_graph_provider_name("falkor-db") == "falkordb"
    assert normalize_graph_provider("neo") is GraphProvider.NEO4J
    with pytest.raises(ValueError, match="must not be empty"):
        normalize_graph_provider_name("  ")
    with pytest.raises(ValueError, match="Unsupported graph provider"):
        normalize_graph_provider_name("redisgraph")


def test_database_not_found_classifier_has_no_provider_dependency() -> None:
    from tools.graph.core.provider_contract import is_database_not_found_error

    class CodedError(RuntimeError):
        code = "Neo.ClientError.Database.DatabaseNotFound"

    assert is_database_not_found_error(CodedError("opaque"))
    assert is_database_not_found_error(RuntimeError("Database does not exist"))
    assert is_database_not_found_error(RuntimeError("invalid graph reference"))
    assert not is_database_not_found_error(RuntimeError("connection timed out"))


def test_falkor_defaults_ignore_legacy_neo4j_database() -> None:
    from tools.graph.cli import prepare_graph_args
    from tools.graph.journal.config import physical_target_from_env

    args = SimpleNamespace(
        graph_provider="falkordb",
        project_id=None,
        falkordb_graph=None,
        falkordb_uri=None,
        falkordb_path="/tmp/cortex-provider-boundary.rdb",
        neo4j_db="must-not-leak",
    )
    with mock.patch.dict(os.environ, {"CORTEX_DISABLE_GRAPH": ""}):
        assert prepare_graph_args(args)
    assert args.falkordb_graph == "hyper_graph"
    assert args.neo4j_db == "hyper_graph"

    target = physical_target_from_env(
        {
            "CODE_GRAPH_PROVIDER": "falkordb",
            "FALKORDB_PATH": "/tmp/cortex-provider-boundary.rdb",
            "NEO4J_DB": "must-not-leak",
        }
    )
    assert target == "falkordb:/tmp/cortex-provider-boundary.rdb:hyper_graph"


def test_falkor_uses_provider_neutral_cypher_base() -> None:
    from tools.graph.core.cypher_driver import CypherGraphDriver
    from tools.graph.driver.falkordb_driver import FalkorDBDriver
    from tools.graph.driver.neo4j_driver import Neo4jDriver

    assert issubclass(FalkorDBDriver, CypherGraphDriver)
    assert not issubclass(FalkorDBDriver, Neo4jDriver)
    assert FalkorDBDriver.verify_connection is CypherGraphDriver.verify_connection

    driver = object.__new__(FalkorDBDriver)

    async def execute_query(*args, **kwargs):
        return [{"test": 1}], ["test"], None

    driver.execute_query = execute_query
    assert asyncio.run(driver.verify_connection())
