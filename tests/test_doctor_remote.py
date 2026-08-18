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