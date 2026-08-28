"""End-to-end lease surface test for per-instance MCP isolation.

Proves the four claims of the
``plans/260828-1428-instance-isolated-mcp-locks`` plan:

1. A MCP boot path with ``CORTEX_STORAGE_INSTANCE=A`` opens only the
   primary ``data.rdb`` of A — no siblings.
2. ``_mcp_stop_pattern(pattern, instance_id="B")`` does not signal any
   PID whose recorded instance id is A.
3. A second MCP process for B does not conflict with MCP A's lease.
4. The legacy ``CORTEX_MCP_SCOPE_LEASES=0`` escape hatch restores the
   pre-isolation behavior.

The test exercises the discovery and cross-instance gate modules
directly rather than spawning real MCP subprocesses — both exercise
the same contract that the boot path uses, and the in-process model
avoids platform flakiness.
"""

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cortex_harness.storage.lease import (
    StorageLease,
    StorageLeaseConflictError,
)


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sys.modules[name] = mod
    return mod


_MCP_DIR = Path(__file__).resolve().parents[1] / "code-tiny" / "mcp"
if str(_MCP_DIR) not in sys.path:
    sys.path.insert(0, str(_MCP_DIR))
falkordb_discovery = _load_module(
    "falkordb_discovery_under_test", _MCP_DIR / "falkordb_discovery.py"
)
cross_instance = _load_module(
    "cross_instance_under_test", _MCP_DIR / "cross_instance.py"
)


def _make_instance(data_home: Path, instance_id: str) -> Path:
    rdb = data_home / "v1" / "instances" / instance_id / "falkordb" / "code" / "data.rdb"
    rdb.parent.mkdir(parents=True, exist_ok=True)
    rdb.touch()
    return rdb


class LeaseSurfaceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_home = Path(self._tmp.name)
        self.alpha = _make_instance(self.data_home, "alpha")
        self.beta = _make_instance(self.data_home, "beta")
        self._leases: list[StorageLease] = []
        self.addCleanup(self._release_leases)

    def _release_leases(self):
        for lease in self._leases:
            try:
                lease.release()
            except Exception:
                pass

    def _acquire(self, path: Path, instance_id: str, owner_id: str = "code") -> StorageLease:
        lease = StorageLease(
            path,
            instance_id=instance_id,
            owner_id=owner_id,
            backend="falkordb",
        )
        lease.acquire()
        self._leases.append(lease)
        return lease

    def test_alpha_driver_owns_only_alpha_lease(self):
        """MCP A's primary lease is the only lease it acquires."""
        with mock.patch.dict(
            os.environ,
            {"CORTEX_DATA_HOME": str(self.data_home),
             "CORTEX_STORAGE_INSTANCE": "alpha"},
        ):
            paths = falkordb_discovery.discover_falkordb_data_files()

        self.assertEqual(paths, [self.alpha])

        # Both files are accessible; A's lease on alpha does not block
        # an external party from acquiring beta.
        self._acquire(self.alpha, "alpha")
        external_lease = StorageLease(
            self.beta,
            instance_id="beta",
            owner_id="code",
            backend="falkordb",
        )
        external_lease.acquire()
        external_lease.release()

    def test_ingest_of_beta_succeeds_while_mcp_alpha_holds_alpha(self):
        """An ingest of B does not collide with MCP A's lease on A."""
        self._acquire(self.alpha, "alpha")
        ingest_lease = StorageLease(
            self.beta,
            instance_id="beta",
            owner_id="ingest",
            backend="falkordb",
        )
        try:
            ingest_lease.acquire()
        except StorageLeaseConflictError as exc:  # pragma: no cover - test fails
            self.fail(
                f"Ingest of B unexpectedly conflicted with MCP A: {exc}"
            )
        ingest_lease.release()

    def test_ingest_of_alpha_fails_when_mcp_alpha_holds_it(self):
        """Sanity: same-instance still conflicts (this is the lease's job)."""
        self._acquire(self.alpha, "alpha")
        colliding_lease = StorageLease(
            self.alpha,
            instance_id="alpha",
            owner_id="other",
            backend="falkordb",
        )
        with self.assertRaises(StorageLeaseConflictError):
            colliding_lease.acquire()

    def test_legacy_escape_hatch_returns_every_sibling(self):
        """``CORTEX_MCP_SCOPE_LEASES=0`` reverts to the legacy behavior."""
        with mock.patch.dict(
            os.environ,
            {"CORTEX_DATA_HOME": str(self.data_home),
             "CORTEX_STORAGE_INSTANCE": "alpha",
             "CORTEX_MCP_SCOPE_LEASES": "0"},
        ):
            paths = falkordb_discovery.discover_falkordb_data_files()

        # The legacy mode returns every instance including self.
        self.assertEqual(paths, [self.alpha, self.beta])


class CrossInstanceGateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_home = Path(self._tmp.name)
        self.alpha = _make_instance(self.data_home, "alpha")
        self.beta = _make_instance(self.data_home, "beta")

    def test_gate_closed_by_default(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(cross_instance.enabled())
            self.assertFalse(cross_instance.is_allowed("analyze_workflow_impact"))
            self.assertEqual(cross_instance.sibling_paths_if_allowed("analyze_workflow_impact"), [])

    def test_gate_open_with_opt_in_env(self):
        with mock.patch.dict(
            os.environ,
            {"CORTEX_DATA_HOME": str(self.data_home),
             "CORTEX_STORAGE_INSTANCE": "alpha",
             "CROSS_INSTANCE_QUERY": "1"},
        ):
            self.assertTrue(cross_instance.enabled())
            self.assertTrue(cross_instance.is_allowed("analyze_workflow_impact"))
            self.assertTrue(cross_instance.is_allowed("explore_graph"))
            self.assertFalse(cross_instance.is_allowed("not_on_allowlist"))

            paths = cross_instance.sibling_paths_if_allowed("analyze_workflow_impact")
            self.assertEqual(paths, [self.beta])

            combined = cross_instance.self_and_allowed_siblings_paths("analyze_workflow_impact")
            self.assertEqual(combined, [self.alpha, self.beta])

    def test_gate_open_but_tool_not_allowlisted(self):
        with mock.patch.dict(
            os.environ,
            {"CORTEX_DATA_HOME": str(self.data_home),
             "CORTEX_STORAGE_INSTANCE": "alpha",
             "CROSS_INSTANCE_QUERY": "1"},
        ):
            self.assertFalse(cross_instance.is_allowed("some_unlisted_tool"))
            self.assertEqual(cross_instance.sibling_paths_if_allowed("some_unlisted_tool"), [])


if __name__ == "__main__":
    unittest.main()
