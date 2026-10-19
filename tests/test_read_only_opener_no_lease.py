"""No-lease contract for the RT-3 bounded read-only opener.

Pins the bounded exception from
``docs/plans/261019-1126-link-tiny-doc-code-traceability``: opening graphs
through ``code-tiny/tools/graph/read_only_opener.py`` must acquire ZERO
``StorageLease`` — the link engine reads both embedded stores while the
owning MCP servers / sync writers keep their exclusive leases. Mirrors
``tests/test_mcp_sibling_no_lease.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.graph.driver import falkordb_driver, ladybug_driver  # noqa: E402
from tools.graph.read_only_opener import open_read_only_graph  # noqa: E402


class _RecordingLease:
    def __init__(self) -> None:
        self.acquire_calls = 0

    def acquire(self):
        self.acquire_calls += 1
        return self


class _FakeResultSet:
    # falkordblite header items are plain strings
    header = ["n", "count(n)"]
    result_set = [[{"name": "login_handler"}, 1]]


class _FakeGraph:
    def query(self, query, params=None):
        assert "MATCH" in query
        return _FakeResultSet()


class _FakeClient:
    def select_graph(self, name):
        return _FakeGraph()

    def list_graphs(self):
        return []

    def close(self):
        return None


@pytest.fixture
def recording_lease(monkeypatch):
    recorder = _RecordingLease()
    monkeypatch.setattr(
        "tools.graph.driver.falkordb_driver.StorageLease.acquire",
        recorder.acquire,
    )
    monkeypatch.setattr(
        "tools.graph.driver.ladybug_driver.StorageLease.acquire",
        recorder.acquire,
    )
    return recorder


def test_falkordb_open_acquires_no_lease(recording_lease, tmp_path: Path) -> None:
    store = tmp_path / "code.rdb"
    store.touch()
    with patch.object(
        falkordb_driver, "_open_local_falkordb", return_value=_FakeClient()
    ):
        handle = open_read_only_graph(
            backend="falkordb", path=str(store), graph_name="fixt"
        )
    try:
        assert recording_lease.acquire_calls == 0
        records = handle.run("MATCH (n) RETURN n, count(n)")
        assert records == [{"n": {"name": "login_handler"}, "count(n)": 1}]
    finally:
        handle.close()


def test_ladybug_open_acquires_no_lease(recording_lease, tmp_path: Path) -> None:
    store = tmp_path / "doc.ladybug"
    store.touch()
    with patch.object(
        ladybug_driver.LadybugDBDriver,
        "_open_database",
        return_value=(object(), _FakeLadybugConnection()),
    ) as open_database:
        handle = open_read_only_graph(
            backend="ladybug", path=str(store), graph_name="fixt_doc"
        )
    try:
        assert recording_lease.acquire_calls == 0
        # read-only open reaches the driver's read_only path
        assert open_database.call_args.kwargs.get("read_only") is True
        records = handle.run("MATCH (p:Paragraph) RETURN p")
        assert records == [{"p": {"source_id": "SPEC.md"}}]
    finally:
        handle.close()


class _FakeLadybugResult:
    def get_column_names(self):
        return ["p"]

    def has_next(self):
        return not getattr(self, "_done", False)

    def get_next(self):
        self._done = True
        return [{"source_id": "SPEC.md"}]


class _FakeLadybugConnection:
    def execute(self, query, params=None):
        return _FakeLadybugResult()

    def close(self):
        return None


def test_open_requires_path_or_uri(recording_lease) -> None:
    with pytest.raises(Exception, match="path= or uri="):
        open_read_only_graph(backend="falkordb", graph_name="x")
    assert recording_lease.acquire_calls == 0


def test_open_rejects_neo4j(recording_lease) -> None:
    with pytest.raises(Exception, match="neo4j"):
        open_read_only_graph(backend="neo4j", graph_name="x")
