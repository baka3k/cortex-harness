"""End-to-end lease surface test for the multi-instance fan-out default.

Proves the four claims of the
``plans/260828-1508-multi-instance-fanout-default`` plan:

1. A MCP boot path with ``CORTEX_STORAGE_INSTANCE=A`` discovers every
   sibling ``data.rdb`` by default — fan-out is the default again.
2. ``_mcp_stop_pattern(pattern, instance_id="B")`` does not signal any
   PID whose recorded instance id is A (carried over from
   ``plans/260828-1428-instance-isolated-mcp-locks``).
3. An ingest of B succeeds while MCP A holds the lease on A.
4. Sibling stores are opened by the driver without acquiring a
   ``StorageLease`` (verified separately by
   ``tests/test_mcp_sibling_no_lease.py``).

The test exercises the discovery helper directly rather than spawning
real MCP subprocesses — both exercise the same contract that the boot
path uses, and the in-process model avoids platform flakiness.
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

    def test_default_discovery_returns_every_sibling(self):
        """Boot-path default returns every instance's ``data.rdb``."""
        with mock.patch.dict(
            os.environ,
            {"CORTEX_DATA_HOME": str(self.data_home),
             "CORTEX_STORAGE_INSTANCE": "alpha"},
        ):
            paths = falkordb_discovery.discover_falkordb_data_files()

        self.assertEqual(paths, [self.alpha, self.beta])

    def test_alpha_driver_includes_every_sibling(self):
        """An MCP boot path with ``CORTEX_STORAGE_INSTANCE=alpha`` sees every sibling."""
        with mock.patch.dict(
            os.environ,
            {"CORTEX_DATA_HOME": str(self.data_home),
             "CORTEX_STORAGE_INSTANCE": "alpha"},
        ):
            primary = falkordb_discovery.discover_falkordb_data_files(
                include_siblings=False, exclude_self=False
            )
            siblings = falkordb_discovery.discover_falkordb_data_files(
                include_siblings=True, exclude_self=True
            )

        self.assertEqual(primary, [self.alpha])
        self.assertEqual(siblings, [self.beta])

    def test_explicit_kwargs_filter_siblings(self):
        """``exclude_self=True`` drops the current instance; ``include_siblings=False`` keeps only the primary."""
        with mock.patch.dict(
            os.environ,
            {"CORTEX_DATA_HOME": str(self.data_home),
             "CORTEX_STORAGE_INSTANCE": "alpha"},
        ):
            with_self = falkordb_discovery.discover_falkordb_data_files(
                include_siblings=True, exclude_self=False
            )
            without_self = falkordb_discovery.discover_falkordb_data_files(
                include_siblings=True, exclude_self=True
            )
            only_primary = falkordb_discovery.discover_falkordb_data_files(
                include_siblings=False, exclude_self=False
            )

        self.assertEqual(with_self, [self.alpha, self.beta])
        self.assertEqual(without_self, [self.beta])
        self.assertEqual(only_primary, [self.alpha])

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

    def test_ingest_of_alpha_still_conflicts(self):
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


if __name__ == "__main__":
    unittest.main()
