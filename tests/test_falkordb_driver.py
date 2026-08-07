import asyncio
import os
import subprocess
import sys
import threading
import time
import textwrap
import unittest
from unittest.mock import AsyncMock, Mock, patch


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CODE_TINY = os.path.join(REPO_ROOT, "code-tiny")
if CODE_TINY not in sys.path:
    sys.path.insert(0, CODE_TINY)

from tools.graph.core.base import GraphProvider
from tools.graph.core.factory import GraphDriverFactory


class FakeNode:
    labels = ["Function"]

    def __init__(self, properties):
        self.properties = properties


class FakeRelationship:
    relation = "CALLS"
    src_node = 1
    dest_node = 2

    def __init__(self, properties):
        self.properties = properties


class FakeQueryResult:
    header = [[8, "n"], [7, "r"], [3, "count"]]

    def __init__(self):
        self.result_set = [
            [
                FakeNode({"id": "fn-1", "name": "main"}),
                FakeRelationship({"weight": 1.0}),
                1,
            ]
        ]


class FakeGraph:
    name = "test_graph"

    def __init__(self):
        self.calls = []
        self.timeouts = []

    def query(self, query, params=None, timeout=None):
        self.calls.append((query, params))
        self.timeouts.append(timeout)
        return FakeQueryResult()


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


class FalkorDBDriverTests(unittest.IsolatedAsyncioTestCase):
    async def test_factory_creates_falkordb_driver(self):
        with patch("falkordb.FalkorDB", FakeFalkorDB):
            driver = await GraphDriverFactory.create_driver(
                GraphProvider.FALKORDB,
                {
                    "uri": "falkor://localhost:6379",
                    "database": "test_graph",
                    "user": "default",
                    "password": "secret",
                },
            )

        self.assertEqual(driver.provider, GraphProvider.FALKORDB)
        self.assertEqual(driver.database, "test_graph")

    async def test_execute_query_normalizes_falkordb_rows_to_dict_records(self):
        with patch("falkordb.FalkorDB", FakeFalkorDB):
            driver = await GraphDriverFactory.create_driver(
                GraphProvider.FALKORDB,
                {"host": "localhost", "port": 6379, "database": "test_graph"},
            )

        records, keys, summary = driver.execute_query_sync(
            "MATCH (n) RETURN n",
            {"limit": 1},
        )

        self.assertEqual(keys, ["n", "r", "count"])
        self.assertEqual(records[0]["n"], {"id": "fn-1", "name": "main"})
        self.assertEqual(records[0]["r"]["_type"], "CALLS")
        self.assertEqual(records[0]["r"]["_start_id"], 1)
        self.assertEqual(records[0]["r"]["_end_id"], 2)
        self.assertEqual(records[0]["count"], 1)
        self.assertIsInstance(summary, FakeQueryResult)

    async def test_execute_query_rewrites_neo4j_datetime_function(self):
        with patch("falkordb.FalkorDB", FakeFalkorDB):
            driver = await GraphDriverFactory.create_driver(
                GraphProvider.FALKORDB,
                {"host": "localhost", "port": 6379, "database": "test_graph"},
            )

        driver.execute_query_sync(
            "MATCH (n) SET n.updated_at = datetime() RETURN n",
            {"id": "node-1"},
        )

        query, params = driver.graph.calls[-1]
        self.assertNotIn("datetime()", query)
        self.assertIn("$__falkordb_now", query)
        self.assertEqual(params["id"], "node-1")
        self.assertRegex(params["__falkordb_now"], r"^\d{4}-\d{2}-\d{2}T.*Z$")

    async def test_async_execute_query_uses_the_bounded_driver_executor(self):
        with patch("falkordb.FalkorDB", FakeFalkorDB):
            driver = await GraphDriverFactory.create_driver(
                GraphProvider.FALKORDB,
                {"host": "localhost", "port": 6379, "database": "test_graph"},
            )
        caller_threads = []
        original_query = driver.graph.query

        def query(*args, **kwargs):
            caller_threads.append(threading.current_thread().name)
            return original_query(*args, **kwargs)

        driver.graph.query = query
        await driver.execute_query("MATCH (n) RETURN n")

        self.assertEqual(len(caller_threads), 1)
        self.assertTrue(caller_threads[0].startswith("cortex-falkordb-query"))
        driver.close()

    async def test_mutating_query_is_not_retried_after_an_ambiguous_failure(self):
        with patch("falkordb.FalkorDB", FakeFalkorDB):
            driver = await GraphDriverFactory.create_driver(
                GraphProvider.FALKORDB,
                {"host": "localhost", "port": 6379, "database": "test_graph"},
            )
        driver.graph.query = Mock(side_effect=RuntimeError("connection dropped"))

        with self.assertRaisesRegex(RuntimeError, "connection dropped"):
            driver.execute_query_sync("CREATE (:Probe {id: $id})", {"id": "one"})

        driver.graph.query.assert_called_once()
        driver.close()

    async def test_native_query_timeout_is_passed_to_falkordb(self):
        with patch("falkordb.FalkorDB", FakeFalkorDB):
            driver = await GraphDriverFactory.create_driver(
                GraphProvider.FALKORDB,
                {
                    "host": "localhost",
                    "port": 6379,
                    "database": "test_graph",
                    "query_timeout_ms": 4321,
                },
            )
        driver.execute_query_sync("MATCH (n) RETURN n")
        self.assertEqual(driver.graph.timeouts[-1], 4321)
        driver.close()

    async def test_mutating_timeout_is_typed_as_ambiguous_and_not_retried(self):
        from tools.graph.driver.falkordb_driver import AmbiguousWriteTimeoutError

        with patch("falkordb.FalkorDB", FakeFalkorDB):
            driver = await GraphDriverFactory.create_driver(
                GraphProvider.FALKORDB,
                {"host": "localhost", "port": 6379, "database": "test_graph"},
            )
        driver.graph.query = Mock(side_effect=TimeoutError("query timed out"))

        with self.assertRaisesRegex(AmbiguousWriteTimeoutError, "ambiguous"):
            driver.execute_query_sync("MERGE (:Probe {id: $id})", {"id": "one"})

        driver.graph.query.assert_called_once()
        driver.close()

    async def test_existing_index_is_logged_as_idempotent_skip(self):
        with patch("falkordb.FalkorDB", FakeFalkorDB):
            driver = await GraphDriverFactory.create_driver(
                GraphProvider.FALKORDB,
                {"host": "localhost", "port": 6379, "database": "test_graph"},
            )

        driver.graph.create_node_range_index = Mock(
            side_effect=RuntimeError("Attribute 'project_id' is already indexed")
        )

        with patch("tools.graph.driver.falkordb_driver.logger.warning") as warning:
            await driver.create_indexes(
                [{"label": "Project", "property": "project_id"}]
            )

        warning.assert_not_called()
        driver.close()

    async def test_required_index_creation_failure_propagates(self):
        with patch("falkordb.FalkorDB", FakeFalkorDB):
            driver = await GraphDriverFactory.create_driver(
                GraphProvider.FALKORDB,
                {"host": "localhost", "port": 6379, "database": "test_graph"},
            )

        driver.graph.create_node_range_index = Mock(
            side_effect=RuntimeError("index build failed")
        )
        with self.assertRaisesRegex(RuntimeError, "index build failed"):
            await driver.create_indexes(
                [{"label": "Function", "property": "id"}]
            )
        driver.close()

    async def test_index_inspection_normalizes_falkordb_types_map(self):
        driver = object.__new__(
            __import__(
                "tools.graph.driver.falkordb_driver",
                fromlist=["FalkorDBDriver"],
            ).FalkorDBDriver
        )

        async def execute_query(query, parameters=None, database=None):
            return (
                [
                    {
                        "label": "Function",
                        "properties": ["id", "symbol_id"],
                        "types": {"id": ["RANGE"], "symbol_id": ["RANGE"]},
                        "entitytype": "NODE",
                        "status": "OPERATIONAL",
                    }
                ],
                [],
                None,
            )

        driver.execute_query = execute_query
        self.assertEqual(
            await driver.inspect_indexes(database="code"),
            [
                {
                    "label": "Function",
                    "properties": ["id"],
                    "index_type": "range",
                    "entity_type": "node",
                    "status": "OPERATIONAL",
                },
                {
                    "label": "Function",
                    "properties": ["symbol_id"],
                    "index_type": "range",
                    "entity_type": "node",
                    "status": "OPERATIONAL",
                }
            ],
        )

    async def test_native_index_creation_uses_bounded_driver_executor(self):
        with patch("falkordb.FalkorDB", FakeFalkorDB):
            driver = await GraphDriverFactory.create_driver(
                GraphProvider.FALKORDB,
                {"host": "localhost", "port": 6379, "database": "test_graph"},
            )
        caller_threads = []

        def create_index(label, *properties):
            caller_threads.append(threading.current_thread().name)

        driver.graph.create_node_range_index = create_index
        await driver.create_indexes([{"label": "Function", "property": "id"}])

        self.assertEqual(len(caller_threads), 1)
        self.assertTrue(caller_threads[0].startswith("cortex-falkordb-query"))
        driver.close()

    async def test_close_does_not_wait_for_cancelled_native_ddl(self):
        with patch("falkordb.FalkorDB", FakeFalkorDB):
            driver = await GraphDriverFactory.create_driver(
                GraphProvider.FALKORDB,
                {"host": "localhost", "port": 6379, "database": "test_graph"},
            )
        started = threading.Event()
        release = threading.Event()

        def blocked_create(label, *properties):
            started.set()
            release.wait(timeout=1)

        driver.graph.create_node_range_index = blocked_create
        task = asyncio.create_task(
            driver.create_indexes([{"label": "Function", "property": "id"}])
        )
        await asyncio.to_thread(started.wait, 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        before = time.monotonic()
        driver.close()
        self.assertLess(time.monotonic() - before, 0.05)
        release.set()
        await asyncio.sleep(0.02)
        self.assertTrue(driver._resources_closed)

    def test_cancelled_native_call_does_not_delay_process_exit(self):
        script = textwrap.dedent(
            """
            import asyncio
            import threading
            import time
            from tools.graph.driver.falkordb_driver import FalkorDBDriver

            async def main():
                driver = object.__new__(FalkorDBDriver)
                driver._inflight_native_futures = set()
                driver._native_future_lock = threading.Lock()
                driver._deferred_close = False
                driver._resources_closed = False
                driver._client = None
                driver._storage_lease = None
                task = asyncio.create_task(driver._run_in_executor(time.sleep, 5))
                await asyncio.sleep(0.02)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                driver.close()

            asyncio.run(main())
            """
        )
        env = dict(os.environ)
        env["PYTHONPATH"] = CODE_TINY
        started = time.monotonic()
        completed = subprocess.run(
            [sys.executable, "-c", script],
            env=env,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertLess(time.monotonic() - started, 2)

    async def test_inspect_indexes_normalizes_provider_metadata(self):
        with patch("falkordb.FalkorDB", FakeFalkorDB):
            driver = await GraphDriverFactory.create_driver(
                GraphProvider.FALKORDB,
                {"host": "localhost", "port": 6379, "database": "test_graph"},
            )
        driver.execute_query = AsyncMock(
            return_value=(
                [
                    {
                        "label": "Function",
                        "properties": ["id"],
                        "type": "RANGE",
                        "entitytype": "NODE",
                        "status": "OPERATIONAL",
                    }
                ],
                [],
                None,
            )
        )

        self.assertEqual(
            await driver.inspect_indexes(),
            [
                {
                    "label": "Function",
                    "properties": ["id"],
                    "index_type": "range",
                    "entity_type": "node",
                    "status": "OPERATIONAL",
                }
            ],
        )
        driver.close()


if __name__ == "__main__":
    unittest.main()
