"""Tests for the graph-write optimization changes:

1. ``write_relations_typed`` groups by (source_label, target_label, rel_type)
   and emits labeled MATCH queries that hit indexes.
2. ``write_relations`` groups by (source_label, target_label).
3. Rows without labels fall back to unlabeled MATCH.
4. ``FalkorDBDriver.execute_query`` runs in a thread executor (real async).
5. ``FalkorDBDriver.execute_queries_pipelined`` batches commands in one pipeline.

Run from repo root::

    PYTHONPATH=code-tiny python -m pytest tests/test_graph_write_optimization.py -v
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CODE_TINY = os.path.join(REPO_ROOT, "code-tiny")
if CODE_TINY not in sys.path:
    sys.path.insert(0, CODE_TINY)


_CODE_TINY = Path(__file__).resolve().parents[1] / "code-tiny"
_LANGUAGE_WRITER_PATH = _CODE_TINY / "tools" / "graph" / "writer" / "language_writer.py"
_PROJECT_SCOPE_PATH = _CODE_TINY / "tools" / "common" / "project_scope.py"


def _load_module(name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(name, file_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_load_module("tools.common.project_scope", _PROJECT_SCOPE_PATH)
language_writer = _load_module("language_writer_opt_test", _LANGUAGE_WRITER_PATH)


class _RecordingDriver:
    """Captures every (query, params, database) tuple for assertions."""

    def __init__(self) -> None:
        self.calls = []

    async def execute_query(self, query, parameters=None, database=None):
        self.calls.append((query, dict(parameters or {}), database))
        return ([{"count": 0}], [], None)


class WriteRelationsTypedLabeledMatchTests(unittest.IsolatedAsyncioTestCase):
    """Verify write_relations_typed emits labeled MATCH when labels are present."""

    async def test_labeled_match_used_when_labels_present(self) -> None:
        driver = _RecordingDriver()
        writer = language_writer.LanguageCodeWriter(driver, database="db")
        relations = [
            {
                "source_label": "Namespace",
                "target_label": "Function",
                "rel_type": "DECLARES",
                "source_id": "ns-1",
                "target_id": "fn-1",
                "properties": {},
            },
            {
                "source_label": "Type",
                "target_label": "Function",
                "rel_type": "DECLARES",
                "source_id": "type-1",
                "target_id": "fn-2",
                "properties": {},
            },
        ]
        await writer.write_relations_typed(relations)

        # Two groups: (Namespace, Function, DECLARES) and (Type, Function, DECLARES)
        self.assertEqual(len(driver.calls), 2)

        queries = " ".join(q for q, _, _ in driver.calls)
        self.assertIn("MATCH (a:Namespace {id: row.source_id})", queries)
        self.assertIn("(b:Function {id: row.target_id})", queries)
        self.assertIn("MATCH (a:Type {id: row.source_id})", queries)

    async def test_unlabeled_fallback_when_labels_missing(self) -> None:
        driver = _RecordingDriver()
        writer = language_writer.LanguageCodeWriter(driver, database="db")
        relations = [
            {
                "source_id": "a-1",
                "target_id": "b-1",
                "rel_type": "CONTAINS",
                "properties": {},
            },
        ]
        await writer.write_relations_typed(relations)

        self.assertEqual(len(driver.calls), 1)
        query = driver.calls[0][0]
        self.assertIn("MATCH (a {id: row.source_id})", query)
        self.assertNotIn("a:", query)

    async def test_same_rel_type_different_labels_creates_separate_queries(self) -> None:
        driver = _RecordingDriver()
        writer = language_writer.LanguageCodeWriter(driver, database="db")
        relations = [
            {
                "source_label": "Namespace",
                "target_label": "Type",
                "rel_type": "CONTAINS",
                "source_id": "ns-1",
                "target_id": "type-1",
                "properties": {},
            },
            {
                "source_label": "File",
                "target_label": "Function",
                "rel_type": "CONTAINS",
                "source_id": "file-1",
                "target_id": "fn-1",
                "properties": {},
            },
        ]
        await writer.write_relations_typed(relations)

        # Two separate queries, one per label pair
        self.assertEqual(len(driver.calls), 2)
        q0 = driver.calls[0][0]
        q1 = driver.calls[1][0]
        self.assertIn("a:Namespace", q0)
        self.assertIn("a:File", q1)

    async def test_project_contains_edges_filtered(self) -> None:
        driver = _RecordingDriver()
        writer = language_writer.LanguageCodeWriter(driver, database="db")
        relations = [
            {
                "source_label": "Project",
                "target_label": "File",
                "rel_type": "CONTAINS",
                "source_id": "proj-1",
                "target_id": "file-1",
                "properties": {},
            },
            {
                "source_label": "Type",
                "target_label": "Function",
                "rel_type": "DECLARES",
                "source_id": "type-1",
                "target_id": "fn-1",
                "properties": {},
            },
        ]
        result = await writer.write_relations_typed(relations)
        # Only the DECLARES edge should be written; CONTAINS from Project is filtered
        self.assertEqual(len(driver.calls), 1)
        self.assertIn("a:Type", driver.calls[0][0])


class WriteRelationsLabeledMatchTests(unittest.IsolatedAsyncioTestCase):
    """Verify write_relations groups by label pair."""

    async def test_labeled_match_used(self) -> None:
        driver = _RecordingDriver()
        writer = language_writer.LanguageCodeWriter(driver, database="db")
        relations = [
            {
                "source_label": "File",
                "target_label": "File",
                "rel_type": "INCLUDES",
                "source_id": "file-a",
                "target_id": "file-b",
                "properties": {},
            },
        ]
        await writer.write_relations(relations)

        # The first call will try APOC and fail, then try fallback.
        # Both should use labeled MATCH.
        for query, _, _ in driver.calls:
            self.assertIn("MATCH (source:File {id: row.source_id})", query)

    async def test_unlabeled_fallback(self) -> None:
        driver = _RecordingDriver()
        writer = language_writer.LanguageCodeWriter(driver, database="db")
        relations = [
            {
                "source_id": "a-1",
                "target_id": "b-1",
                "rel_type": "CONTAINS",
                "properties": {},
            },
        ]
        await writer.write_relations(relations)

        for query, _, _ in driver.calls:
            self.assertIn("MATCH (source {id: row.source_id})", query)
            self.assertNotIn("source:", query)


# ---------------------------------------------------------------------------
# FalkorDB driver async + pipelining tests
# ---------------------------------------------------------------------------

from tools.graph.core.base import GraphProvider
from tools.graph.core.factory import GraphDriverFactory


class FakeNode:
    labels = ["Function"]

    def __init__(self, properties):
        self.properties = properties


class FakeQueryResult:
    header = [[8, "count"]]

    def __init__(self):
        self.result_set = [[1]]


class FakeGraph:
    name = "test_graph"

    def __init__(self):
        self.calls = []
        self.client = Mock()

    def query(self, query, params=None):
        self.calls.append((query, params))
        return FakeQueryResult()

    def _build_params_header(self, params):
        if not params:
            return ""
        parts = []
        for k, v in params.items():
            if isinstance(v, str):
                parts.append(f"`{k}`='{v}'")
            elif isinstance(v, list):
                parts.append(f"`{k}`=[{','.join(str(x) for x in v)}]")
            else:
                parts.append(f"`{k}`={v}")
        return "CYPHER " + " ".join(parts) + " "


class FakeFalkorDB:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.selected = []
        self.graph = FakeGraph()

    @classmethod
    def from_url(cls, url, **kwargs):
        instance = cls(**kwargs)
        instance.url = url
        return instance

    def select_graph(self, graph):
        self.selected.append(graph)
        return self.graph

    def list_graphs(self):
        return ["test_graph"]

    def close(self):
        self.closed = True


class FalkorDBAsyncAndPipeliningTests(unittest.IsolatedAsyncioTestCase):

    async def test_execute_query_runs_in_executor(self) -> None:
        """execute_query should not block the event loop."""
        with patch("falkordb.FalkorDB", FakeFalkorDB):
            driver = await GraphDriverFactory.create_driver(
                GraphProvider.FALKORDB,
                {"host": "localhost", "port": 6379, "database": "test_graph"},
            )

        async def _check_concurrent():
            """Run execute_query and verify another coroutine runs concurrently."""
            flag = {"ran": False}

            async def _marker():
                await asyncio.sleep(0.01)
                flag["ran"] = True

            # Patch execute_query_sync to simulate blocking I/O
            original = driver.execute_query_sync

            def _blocking_sync(*args, **kwargs):
                import time
                time.sleep(0.05)
                return original(*args, **kwargs)

            driver.execute_query_sync = _blocking_sync

            task1 = asyncio.create_task(driver.execute_query("MATCH (n) RETURN n"))
            task2 = asyncio.create_task(_marker())
            await task1
            await task2

            self.assertTrue(flag["ran"], "execute_query blocked the event loop")

            # Restore
            driver.execute_query_sync = original

    async def test_pipelined_method_returns_list_of_results(self) -> None:
        """execute_queries_pipelined should return one result per query."""
        with patch("falkordb.FalkorDB", FakeFalkorDB):
            driver = await GraphDriverFactory.create_driver(
                GraphProvider.FALKORDB,
                {"host": "localhost", "port": 6379, "database": "test_graph"},
            )

        # Set up fake pipeline that returns 2 raw responses (one per query)
        fake_pipe = Mock()
        fake_pipe.execute.return_value = [
            [["Labels added: 1"]],   # raw response for query 1
            [["Labels added: 1"]],   # raw response for query 2
        ]
        driver.graph.client.pipeline.return_value = fake_pipe

        # Mock QueryResult to handle raw responses
        with patch("falkordb.QueryResult") as MockQR:
            mock_qr = Mock()
            mock_qr.header = [[8, "count"]]
            mock_qr.result_set = [[1]]
            MockQR.return_value = mock_qr

            results = driver.execute_queries_pipelined(
                [
                    ("MERGE (n:Test {id: 'a'})", None),
                    ("MERGE (n:Test {id: 'b'})", None),
                ]
            )

        self.assertEqual(len(results), 2)
        self.assertEqual(fake_pipe.execute_command.call_count, 2)

    async def test_pipelined_empty_input_returns_empty_list(self) -> None:
        with patch("falkordb.FalkorDB", FakeFalkorDB):
            driver = await GraphDriverFactory.create_driver(
                GraphProvider.FALKORDB,
                {"host": "localhost", "port": 6379, "database": "test_graph"},
            )

        results = driver.execute_queries_pipelined([])
        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
