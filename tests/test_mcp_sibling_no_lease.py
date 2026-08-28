"""Driver-level no-lease contract for sibling FalkorDB stores.

Pins the multi-instance fan-out default from
``plans/260828-1508-multi-instance-fanout-default``: the
``FalkorDBDriver`` constructor must acquire exactly one
``StorageLease`` (the primary) regardless of how many sibling paths
are passed in ``additional_paths``. Any regression that re-introduces
the per-sibling lease is caught at CI time.
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path
from unittest.mock import patch

import pytest


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.graph.driver.falkordb_driver import (  # noqa: E402
    FalkorDBDriver,
    _open_local_falkordb,
)


class _RecordingLease:
    """Stand-in for ``StorageLease.acquire`` that counts calls."""

    def __init__(self) -> None:
        self.acquire_calls = 0
        self.released = False

    def acquire(self):
        self.acquire_calls += 1
        return self

    def release(self) -> None:
        self.released = True


@pytest.fixture
def recording_lease(monkeypatch):
    """Replace ``StorageLease.acquire`` with a counting stub."""
    recorder = _RecordingLease()
    # Patch the symbol that the driver resolved at import time.
    monkeypatch.setattr(
        "tools.graph.driver.falkordb_driver.StorageLease.acquire",
        recorder.acquire,
    )
    return recorder


def _fake_client():
    class _Stub:
        list_graphs = lambda self: []  # noqa: E731
        select_graph = lambda self, name: object()  # noqa: E731

        def close(self) -> None:
            return None

    return _Stub()


def test_no_lease_for_single_sibling(recording_lease, tmp_path: Path) -> None:
    primary = tmp_path / "alpha.rdb"
    sibling = tmp_path / "beta.rdb"
    for path in (primary, sibling):
        path.touch()

    with patch(
        "tools.graph.driver.falkordb_driver._open_local_falkordb",
        return_value=_fake_client(),
    ), patch.object(FalkorDBDriver, "_graph_for", return_value=object()), warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        driver = FalkorDBDriver(
            path=primary,
            graph="hyper_graph",
            additional_paths=[sibling],
        )

    try:
        # Exactly one lease call — for the primary path.
        assert recording_lease.acquire_calls == 1
        # The sibling client was opened and registered.
        assert len(driver._additional_clients) == 1
        assert sibling.resolve() in driver._additional_open_paths
        # The legacy leases list must not exist (replaced by open paths).
        assert not hasattr(driver, "_additional_storage_leases") or not getattr(
            driver, "_additional_storage_leases", []
        )
    finally:
        driver.close()


def test_no_lease_for_multiple_siblings(recording_lease, tmp_path: Path) -> None:
    primary = tmp_path / "alpha.rdb"
    siblings = [tmp_path / f"{name}.rdb" for name in ("beta", "gamma", "delta")]
    for path in [primary, *siblings]:
        path.touch()

    with patch(
        "tools.graph.driver.falkordb_driver._open_local_falkordb",
        return_value=_fake_client(),
    ), patch.object(FalkorDBDriver, "_graph_for", return_value=object()), warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        driver = FalkorDBDriver(
            path=primary,
            graph="hyper_graph",
            additional_paths=siblings,
        )

    try:
        assert recording_lease.acquire_calls == 1
        assert len(driver._additional_clients) == len(siblings)
    finally:
        driver.close()


def test_no_lease_when_additional_paths_empty(recording_lease, tmp_path: Path) -> None:
    primary = tmp_path / "alpha.rdb"
    primary.touch()

    with patch(
        "tools.graph.driver.falkordb_driver._open_local_falkordb",
        return_value=_fake_client(),
    ), patch.object(FalkorDBDriver, "_graph_for", return_value=object()), warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        driver = FalkorDBDriver(
            path=primary,
            graph="hyper_graph",
            additional_paths=[],
        )

    try:
        # One call for the primary; no siblings to lease.
        assert recording_lease.acquire_calls == 1
        assert driver._additional_clients == []
    finally:
        driver.close()
