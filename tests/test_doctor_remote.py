"""Tests for the doctor remote-backend checks."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest import mock

import pytest


ROOT = Path(__file__).resolve().parents[1]
LIFECYCLE_PATH = ROOT / "scripts" / "mcp-lifecycle.py"

# Ensure ``cortex_harness.storage.remote_probe.ProbeResult`` is importable from
# the same module path the production code uses, so test fixtures can compare
# against the same dataclass identity the lifecycle calls construct.
sys_path_entry = str(ROOT / "cortex_harness")
import sys as _sys
if sys_path_entry not in _sys.path:
    _sys.path.insert(0, sys_path_entry)

from cortex_harness.storage.remote_probe import ProbeResult  # noqa: E402


def _load_lifecycle_module():
    spec = importlib.util.spec_from_file_location(
        "mcp_lifecycle_doctor_remote_test", LIFECYCLE_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LIFECYCLE = _load_lifecycle_module()


@pytest.fixture
def local_config_dir(tmp_path, monkeypatch):
    config_dir = tmp_path / ".cortext-harness" / "config"
    config_dir.mkdir(parents=True)
    monkeypatch.setattr(LIFECYCLE, "ROOT", tmp_path)
    return config_dir


def _write_config(config_dir: Path, name: str, payload: dict) -> Path:
    path = config_dir / f"{name}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class TestDoctorRemoteChecks:
    def test_no_remote_projects_returns_zero(self, local_config_dir):
        _write_config(
            local_config_dir, "local_a", {"project": {"code": "local_a"}},
        )
        with mock.patch.object(LIFECYCLE, "doctor_check", return_value=0) as check:
            failures = LIFECYCLE.doctor_remote_checks()
        assert failures == 0
        # The "remote projects" optional check is reported once.
        check.assert_called_once()
        args = check.call_args.args
        kwargs = check.call_args.kwargs
        assert args[0] == "remote projects"
        assert args[1] is True
        assert kwargs.get("required") is False

    def test_remote_project_reachable(self, local_config_dir):
        _write_config(
            local_config_dir,
            "remote_a",
            {
                "project": {"code": "remote_a"},
                "storage_backend": "remote",
                "remote": {
                    "qdrant_url": "http://qdrant.local:6333",
                    "falkordb_uri": "redis://falkor.local:6379",
                },
            },
        )
        probe_results = [
            ProbeResult("qdrant", "http://qdrant.local:6333", True, "reachable"),
            ProbeResult("falkordb", "redis://falkor.local:6379", True, "reachable"),
        ]
        from cortex_harness.storage import remote_probe as rp

        with mock.patch.object(rp, "probe_all", return_value=probe_results), mock.patch.object(
            LIFECYCLE, "doctor_check", return_value=0
        ):
            failures = LIFECYCLE.doctor_remote_checks()
        assert failures == 0

    def test_remote_project_unreachable_increments_failures(self, local_config_dir):
        _write_config(
            local_config_dir,
            "remote_a",
            {
                "project": {"code": "remote_a"},
                "storage_backend": "remote",
                "remote": {"qdrant_url": "http://qdrant.local:6333"},
            },
        )
        probe_results = [
            ProbeResult("qdrant", "http://qdrant.local:6333", False, "Connection refused"),
        ]
        from cortex_harness.storage import remote_probe as rp

        with mock.patch.object(rp, "probe_all", return_value=probe_results), mock.patch.object(
            LIFECYCLE, "doctor_check", return_value=1
        ):
            failures = LIFECYCLE.doctor_remote_checks()
        assert failures == 1

    def test_remote_project_partial_config_is_skipped(self, local_config_dir):
        """Backend with only qdrant_url configured skips the falkordb check."""
        _write_config(
            local_config_dir,
            "remote_a",
            {
                "project": {"code": "remote_a"},
                "storage_backend": "remote",
                "remote": {"qdrant_url": "http://qdrant.local:6333"},
            },
        )
        probe_results = [
            ProbeResult("qdrant", "http://qdrant.local:6333", True, "reachable"),
            ProbeResult("falkordb", "(not configured)", True, "skipped — no falkordb_uri"),
        ]
        from cortex_harness.storage import remote_probe as rp

        with mock.patch.object(rp, "probe_all", return_value=probe_results), mock.patch.object(
            LIFECYCLE, "doctor_check", return_value=0
        ) as check:
            failures = LIFECYCLE.doctor_remote_checks()
        assert failures == 0
        # Only qdrant check reported.
        names = [c.args[0] for c in check.call_args_list]
        assert "remote:remote_a:qdrant" in names
        assert "remote:remote_a:falkordb" not in names

    def test_force_local_bypass(self, local_config_dir, monkeypatch):
        _write_config(
            local_config_dir,
            "remote_a",
            {
                "project": {"code": "remote_a"},
                "storage_backend": "remote",
                "remote": {"qdrant_url": "http://qdrant.local:6333"},
            },
        )
        monkeypatch.setenv("CORTEX_STORAGE_BACKEND_FORCE_LOCAL", "1")
        with mock.patch.object(LIFECYCLE, "doctor_check", return_value=0) as check, mock.patch.object(
            LIFECYCLE, "_scan_project_backends"
        ) as scan:
            failures = LIFECYCLE.doctor_remote_checks()
        assert failures == 0
        scan.assert_not_called()
        check.assert_called_once()
        args = check.call_args.args
        assert args[0] == "remote backends"
        assert "bypassed" in args[2]

    def test_invalid_remote_config_reports_failure(self, local_config_dir):
        """A remote project missing both qdrant_url and falkordb_uri fails."""
        _write_config(
            local_config_dir,
            "remote_a",
            {
                "project": {"code": "remote_a"},
                "storage_backend": "remote",
                "remote": {},  # empty remote section
            },
        )
        with mock.patch.object(LIFECYCLE, "doctor_check", return_value=1):
            failures = LIFECYCLE.doctor_remote_checks()
        assert failures == 1


class TestFalkordbUiUrl:
    """URI → Browser UI URL derivation, mirroring the driver's URI parsing."""

    @pytest.mark.parametrize(
        "uri, expected",
        [
            ("localhost:6379", "http://localhost:3000"),
            ("redis://db.internal:6379", "http://db.internal:3000"),
            ("rediss://db.internal:6379", "http://db.internal:3000"),
            ("falkor://falkor.local:6379", "http://falkor.local:3000"),
            ("falkors://falkor.local:6379", "http://falkor.local:3000"),
            # Bare host with no port still yields a UI URL.
            ("db.internal", "http://db.internal:3000"),
            # Userinfo is stripped before the host is read.
            ("redis://user@db.internal:6379/0", "http://db.internal:3000"),
            ("redis://:pass@db.internal:6379", "http://db.internal:3000"),
            # IPv6 literals keep their brackets, with or without a port.
            ("[::1]:6379", "http://[::1]:3000"),
            ("[::1]", "http://[::1]:3000"),
            ("redis://[2001:db8::1]:6379", "http://[2001:db8::1]:3000"),
        ],
    )
    def test_derives_ui_url(self, uri, expected):
        assert LIFECYCLE._falkordb_ui_url(uri) == expected

    @pytest.mark.parametrize(
        "uri",
        [
            "unix:///tmp/falkor.sock",
            "ftp://db.internal:6379",
            "",
            "   ",
            # Empty host — must yield None rather than a malformed
            # "http://:6379:3000".
            ":6379",
            "redis://user@:6379/0",
            # Bare (unbracketed) IPv6 cannot be embedded in an http URL.
            "::1",
            # Unterminated / empty brackets.
            "[::1",
            "[]:6379",
        ],
    )
    def test_returns_none_when_host_is_underivable(self, uri):
        assert LIFECYCLE._falkordb_ui_url(uri) is None

    def test_respects_ui_port_env_override(self, monkeypatch):
        monkeypatch.setenv("FALKORDB_UI_PORT", "3001")
        assert LIFECYCLE._falkordb_ui_url("localhost:6379") == "http://localhost:3001"


class TestDoctorBrowserUiHint:
    """The reachable falkordb check advertises the Browser UI URL."""

    def _run_with_probes(self, config_dir, probe_results, falkordb_uri):
        _write_config(
            config_dir,
            "remote_a",
            {
                "project": {"code": "remote_a"},
                "storage_backend": "remote",
                "remote": {
                    "qdrant_url": "http://qdrant.local:6333",
                    "falkordb_uri": falkordb_uri,
                },
            },
        )
        from cortex_harness.storage import remote_probe as rp

        with mock.patch.object(rp, "probe_all", return_value=probe_results), mock.patch.object(
            LIFECYCLE, "doctor_check", return_value=0
        ) as check:
            LIFECYCLE.doctor_remote_checks()
        return {c.args[0]: c.args[2] for c in check.call_args_list}

    def test_reachable_falkordb_shows_browser_ui_url(self, local_config_dir):
        messages = self._run_with_probes(
            local_config_dir,
            [ProbeResult("falkordb", "localhost:6379", True, "reachable")],
            "localhost:6379",
        )
        assert "Browser UI: http://localhost:3000" in messages["remote:remote_a:falkordb"]

    def test_scheme_uri_is_parsed(self, local_config_dir):
        messages = self._run_with_probes(
            local_config_dir,
            [ProbeResult("falkordb", "redis://db.internal:6379", True, "reachable")],
            "redis://db.internal:6379",
        )
        assert "Browser UI: http://db.internal:3000" in messages["remote:remote_a:falkordb"]

    def test_ui_port_env_override(self, local_config_dir, monkeypatch):
        monkeypatch.setenv("FALKORDB_UI_PORT", "3001")
        messages = self._run_with_probes(
            local_config_dir,
            [ProbeResult("falkordb", "localhost:6379", True, "reachable")],
            "localhost:6379",
        )
        assert "Browser UI: http://localhost:3001" in messages["remote:remote_a:falkordb"]

    def test_unix_uri_has_no_ui_url(self, local_config_dir):
        messages = self._run_with_probes(
            local_config_dir,
            [ProbeResult("falkordb", "unix:///tmp/falkor.sock", True, "reachable")],
            "unix:///tmp/falkor.sock",
        )
        assert "Browser UI" not in messages["remote:remote_a:falkordb"]

    def test_unreachable_falkordb_has_no_ui_url(self, local_config_dir):
        """A failing check must not advertise a UI that is very likely down."""
        messages = self._run_with_probes(
            local_config_dir,
            [ProbeResult("falkordb", "localhost:6379", False, "refused")],
            "localhost:6379",
        )
        assert "Browser UI" not in messages["remote:remote_a:falkordb"]

    def test_qdrant_check_is_untouched(self, local_config_dir):
        messages = self._run_with_probes(
            local_config_dir,
            [
                ProbeResult("qdrant", "http://qdrant.local:6333", True, "reachable"),
                ProbeResult("falkordb", "localhost:6379", True, "reachable"),
            ],
            "localhost:6379",
        )
        assert messages["remote:remote_a:qdrant"] == "http://qdrant.local:6333 — reachable"


class TestDoctorCallerConfig:
    """Caller-aware scan: ``dev doctor`` must read the caller's project config."""

    def test_caller_remote_config_is_picked_up(self, tmp_path, monkeypatch):
        """Doctor finds remote project config from cwd even when ROOT has none."""
        caller_project = tmp_path / "my-project"
        caller_config = caller_project / ".cortext-harness" / "config"
        caller_config.mkdir(parents=True)
        _write_config(
            caller_config,
            "my_app",
            {
                "project": {"code": "my_app"},
                "storage_backend": "remote",
                "remote": {
                    "qdrant_url": "http://localhost:6333",
                    "falkordb_uri": "redis://localhost:6379",
                },
            },
        )

        # ROOT is a fresh directory with no configs; cwd points at the caller.
        fake_root = tmp_path / "cortex-harness"
        fake_root.mkdir()
        monkeypatch.setattr(LIFECYCLE, "ROOT", fake_root)
        monkeypatch.chdir(caller_project)

        probe_results = [
            ProbeResult("qdrant", "http://localhost:6333", True, "reachable"),
            ProbeResult("falkordb", "redis://localhost:6379", True, "reachable"),
        ]
        from cortex_harness.storage import remote_probe as rp

        with mock.patch.object(rp, "probe_all", return_value=probe_results), mock.patch.object(
            LIFECYCLE, "doctor_check", return_value=0
        ) as check:
            failures = LIFECYCLE.doctor_remote_checks()

        assert failures == 0
        names = [c.args[0] for c in check.call_args_list]
        assert "remote:my_app:qdrant" in names
        assert "remote:my_app:falkordb" in names

    def test_same_dir_no_double_scan(self, tmp_path, monkeypatch):
        """When cwd == ROOT, configs are scanned only once."""
        config_dir = tmp_path / ".cortext-harness" / "config"
        config_dir.mkdir(parents=True)
        _write_config(
            config_dir,
            "proj",
            {
                "project": {"code": "proj"},
                "storage_backend": "local",
            },
        )
        monkeypatch.setattr(LIFECYCLE, "ROOT", tmp_path)
        monkeypatch.chdir(tmp_path)

        projects = LIFECYCLE._scan_project_backends()
        assert len(projects) == 1
        assert projects[0]["project_id"] == "proj"