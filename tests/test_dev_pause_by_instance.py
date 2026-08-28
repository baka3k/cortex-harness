"""Tests for per-instance MCP pause scoping (Phase 03).

These tests prove that ``_mcp_stop_pattern(pattern, instance_id=...)``
only kills the MCP process whose ``CORTEX_STORAGE_INSTANCE`` matches the
target. Without this guard, ``dev sync <instance>`` would tear down
every running MCP process regardless of the instance that owns the
embedded store.
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

from cortex_harness.dev import (
    MCP_LOG_DIR,
    _legacy_pause_by_instance_disabled,
    _mcp_pids,
    _mcp_pid_sidecar_path,
    _mcp_stop_pattern,
    _pid_instance_id,
    _resolve_storage_instance,
)


def _write_sidecar(name: str, instance_id: str, pid: int) -> Path:
    """Write a per-instance pid sidecar and return the path."""
    MCP_LOG_DIR.mkdir(parents=True, exist_ok=True)
    sidecar = _mcp_pid_sidecar_path(name, instance_id)
    sidecar.write_text(f"pid={pid}\ninstance_id={instance_id}\n")
    return sidecar


class PauseByInstanceTests(unittest.TestCase):
    def setUp(self):
        # Use a temp log dir so the test does not pick up real sidecars
        # from the developer machine.
        self._tmp_log = mock.patch(
            "cortex_harness.dev.MCP_LOG_DIR",
            new=Path(tempfile_mkdtemp()),
        )
        self._tmp_log.start()
        self.addCleanup(self._tmp_log.stop)

    def tearDown(self):
        # Remove any sidecar files we created in the temp dir.
        for path in MCP_LOG_DIR.glob("dev-mcp-*-*.pid"):
            try:
                path.unlink()
            except OSError:
                pass

    def test_resolve_storage_instance_defaults_to_default(self):
        self.assertEqual(_resolve_storage_instance({}), "default")

    def test_resolve_storage_instance_reads_process_env(self):
        with mock.patch.dict(
            "cortex_harness.dev.os.environ",
            {"CORTEX_STORAGE_INSTANCE": "alpha"},
        ):
            self.assertEqual(_resolve_storage_instance({}), "alpha")

    def test_legacy_pause_gate_default_is_off(self):
        with mock.patch.dict("cortex_harness.dev.os.environ", {}, clear=False):
            os_environ = mock.patch.dict(
                "cortex_harness.dev.os.environ",
                {},
                clear=True,
            )
            with os_environ:
                self.assertFalse(_legacy_pause_by_instance_disabled())

    def test_legacy_pause_gate_toggled_by_zero(self):
        with mock.patch.dict(
            "cortex_harness.dev.os.environ",
            {"CORTEX_MCP_PAUSE_BY_INSTANCE": "0"},
        ):
            self.assertTrue(_legacy_pause_by_instance_disabled())

    def test_pid_instance_id_from_sidecar(self):
        sidecar = _write_sidecar("code-tiny", "alpha", 12345)
        try:
            self.assertEqual(_pid_instance_id(12345), "alpha")
        finally:
            sidecar.unlink()

    def test_pid_instance_id_returns_none_for_unknown_pid(self):
        self.assertIsNone(_pid_instance_id(999_999_999))

    @unittest.skipIf(sys.platform == "win32", "POSIX ps only")
    def test_mcp_pids_filters_by_instance(self):
        # Two sidecars with different instance ids.
        _write_sidecar("code-tiny", "alpha", 1001)
        _write_sidecar("code-tiny", "beta", 1002)
        # Inject a fake ``ps`` response that returns both PIDs with the
        # expected command line shape; the sidecar lookup runs first and
        # the env read is never consulted in the happy path.
        fake_ps = "1001 python code-tiny/mcp/unified_mcp.py\n1002 python code-tiny/mcp/unified_mcp.py\n"
        with mock.patch(
            "cortex_harness.dev.subprocess.run",
            return_value=mock.Mock(stdout=fake_ps, returncode=0),
        ):
            alpha_pids = _mcp_pids("unified_mcp.py", instance_id="alpha")
            beta_pids = _mcp_pids("unified_mcp.py", instance_id="beta")
            legacy_pids = _mcp_pids("unified_mcp.py")  # no filter

        self.assertEqual(alpha_pids, [1001])
        self.assertEqual(beta_pids, [1002])
        self.assertEqual(sorted(legacy_pids), [1001, 1002])

    @unittest.skipIf(sys.platform == "win32", "POSIX ps only")
    def test_mcp_pids_legacy_escape_hatch(self):
        _write_sidecar("code-tiny", "alpha", 1001)
        _write_sidecar("code-tiny", "beta", 1002)
        fake_ps = "1001 python code-tiny/mcp/unified_mcp.py\n1002 python code-tiny/mcp/unified_mcp.py\n"
        with mock.patch.dict(
            "cortex_harness.dev.os.environ",
            {"CORTEX_MCP_PAUSE_BY_INSTANCE": "0"},
        ), mock.patch(
            "cortex_harness.dev.subprocess.run",
            return_value=mock.Mock(stdout=fake_ps, returncode=0),
        ):
            pids = _mcp_pids("unified_mcp.py", instance_id="alpha")

        # Legacy mode: every pattern-matching PID is returned regardless
        # of instance id.
        self.assertEqual(sorted(pids), [1001, 1002])

    @unittest.skipIf(sys.platform == "win32", "POSIX ps only")
    def test_mcp_stop_pattern_only_signals_target_instance(self):
        _write_sidecar("code-tiny", "alpha", 1001)
        _write_sidecar("code-tiny", "beta", 1002)
        fake_ps = "1001 python code-tiny/mcp/unified_mcp.py\n1002 python code-tiny/mcp/unified_mcp.py\n"
        signaled: set[int] = set()

        def fake_run(cmd, *args, **kwargs):
            if isinstance(cmd, list) and cmd and cmd[0] == "kill":
                signaled.add(int(cmd[2]))
                return mock.Mock(returncode=0)
            return mock.Mock(stdout=fake_ps, returncode=0)

        with mock.patch(
            "cortex_harness.dev.subprocess.run",
            side_effect=fake_run,
        ):
            _mcp_stop_pattern("unified_mcp.py", instance_id="alpha")

        # Only the alpha PID was ever signaled, regardless of TERM/KILL
        # sequence.
        self.assertEqual(signaled, {1001})


def tempfile_mkdtemp():
    import tempfile
    return Path(tempfile.mkdtemp(prefix="cortex-mcp-locks-test-"))


if __name__ == "__main__":
    unittest.main()
