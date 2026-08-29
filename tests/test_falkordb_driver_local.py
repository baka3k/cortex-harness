"""Tests for the FalkorDB driver local-mode constructor."""

from __future__ import annotations

import asyncio
import sys
import warnings
from pathlib import Path
from unittest.mock import patch

import pytest

# Make sure code-tiny is on the import path so the relative `tools.graph.*`
# imports resolve. Tests run from the repository root.
ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.graph.driver.falkordb_driver import (  # noqa: E402
    FalkorDBDriver,
    _open_local_falkordb,
)
from tools.graph.schema import GraphSchemaManifest, SchemaIndex  # noqa: E402
from tools.graph.writer.query_contract import (  # noqa: E402
    RelationshipGroup,
    compile_relationship_upsert,
)
from tools.graph.writer.language_writer import LanguageCodeWriter  # noqa: E402


def test_driver_accepts_path_only(tmp_path: Path) -> None:
    """Path-only construction must not require network-style arguments."""
    rdb = tmp_path / "cortex.rdb"
    fake_client = _make_fake_client()
    with patch(
        "tools.graph.driver.falkordb_driver._open_local_falkordb",
        return_value=fake_client,
    ) as open_mock, patch.object(FalkorDBDriver, "_graph_for", return_value=object()):
        driver = FalkorDBDriver(path=rdb, graph="hyper_graph")
    assert driver.path == rdb.resolve()
    open_mock.assert_called_once()


def test_driver_warns_when_network_and_path_are_both_supplied(tmp_path: Path) -> None:
    """Network args + path must emit DeprecationWarning and ignore the network side."""
    rdb = tmp_path / "cortex.rdb"
    fake_client = _make_fake_client()
    with patch(
        "tools.graph.driver.falkordb_driver._open_local_falkordb",
        return_value=fake_client,
    ), patch.object(FalkorDBDriver, "_graph_for", return_value=object()):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            FalkorDBDriver(
                path=rdb,
                graph="hyper_graph",
                uri="redis://legacy.example:6379",
                host="legacy.example",
                port=6379,
                user="u",
                password="p",  # sensitive-guard:allow -- local test fixture
                ssl=True,
            )
    deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert deprecations, "expected DeprecationWarning when network args are supplied with path"


def test_driver_warns_when_constructed_without_path() -> None:
    """Constructing without 'path' must still warn (network fallback path)."""

    class _FakeClient:
        def close(self) -> None:
            return None

        def select_graph(self, name: str) -> object:
            return object()

    fake_client = _FakeClient()
    fake_falkordb = type("F", (), {"FalkorDB": lambda **kw: fake_client})
    fake_redis = type("R", (), {})
    with patch.dict(sys.modules, {"falkordb": fake_falkordb, "redis": fake_redis}):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            FalkorDBDriver(host="example.com", port=6379, graph="hyper_graph")
    deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert deprecations


def test_open_local_falkordb_creates_parent(tmp_path: Path, monkeypatch) -> None:
    """_open_local_falkordb must ensure the .rdb parent directory exists."""
    target = tmp_path / "nested" / "deep" / "cortex.rdb"
    assert not target.parent.exists()

    captured: dict = {}

    class _StubFalkorDB:
        def __init__(self, path: str, **kwargs) -> None:
            captured["path"] = path
            captured["kwargs"] = kwargs
            captured["parent_exists"] = Path(path).parent.exists()
            return None

    monkeypatch.setitem(
        sys.modules,
        "redislite.falkordb_client",
        type("M", (), {"FalkorDB": _StubFalkorDB}),
    )

    _open_local_falkordb(target)
    assert captured["parent_exists"] is True
    assert captured["path"] == str(target)
    assert captured["kwargs"] == {
        "socket_timeout": 300.0,
        "socket_connect_timeout": 30,
    }


def test_driver_close_handles_exceptions(tmp_path: Path) -> None:
    """close() must not propagate exceptions raised by the embedded backend."""
    rdb = tmp_path / "cortex.rdb"

    class _BoomClient:
        def close(self) -> None:
            raise RuntimeError("boom")

        def select_graph(self, name: str) -> object:
            return object()

    fake_client = _BoomClient()
    with patch(
        "tools.graph.driver.falkordb_driver._open_local_falkordb",
        return_value=fake_client,
    ), patch.object(FalkorDBDriver, "_graph_for", return_value=object()):
        driver = FalkorDBDriver(path=rdb, graph="hyper_graph")
    driver.close()  # must not raise


def test_driver_routes_graphs_to_sibling_instance_files(tmp_path: Path) -> None:
    primary_path = tmp_path / "v1" / "instances" / "default" / "falkordb" / "code" / "data.rdb"
    sibling_path = tmp_path / "v1" / "instances" / "other" / "falkordb" / "code" / "data.rdb"
    primary_path.parent.mkdir(parents=True)
    sibling_path.parent.mkdir(parents=True)
    primary_path.touch()
    sibling_path.touch()

    class _Client:
        def __init__(self, graph_name: str) -> None:
            self.graph_name = graph_name
            self.graph = object()
            self.closed = False

        def list_graphs(self) -> list[str]:
            return [self.graph_name]

        def select_graph(self, name: str) -> object:
            assert name == self.graph_name
            return self.graph

        def close(self) -> None:
            self.closed = True

    primary_client = _Client("cortext")
    sibling_client = _Client("hyperpack")

    def open_client(path: Path) -> _Client:
        return primary_client if Path(path).resolve() == primary_path.resolve() else sibling_client

    with patch(
        "tools.graph.driver.falkordb_driver._open_local_falkordb",
        side_effect=open_client,
    ):
        driver = FalkorDBDriver(
            path=primary_path,
            graph="cortext",
            owner_id="code",
            additional_paths=[sibling_path],
        )
        assert asyncio.run(driver.list_databases()) == ["cortext", "hyperpack"]
        assert driver._graph_for("hyperpack") is sibling_client.graph
        driver.close()

    assert primary_client.closed is True
    assert sibling_client.closed is True


def test_real_falkordblite_persists_and_isolates_graphs(tmp_path: Path) -> None:
    pytest.importorskip("redislite.falkordb_client")
    rdb = tmp_path / "owner" / "data.rdb"
    first = FalkorDBDriver(path=rdb, graph="alpha", owner_id="code")
    first.execute_query_sync("CREATE (:Probe {id: $id})", {"id": "one"})
    with pytest.raises(Exception, match="already owned"):
        FalkorDBDriver(path=rdb, graph="alpha", owner_id="code")
    first.close()

    reopened = FalkorDBDriver(path=rdb, graph="alpha", owner_id="code")
    records, _, _ = reopened.execute_query_sync(
        "MATCH (n:Probe {id: $id}) RETURN count(n) AS count", {"id": "one"},
    )
    assert records == [{"count": 1}]
    reopened.close()

    other_graph = FalkorDBDriver(path=rdb, graph="beta", owner_id="code")
    records, _, _ = other_graph.execute_query_sync("MATCH (n:Probe) RETURN count(n) AS count")
    assert records == [{"count": 0}]
    other_graph.close()


def test_real_falkordblite_routes_queries_across_instance_files(tmp_path: Path) -> None:
    pytest.importorskip("redislite.falkordb_client")
    primary_path = tmp_path / "v1" / "instances" / "default" / "falkordb" / "code" / "data.rdb"
    sibling_path = tmp_path / "v1" / "instances" / "other" / "falkordb" / "code" / "data.rdb"

    primary = FalkorDBDriver(path=primary_path, graph="alpha", owner_id="code")
    primary.execute_query_sync("CREATE (:Probe {id: $id})", {"id": "alpha"})
    primary.close()

    sibling = FalkorDBDriver(path=sibling_path, graph="beta", owner_id="code")
    sibling.execute_query_sync("CREATE (:Probe {id: $id})", {"id": "beta"})
    sibling.close()

    routed = FalkorDBDriver(
        path=primary_path,
        graph="alpha",
        owner_id="code",
        additional_paths=[sibling_path],
    )
    try:
        assert asyncio.run(routed.list_databases()) == ["alpha", "beta"]
        alpha_rows, _, _ = routed.execute_query_sync(
            "MATCH (n:Probe) RETURN n.id AS id", database="alpha"
        )
        beta_rows, _, _ = routed.execute_query_sync(
            "MATCH (n:Probe) RETURN n.id AS id", database="beta"
        )
        assert alpha_rows == [{"id": "alpha"}]
        assert beta_rows == [{"id": "beta"}]
    finally:
        routed.close()


def test_real_falkordblite_relationship_plan_uses_both_indexes(tmp_path: Path) -> None:
    pytest.importorskip("redislite.falkordb_client")
    driver = FalkorDBDriver(
        path=tmp_path / "indexed" / "data.rdb",
        graph="code",
        owner_id="code",
    )
    manifest = GraphSchemaManifest(
        "relationship_probe",
        1,
        (
            SchemaIndex("File", ("id",)),
            SchemaIndex("Function", ("id",)),
        ),
    )
    try:
        result = asyncio.run(
            driver.ensure_schema(manifest, database="code", timeout_seconds=20)
        )
        assert result.verified_count == 2
        driver.execute_query_sync(
            "CREATE (:File {id: $file_id, project_id_normalized: $scope}), "
            "(:Function {id: $function_id, project_id_normalized: $scope})",
            {"file_id": "src/main.c", "function_id": "fn:main", "scope": "demo"},
        )
        driver.execute_query_sync(
            "CREATE (:File {id: $file_id, project_id_normalized: $scope}), "
            "(:Function {id: $function_id, project_id_normalized: $scope})",
            {"file_id": "src/main.c", "function_id": "fn:main", "scope": "other"},
        )
        query = compile_relationship_upsert(
            RelationshipGroup("File", "Function", "CONTAINS")
        )
        rows = [
            {
                "source_id": "src/main.c",
                "target_id": "fn:main",
                "project_id": "demo",
                "project_id_normalized": "demo",
                "properties": {},
            }
        ]
        plan = str(driver.graph.explain(query, params={"rows": rows}))
        assert "Node By Index Scan | (a:File)" in plan
        assert "Node By Index Scan | (b:Function)" in plan
        assert "All Node Scan" not in plan
        assert "Cartesian Product" not in plan

        writer = LanguageCodeWriter(driver, database="code", batch_size=10)
        scoped_relation = {
            **rows[0],
            "source_label": "File",
            "target_label": "Function",
            "rel_type": "CONTAINS",
        }
        assert asyncio.run(
            writer.write_relations_typed([scoped_relation, dict(scoped_relation)])
        ) == 2
        driver.execute_query_sync(query, {"rows": rows})
        driver.execute_query_sync(query, {"rows": rows})
        records, _, _ = driver.execute_query_sync(
            "MATCH (:File {id: $file_id, project_id_normalized: $scope})"
            "-[r:CONTAINS]->"
            "(:Function {id: $function_id, project_id_normalized: $scope}) "
            "RETURN count(r) AS count",
            {"file_id": "src/main.c", "function_id": "fn:main", "scope": "demo"},
        )
        assert records == [{"count": 1}]
        records, _, _ = driver.execute_query_sync(
            "MATCH (:File {id: $file_id, project_id_normalized: $scope})"
            "-[r:CONTAINS]->"
            "(:Function {id: $function_id, project_id_normalized: $scope}) "
            "RETURN count(r) AS count",
            {"file_id": "src/main.c", "function_id": "fn:main", "scope": "other"},
        )
        assert records == [{"count": 0}]
    finally:
        driver.close()


def _make_fake_client() -> object:
    """Return a minimal fake backend that satisfies the driver's init contract."""

    class _FakeClient:
        def close(self) -> None:
            return None

        def select_graph(self, name: str) -> object:
            return object()

    return _FakeClient()
