"""Tests for the FalkorDB driver local-mode constructor."""

from __future__ import annotations

import sys
import warnings
from pathlib import Path
from unittest.mock import patch

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
                password="p",
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
        def __init__(self, path: str) -> None:
            captured["path"] = path
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


def _make_fake_client() -> object:
    """Return a minimal fake backend that satisfies the driver's init contract."""

    class _FakeClient:
        def close(self) -> None:
            return None

        def select_graph(self, name: str) -> object:
            return object()

    return _FakeClient()
