"""LadybugDB provider plumbing tests: selection, isolation, fail-closed rules.

Covers the phase-02/phase-05 acceptance criteria of
``plans/260913-1538-ladybug-graph-provider``:
provider aliases (including the deprecated ``kuzu``), environment
isolation, target resolution (local-only, remote rejected), win32 default
flip, and storage-factory wiring.
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from cortex_harness.storage.config import (
    BackendMode,
    resolve_storage,
    storage_overlay,
    validate_backend_config,
)
from cortex_harness.storage.targets import (
    EffectiveStorageTarget,
    _runtime_graph_target_from_env,
    effective_graph_target_from_env,
    local_graph_target,
    remote_graph_target,
)
from tools.graph.core.base import GraphProvider
from tools.graph.core.provider_contract import (
    isolate_graph_provider_environment,
    normalize_graph_provider,
    normalize_graph_provider_name,
)
from tools.graph.core.factory import GraphDriverFactory


# ---------------------------------------------------------------------------
# Provider selection
# ---------------------------------------------------------------------------


def test_provider_aliases_normalize_to_ladybug() -> None:
    for alias in ("ladybug", "lbug", "lady-bug", "kuzu"):
        assert normalize_graph_provider(alias) is GraphProvider.LADYBUG


def test_kuzu_enum_value_unchanged_but_routed() -> None:
    assert GraphProvider.KUZU.value == "kuzu"
    assert normalize_graph_provider_name("kuzu") == "ladybug"


def test_unknown_provider_still_fails_closed() -> None:
    with pytest.raises(ValueError, match="Unsupported graph provider"):
        normalize_graph_provider("tokudb")


def test_ladybug_allowed_in_contract() -> None:
    assert normalize_graph_provider_name("ladybug") == "ladybug"


# ---------------------------------------------------------------------------
# Environment isolation
# ---------------------------------------------------------------------------


def test_isolation_strips_inactive_provider_settings() -> None:
    env = {
        "FALKORDB_URI": "redis://old:6379",
        "FALKORDB_PATH": "/old/data.rdb",
        "NEO4J_URI": "bolt://old:7687",
        "LADYBUG_PATH": "/old/graph",
        "DOC_FALKORDB_GRAPH": "x",
        "LADYBUG_PROVIDER": "falkordb",
    }
    provider = isolate_graph_provider_environment(
        env, "falkordb", scoped_key="CODE_GRAPH_PROVIDER"
    )
    assert provider == "falkordb"
    assert env["GRAPH_PROVIDER"] == "falkordb"
    assert env["LADYBUG_PROVIDER"] == "falkordb"
    assert "NEO4J_URI" not in env
    assert "LADYBUG_PATH" not in env
    assert env["FALKORDB_URI"] == "redis://old:6379"  # active provider kept


def test_isolation_for_ladybug_strips_falkordb_and_neo4j() -> None:
    env = {
        "FALKORDB_URI": "redis://old:6379",
        "FALKORDB_GRAPH": "g",
        "NEO4J_URI": "bolt://old:7687",
        "LADYBUG_PATH": "/new/graph",
    }
    provider = isolate_graph_provider_environment(
        env, "ladybug", scoped_key="CODE_GRAPH_PROVIDER"
    )
    assert provider == "ladybug"
    assert "FALKORDB_URI" not in env
    assert "FALKORDB_GRAPH" not in env
    assert "NEO4J_URI" not in env
    assert env["LADYBUG_PATH"] == "/new/graph"


# ---------------------------------------------------------------------------
# Target resolution
# ---------------------------------------------------------------------------


def test_ladybug_target_is_local_file() -> None:
    target = _runtime_graph_target_from_env(
        {
            "GRAPH_PROVIDER": "ladybug",
            "LADYBUG_PATH": "/tmp/store.lbug",
            "LADYBUG_GRAPH": "hyper_graph",
            "CORTEX_STORAGE_OWNER": "code",
        }
    )
    assert target.provider == "ladybug"
    assert target.mode == "file"
    assert target.namespace == "hyper_graph"


def test_ladybug_with_remote_uri_fails_closed() -> None:
    with pytest.raises(ValueError, match="local-only"):
        _runtime_graph_target_from_env(
            {
                "GRAPH_PROVIDER": "ladybug",
                "FALKORDB_URI": "redis://remote:6379",
            }
        )


def test_remote_graph_target_rejects_ladybug_provider() -> None:
    with pytest.raises(ValueError, match="local-only"):
        remote_graph_target(
            "redis://remote:6379", graph="g", role="code", provider="ladybug"
        )


def test_local_graph_target_accepts_provider() -> None:
    target = local_graph_target("/tmp/x", graph="g", role="code", provider="ladybug")
    assert target.provider == "ladybug"
    # Default stays falkordb so historical fingerprints are unchanged.
    default_target = local_graph_target("/tmp/x", graph="g", role="code")
    assert default_target.provider == "falkordb"


def test_effective_graph_target_descriptor_roundtrip() -> None:
    env = {
        "GRAPH_PROVIDER": "ladybug",
        "LADYBUG_PATH": "/tmp/store.lbug",
        "LADYBUG_GRAPH": "hyper_graph",
        "CORTEX_STORAGE_OWNER": "code",
    }
    runtime = effective_graph_target_from_env(env)
    # The propagated descriptor matches the runtime reconstruction.
    assert EffectiveStorageTarget.from_json(runtime.canonical_json) == runtime
    # A descriptor built against different runtime values fails closed.
    with pytest.raises(ValueError, match="does not match runtime"):
        effective_graph_target_from_env(
            {
                "GRAPH_PROVIDER": "ladybug",
                "LADYBUG_PATH": "/tmp/store.lbug",
                "LADYBUG_GRAPH": "other_graph",
                "CORTEX_STORAGE_OWNER": "code",
                "CORTEX_EFFECTIVE_GRAPH_TARGET": runtime.canonical_json,
            }
        )


# ---------------------------------------------------------------------------
# Storage config
# ---------------------------------------------------------------------------


def test_validate_backend_config_rejects_remote_ladybug() -> None:
    with pytest.raises(ValueError, match="ladybug is local-only"):
        validate_backend_config(
            "remote",
            {"falkordb_uri": "redis://remote:6379"},
            graph_provider="ladybug",
        )
    # Non-ladybug providers keep the historical behavior.
    mode, config = validate_backend_config(
        "remote",
        {"falkordb_uri": "redis://remote:6379"},
        graph_provider="falkordb",
    )
    assert mode is BackendMode.REMOTE
    assert config is not None


def test_resolve_storage_ladybug_paths_deterministic(tmp_path: Path) -> None:
    resolved_a = resolve_storage(tmp_path, data_home=tmp_path / "data")
    resolved_b = resolve_storage(tmp_path, data_home=tmp_path / "data")
    assert resolved_a.ladybug_code_path == resolved_b.ladybug_code_path
    assert resolved_a.ladybug_doc_path == resolved_b.ladybug_doc_path
    assert resolved_a.ladybug_code_path.is_absolute()
    assert resolved_a.ladybug_code_path != resolved_a.ladybug_doc_path
    # Same root convention as the falkordb path: both under the instance root.
    assert resolved_a.ladybug_code_path.is_relative_to(resolved_a.instance_root)
    assert resolved_a.falkordb_code_path.is_relative_to(resolved_a.instance_root)


def test_resolve_storage_honors_ladybug_path_override(tmp_path: Path) -> None:
    override = tmp_path / "custom" / "store.lbug"
    resolved = resolve_storage(tmp_path, data_home=tmp_path / "data", ladybug_path=override)
    # The shared override seeds the code role only, mirroring FALKORDB_PATH.
    assert resolved.ladybug_code_path == override.resolve()
    assert resolved.ladybug_doc_path != override
    assert resolved.falkordb_code_path != override  # falkordb layout untouched


def test_storage_overlay_for_ladybug(tmp_path: Path) -> None:
    resolved = resolve_storage(tmp_path, data_home=tmp_path / "data")
    overlay = storage_overlay(
        resolved,
        owner="code",
        graph_provider="ladybug",
        code_graph="hyper_graph",
        code_collection="project",
    )
    assert overlay["GRAPH_PROVIDER"] == "ladybug"
    assert overlay["LADYBUG_PATH"] == str(resolved.ladybug_code_path)
    assert "FALKORDB_PATH" not in overlay
    assert overlay["CORTEX_EFFECTIVE_GRAPH_TARGET"]
    target = EffectiveStorageTarget.from_json(overlay["CORTEX_EFFECTIVE_GRAPH_TARGET"])
    assert target.provider == "ladybug"
    assert target.mode == "file"


# ---------------------------------------------------------------------------
# Win32 default flip
# ---------------------------------------------------------------------------


def test_win32_default_provider_is_ladybug(monkeypatch) -> None:
    from cortex_harness import dev

    monkeypatch.setattr(dev.sys, "platform", "win32")
    assert dev._default_graph_provider() == "ladybug"
    assert dev._graph_provider({}, "CODE_GRAPH_PROVIDER") == "ladybug"


def test_posix_default_provider_unchanged(monkeypatch) -> None:
    from cortex_harness import dev

    monkeypatch.setattr(dev.sys, "platform", "darwin")
    assert dev._default_graph_provider() == "falkordb"
    assert dev._graph_provider({}, "CODE_GRAPH_PROVIDER") == "falkordb"


def test_explicit_provider_wins_over_platform_default(monkeypatch) -> None:
    from cortex_harness import dev

    monkeypatch.setattr(dev.sys, "platform", "win32")
    assert dev._graph_provider({"GRAPH_PROVIDER": "falkordb"}, "CODE_GRAPH_PROVIDER") == "falkordb"
    assert dev._graph_provider({"CODE_GRAPH_PROVIDER": "neo4j"}, "CODE_GRAPH_PROVIDER") == "neo4j"


def test_cli_env_provider_win32_default(monkeypatch) -> None:
    from tools.graph.cli import env_graph_provider

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("CODE_GRAPH_PROVIDER", raising=False)
    monkeypatch.delenv("GRAPH_PROVIDER", raising=False)
    assert env_graph_provider() == "ladybug"
    monkeypatch.setattr(sys, "platform", "darwin")
    assert env_graph_provider() == "falkordb"
    # Explicit configuration always wins.
    assert env_graph_provider(default="neo4j") == "neo4j"


def test_runtime_target_win32_default(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    # _runtime_graph_target_from_env reads the env mapping only; the flip is
    # applied by the callers that default the provider into the env.
    target = _runtime_graph_target_from_env(
        {"GRAPH_PROVIDER": "ladybug", "LADYBUG_PATH": "embedded"}
    )
    assert target.provider == "ladybug"


# ---------------------------------------------------------------------------
# Driver factories
# ---------------------------------------------------------------------------


@pytest.fixture()
def fake_ladybug_module():
    created = []

    class _FakeDatabase:
        def __init__(self, database_path, *, read_only=False, **kwargs):
            created.append((Path(database_path), read_only))
            self.path = database_path
            Path(database_path).touch(exist_ok=True)

        def close(self):
            return None

    class _FakeConnection:
        def __init__(self, database):
            self.database = database

        def set_query_timeout(self, ms):
            return None

        def execute(self, query, parameters=None):
            class _R:
                def get_column_names(self_inner):
                    return ["ok"]

                def has_next(self_inner):
                    return False

                def get_next(self_inner):
                    return []

            return _R()

    module = types.ModuleType("ladybug")
    module.Database = _FakeDatabase
    module.Connection = _FakeConnection
    with patch.dict(sys.modules, {"ladybug": module}):
        yield created


def test_graph_driver_factory_creates_ladybug(tmp_path: Path, fake_ladybug_module) -> None:
    from cortex_harness.storage.layout import ladybug_store_file_name

    driver = asyncio.run(
        GraphDriverFactory.create_driver(
            GraphProvider.LADYBUG,
            {"path": tmp_path / "code.lbug" / ladybug_store_file_name("hyper_graph")},
        )
    )
    try:
        assert isinstance(driver.provider, GraphProvider)
        assert driver.provider is GraphProvider.LADYBUG
    finally:
        driver.close()


def test_graph_driver_factory_kuzu_routes_to_ladybug(tmp_path: Path, fake_ladybug_module) -> None:
    from tools.graph.driver.ladybug_driver import LadybugDriver

    driver = asyncio.run(
        GraphDriverFactory.create_driver(
            GraphProvider.KUZU,
            {"path": tmp_path / "g.lbug"},
        )
    )
    try:
        assert isinstance(driver, LadybugDriver)
    finally:
        driver.close()


def test_graph_driver_factory_ladybug_requires_path() -> None:
    with pytest.raises(ValueError, match="local store path"):
        asyncio.run(GraphDriverFactory.create_driver(GraphProvider.LADYBUG, {}))


def test_graph_driver_factory_ladybug_rejects_remote_fields(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="local-only"):
        asyncio.run(
            GraphDriverFactory.create_driver(
                GraphProvider.LADYBUG,
                {"path": tmp_path / "g.lbug", "uri": "bolt://x:7687"},
            )
        )


def test_storage_factory_get_ladybug_driver(tmp_path: Path, fake_ladybug_module) -> None:
    from cortex_harness.storage.factory import StorageFactory
    from tools.graph.driver.ladybug_driver import LadybugDriver

    resolved = resolve_storage(tmp_path, data_home=tmp_path / "data")
    factory = StorageFactory(backend_mode=BackendMode.LOCAL, resolved=resolved)
    driver = factory.get_ladybug_driver("hyper_graph")
    try:
        assert isinstance(driver, LadybugDriver)
        assert driver.path == resolved.ladybug_code_path
        assert fake_ladybug_module[-1][0] == resolved.ladybug_code_path
    finally:
        driver.close()


def test_storage_factory_ladybug_rejected_with_remote_falkordb(
    tmp_path: Path, fake_ladybug_module
) -> None:
    from cortex_harness.storage.factory import StorageFactory

    resolved = resolve_storage(tmp_path, data_home=tmp_path / "data")
    factory = StorageFactory(
        backend_mode=BackendMode.REMOTE,
        resolved=resolved,
        remote=types.SimpleNamespace(
            falkordb_uri="redis://remote:6379", falkordb_password=None, falkordb_ssl=False,  # sensitive-guard:allow -- None placeholder in a fake config
            qdrant_url=None, qdrant_api_key=None,
        ),
    )
    with pytest.raises(ValueError, match="ladybug is local-only"):
        factory.get_ladybug_driver("hyper_graph")


def test_create_from_env_ladybug(tmp_path: Path, fake_ladybug_module, monkeypatch) -> None:
    import os

    monkeypatch.setenv("LADYBUG_PATH", str(tmp_path / "env.lbug"))
    monkeypatch.setenv("LADYBUG_GRAPH", "hyper_graph")
    driver = asyncio.run(GraphDriverFactory.create_from_env(GraphProvider.LADYBUG))
    try:
        assert driver.provider is GraphProvider.LADYBUG
        assert driver.path == Path(os.environ["LADYBUG_PATH"]).resolve()
    finally:
        driver.close()
