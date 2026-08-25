"""Tests for the remote connectivity probe and provisioning helpers.

These tests focus on the pure helpers (``probe_*``, ``provision_*``,
``render_provision_line``, ``setup_remote_falkordb_schema``) and on the
project-config scanner. Live network calls are stubbed via
:mod:`unittest.mock` so the suite never requires a reachable Qdrant or
FalkorDB server.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest import mock

import pytest


ROOT = Path(__file__).resolve().parents[1]
LIFECYCLE_PATH = ROOT / "scripts" / "mcp-lifecycle.py"
STORAGE_INIT = ROOT / "cortex_harness" / "storage" / "__init__.py"

# Make the code-tiny package importable so ``tools.graph.driver.falkordb_driver``
# resolves during probe/provision tests.
for entry in (ROOT, ROOT / "code-tiny"):
    path = str(entry)
    if path not in sys.path:
        sys.path.insert(0, path)

REMOTE_PROBE_SPEC = importlib.util.spec_from_file_location(
    "cortex_harness.storage.remote_probe",
    ROOT / "cortex_harness" / "storage" / "remote_probe.py",
)
REMOTE_PROBE = importlib.util.module_from_spec(REMOTE_PROBE_SPEC)
REMOTE_PROBE_SPEC.loader.exec_module(REMOTE_PROBE)


def _load_lifecycle_module():
    spec = importlib.util.spec_from_file_location(
        "mcp_lifecycle_remote_test", LIFECYCLE_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LIFECYCLE = _load_lifecycle_module()


@pytest.fixture
def local_config_dir(tmp_path, monkeypatch):
    """Force the lifecycle scanner to read configs from ``tmp_path``."""
    config_dir = tmp_path / ".cortext-harness" / "config"
    config_dir.mkdir(parents=True)
    monkeypatch.setattr(LIFECYCLE, "ROOT", tmp_path)
    # Pin cwd to a fresh directory so the caller-aware scan does not pick
    # up unrelated project configs living under the test runner's cwd.
    monkeypatch.chdir(tmp_path)
    return config_dir


def _write_config(config_dir: Path, name: str, payload: dict) -> Path:
    path = config_dir / f"{name}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# ── probe_qdrant / probe_falkordb / probe_all ───────────────────────────


class TestProbeQdrant:
    def test_no_url_returns_skipped(self):
        from cortex_harness.storage.config import RemoteStorageConfig

        config = RemoteStorageConfig(falkordb_uri="redis://localhost:6379")
        result = REMOTE_PROBE.probe_qdrant(config)
        assert result.reachable is True
        assert "skipped" in result.message
        assert result.backend == "qdrant"

    def test_reachable_server(self):
        from cortex_harness.storage.config import RemoteStorageConfig

        config = RemoteStorageConfig(qdrant_url="http://qdrant.example:6333")
        fake_client = mock.Mock()
        with mock.patch(
            "cortex_harness.storage.qdrant_remote.get_remote_client",
            return_value=fake_client,
        ):
            result = REMOTE_PROBE.probe_qdrant(config)
        assert result.reachable is True
        assert result.message == "reachable"
        fake_client.get_collections.assert_called_once_with()

    def test_unreachable_server(self):
        from cortex_harness.storage.config import RemoteStorageConfig

        config = RemoteStorageConfig(qdrant_url="http://qdrant.invalid:6333")
        fake_client = mock.Mock()
        fake_client.get_collections.side_effect = RuntimeError("boom")
        with mock.patch(
            "cortex_harness.storage.qdrant_remote.get_remote_client",
            return_value=fake_client,
        ):
            result = REMOTE_PROBE.probe_qdrant(config)
        assert result.reachable is False
        assert result.cause is not None


class TestProbeFalkordb:
    def test_no_uri_returns_skipped(self):
        from cortex_harness.storage.config import RemoteStorageConfig

        config = RemoteStorageConfig(qdrant_url="http://localhost:6333")
        result = REMOTE_PROBE.probe_falkordb(config)
        assert result.reachable is True
        assert "skipped" in result.message

    def test_reachable_server(self):
        from cortex_harness.storage.config import RemoteStorageConfig

        config = RemoteStorageConfig(falkordb_uri="redis://falkor.example:6379")
        driver = mock.Mock()
        # Patch the attribute on the already-imported FalkorDBDriver class.
        from tools.graph.driver import falkordb_driver as fmod

        with mock.patch.object(fmod, "FalkorDBDriver", return_value=driver):
            result = REMOTE_PROBE.probe_falkordb(config)
        assert result.reachable is True
        driver.execute_query_sync.assert_called_once_with("RETURN 1 AS ok")
        driver.execute_query.assert_not_called()

    def test_query_failure_reports_unreachable(self):
        from cortex_harness.storage.config import RemoteStorageConfig

        config = RemoteStorageConfig(falkordb_uri="redis://falkor.invalid:6379")
        driver = mock.Mock()
        driver.execute_query_sync.side_effect = ConnectionError("connection closed")
        from tools.graph.driver import falkordb_driver as fmod

        with mock.patch.object(fmod, "FalkorDBDriver", return_value=driver):
            result = REMOTE_PROBE.probe_falkordb(config)
        assert result.reachable is False
        assert result.message == "connection closed"


class TestProbeAll:
    def test_returns_both_results(self):
        from cortex_harness.storage.config import RemoteStorageConfig

        config = RemoteStorageConfig(
            qdrant_url="http://localhost:6333",
            falkordb_uri="redis://localhost:6379",
        )
        with mock.patch.object(REMOTE_PROBE, "probe_qdrant", return_value=mock.Mock(backend="qdrant")), mock.patch.object(
            REMOTE_PROBE, "probe_falkordb", return_value=mock.Mock(backend="falkordb")
        ):
            results = REMOTE_PROBE.probe_all(config)
        assert {r.backend for r in results} == {"qdrant", "falkordb"}


# ── provision_qdrant_collection / provision_falkordb_graph ───────────────


class TestProvisionQdrant:
    def test_skips_when_no_url(self):
        from cortex_harness.storage.config import RemoteStorageConfig

        config = RemoteStorageConfig(falkordb_uri="redis://localhost:6379")
        result = REMOTE_PROBE.provision_qdrant_collection(config, "any")
        assert result.action == "skipped"

    def test_reports_existing_collection(self):
        from cortex_harness.storage.config import RemoteStorageConfig

        config = RemoteStorageConfig(qdrant_url="http://localhost:6333")
        fake_client = mock.Mock()
        fake_client.collection_exists.return_value = True
        with mock.patch(
            "cortex_harness.storage.qdrant_remote.get_remote_client",
            return_value=fake_client,
        ):
            result = REMOTE_PROBE.provision_qdrant_collection(config, "existing")
        assert result.action == "exists"
        fake_client.create_collection.assert_not_called()

    def test_creates_missing_collection(self):
        from cortex_harness.storage.config import RemoteStorageConfig

        config = RemoteStorageConfig(qdrant_url="http://localhost:6333")
        fake_client = mock.Mock()
        fake_client.collection_exists.return_value = False
        with mock.patch(
            "cortex_harness.storage.qdrant_remote.get_remote_client",
            return_value=fake_client,
        ):
            result = REMOTE_PROBE.provision_qdrant_collection(
                config, "fresh", vector_size=128, distance="COSINE"
            )
        assert result.action == "created"
        fake_client.create_collection.assert_called_once()


class TestProvisionFalkordb:
    def test_skips_when_no_uri(self):
        from cortex_harness.storage.config import RemoteStorageConfig

        config = RemoteStorageConfig(qdrant_url="http://localhost:6333")
        result = REMOTE_PROBE.provision_falkordb_graph(config, "any")
        assert result.action == "skipped"

    def test_accessible_graph(self):
        from cortex_harness.storage.config import RemoteStorageConfig

        config = RemoteStorageConfig(falkordb_uri="redis://localhost:6379")
        driver = mock.Mock()
        from tools.graph.driver import falkordb_driver as fmod

        with mock.patch.object(fmod, "FalkorDBDriver", return_value=driver):
            result = REMOTE_PROBE.provision_falkordb_graph(config, "graph")
        assert result.action == "exists"
        driver.execute_query_sync.assert_called_once_with("RETURN 1 AS ok")
        driver.execute_query.assert_not_called()


# ── setup_remote_falkordb_schema (subprocess wrapper) ────────────────────


class TestSetupRemoteSchema:
    def test_skips_when_no_uri(self, tmp_path):
        from cortex_harness.storage.config import RemoteStorageConfig

        config = RemoteStorageConfig(qdrant_url="http://localhost:6333")
        result = REMOTE_PROBE.setup_remote_falkordb_schema(
            config, "graph", python=sys.executable, setup_script=tmp_path / "setup.py"
        )
        assert result.action == "skipped"

    def test_runs_setup_script_and_reports_created(self, tmp_path):
        from cortex_harness.storage.config import RemoteStorageConfig

        config = RemoteStorageConfig(falkordb_uri="redis://localhost:6379")
        script = tmp_path / "setup_constraints.py"
        script.write_text("#!/usr/bin/env python\n", encoding="utf-8")
        completed = mock.Mock(returncode=0, stdout="ok", stderr="")
        with mock.patch.object(REMOTE_PROBE.subprocess, "run", return_value=completed):
            result = REMOTE_PROBE.setup_remote_falkordb_schema(
                config, "graph", python=sys.executable, setup_script=script
            )
        assert result.action == "created"

    def test_reports_failed_when_script_exits_nonzero(self, tmp_path):
        from cortex_harness.storage.config import RemoteStorageConfig

        config = RemoteStorageConfig(falkordb_uri="redis://localhost:6379")
        script = tmp_path / "setup_constraints.py"
        script.write_text("#!/usr/bin/env python\n", encoding="utf-8")
        completed = mock.Mock(returncode=2, stdout="", stderr="boom")
        with mock.patch.object(REMOTE_PROBE.subprocess, "run", return_value=completed):
            result = REMOTE_PROBE.setup_remote_falkordb_schema(
                config, "graph", python=sys.executable, setup_script=script
            )
        assert result.action == "failed"
        assert "boom" in result.message


# ── Project config scanner ──────────────────────────────────────────────


class TestScanProjectBackends:
    def test_empty_when_no_config_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(LIFECYCLE, "ROOT", tmp_path)
        monkeypatch.chdir(tmp_path)
        assert LIFECYCLE._scan_project_backends() == []

    def test_local_project_classification(self, local_config_dir):
        _write_config(
            local_config_dir,
            "my_proj",
            {"project": {"code": "my_proj", "name": "My"}},
        )
        result = LIFECYCLE._scan_project_backends()
        assert len(result) == 1
        assert result[0]["backend_mode"] == "local"
        assert result[0]["project_id"] == "my_proj"

    def test_remote_project_classification(self, local_config_dir):
        _write_config(
            local_config_dir,
            "remote_proj",
            {
                "project": {"code": "remote_proj"},
                "storage_backend": "remote",
                "remote": {
                    "qdrant_url": "http://qdrant.local:6333",
                    "falkordb_uri": "redis://falkor.local:6379",
                },
            },
        )
        result = LIFECYCLE._scan_project_backends()
        assert len(result) == 1
        assert result[0]["backend_mode"] == "remote"
        assert result[0]["remote_config"] is not None

    def test_malformed_json_is_skipped(self, local_config_dir):
        (local_config_dir / "broken.json").write_text("not json{", encoding="utf-8")
        _write_config(
            local_config_dir,
            "good",
            {"project": {"code": "good"}},
        )
        result = LIFECYCLE._scan_project_backends()
        assert len(result) == 1
        assert result[0]["project_id"] == "good"


class TestResolveCollectionNames:
    def test_convention_defaults(self, tmp_path):
        path = tmp_path / "x.json"
        path.write_text(json.dumps({"project": {"code": "my_proj"}}), encoding="utf-8")
        names = LIFECYCLE._resolve_collection_names("my_proj", str(path))
        assert names["code_collection"] == "my_proj_code"
        assert names["doc_collection"] == "my_proj_doc"
        assert names["code_graph"] == "hyper_graph"

    def test_env_overrides(self, tmp_path):
        path = tmp_path / "x.json"
        path.write_text(
            json.dumps(
                {
                    "code": {"env": {"QDRANT_COLLECTION": "code_x", "FALKORDB_GRAPH": "code_g"}},
                    "doc": {
                        "env": {
                            "QDRANT_COLLECTION_DOC": "doc_x",
                            "DOC_FALKORDB_GRAPH": "doc_g",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        names = LIFECYCLE._resolve_collection_names("any", str(path))
        assert names["code_collection"] == "code_x"
        assert names["doc_collection"] == "doc_x"
        assert names["code_graph"] == "code_g"
        assert names["doc_graph"] == "doc_g"

    def test_missing_file_falls_back_to_convention(self, tmp_path):
        names = LIFECYCLE._resolve_collection_names("x", str(tmp_path / "missing.json"))
        assert names["code_collection"] == "x_code"


# ── render_provision_line ───────────────────────────────────────────────


def test_render_provision_line_uses_tag_map():
    result = REMOTE_PROBE.ProvisionResult(
        resource="qdrant:demo", action="created", message="ok"
    )
    rendered = REMOTE_PROBE.render_provision_line(result)
    assert rendered.startswith("[new]")
    assert "qdrant:demo" in rendered
    assert "ok" in rendered


def test_render_provision_line_unknown_action_falls_back():
    result = REMOTE_PROBE.ProvisionResult(
        resource="qdrant:demo", action="bogus", message="?"
    )
    assert REMOTE_PROBE.render_provision_line(result).startswith("[?]")


def test_force_local_active_helper():
    assert REMOTE_PROBE.force_local_active() is False
    with mock.patch.dict("os.environ", {REMOTE_PROBE.ENV_FORCE_LOCAL: "1"}):
        assert REMOTE_PROBE.force_local_active() is True
