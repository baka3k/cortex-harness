import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


_MCP_DIR = Path(__file__).resolve().parents[3] / "mcp"
if str(_MCP_DIR) not in sys.path:
    sys.path.insert(0, str(_MCP_DIR))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Load the shared helpers so the tests can patch the same names the
# backend resolves at call time.
falkordb_discovery = _load(
    "falkordb_discovery_under_test", _MCP_DIR / "falkordb_discovery.py"
)
sys.modules["falkordb_discovery"] = falkordb_discovery

cplus_mcp = _load(
    "cplus_mcp_under_test",
    _MCP_DIR / "cplus" / "cplus_mcp.py",
)


class CPlusMCPTests(unittest.IsolatedAsyncioTestCase):
    def test_falkordb_path_edges_keep_type_and_domain_node_ids(self):
        graph = cplus_mcp._paths_to_graph([{
            "nodes": [
                {"id": "function-a", "_graph_id": 10},
                {"id": "function-b", "_graph_id": 20},
            ],
            "edges": [{
                "_type": "CALLS",
                "_start_id": 10,
                "_end_id": 20,
                "confidence": 0.9,
            }],
        }])

        self.assertEqual(graph["edges"], [{
            "type": "CALLS",
            "properties": {"confidence": 0.9},
            "start_id": "function-a",
            "end_id": "function-b",
        }])
        self.assertNotIn("_graph_id", graph["nodes"][0]["properties"])

    def test_query_profile_error_scopes_strict_modes_to_cplus(self):
        with self.assertRaisesRegex(
            ValueError,
            "only supported for C/C\\+\\+/Pro\\*C",
        ):
            cplus_mcp._profile_rel_types("python", "strict")

        self.assertEqual(cplus_mcp._profile_rel_types("cplus", "strict"), ["CALLS"])

    def test_discovery_honors_relocated_data_home(self):
        with tempfile.TemporaryDirectory() as directory:
            data_home = Path(directory)
            first = data_home / "v1" / "instances" / "alpha" / "falkordb" / "code" / "data.rdb"
            second = data_home / "v1" / "instances" / "beta" / "falkordb" / "code" / "data.rdb"
            first.parent.mkdir(parents=True)
            second.parent.mkdir(parents=True)
            first.touch()
            second.touch()
            with patch.dict(
                falkordb_discovery.os.environ,
                {"CORTEX_DATA_HOME": str(data_home)},
            ):
                # Default boot path returns only the current instance.
                # No CORTEX_STORAGE_INSTANCE set ⇒ no primary file ⇒ empty list.
                self.assertEqual(falkordb_discovery.discover_falkordb_data_files(), [])

                # When the current instance matches one of the siblings, only
                # that file is returned — even with siblings present.
                with patch.dict(
                    falkordb_discovery.os.environ,
                    {"CORTEX_DATA_HOME": str(data_home),
                     "CORTEX_STORAGE_INSTANCE": "alpha"},
                ):
                    self.assertEqual(
                        falkordb_discovery.discover_falkordb_data_files(),
                        [first],
                    )

                # Legacy escape hatch restores the "every sibling" behavior.
                with patch.dict(
                    falkordb_discovery.os.environ,
                    {"CORTEX_DATA_HOME": str(data_home),
                     "CORTEX_STORAGE_INSTANCE": "alpha",
                     "CORTEX_MCP_SCOPE_LEASES": "0"},
                ):
                    self.assertEqual(
                        falkordb_discovery.discover_falkordb_data_files(),
                        [first, second],
                    )

    def test_discovery_siblings_exclude_self_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            data_home = Path(directory)
            self_path = data_home / "v1" / "instances" / "alpha" / "falkordb" / "code" / "data.rdb"
            sibling = data_home / "v1" / "instances" / "beta" / "falkordb" / "code" / "data.rdb"
            for path in (self_path, sibling):
                path.parent.mkdir(parents=True)
                path.touch()
            with patch.dict(
                falkordb_discovery.os.environ,
                {"CORTEX_DATA_HOME": str(data_home),
                 "CORTEX_STORAGE_INSTANCE": "alpha"},
            ):
                discovered = falkordb_discovery.discover_falkordb_data_files(
                    include_siblings=True, exclude_self=True
                )
        self.assertEqual(discovered, [sibling])

    def test_discovery_siblings_include_self_when_exclude_self_false(self):
        with tempfile.TemporaryDirectory() as directory:
            data_home = Path(directory)
            self_path = data_home / "v1" / "instances" / "alpha" / "falkordb" / "code" / "data.rdb"
            sibling = data_home / "v1" / "instances" / "beta" / "falkordb" / "code" / "data.rdb"
            for path in (self_path, sibling):
                path.parent.mkdir(parents=True)
                path.touch()
            with patch.dict(
                falkordb_discovery.os.environ,
                {"CORTEX_DATA_HOME": str(data_home),
                 "CORTEX_STORAGE_INSTANCE": "alpha"},
            ):
                discovered = falkordb_discovery.discover_falkordb_data_files(
                    include_siblings=True, exclude_self=False
                )
        self.assertEqual(discovered, [self_path, sibling])

    def test_unregistered_project_id_remains_the_scoped_graph_candidate(self):
        project_id = "unregistered-project"
        with (
            patch.object(
                cplus_mcp,
                "resolve_project_targets",
                side_effect=cplus_mcp.ProjectNotRegisteredError(project_id, []),
            ),
            patch.object(cplus_mcp, "DEFAULT_GRAPH_DB", "default-graph"),
        ):
            candidates = cplus_mcp._resolve_db_candidates(project_id)

        self.assertEqual(candidates, [project_id])

    async def test_unscoped_query_uses_every_discovered_database(self):
        run_cypher = AsyncMock(
            side_effect=lambda query, params, database: [{"db": database}]
        )
        with (
            patch.object(
                cplus_mcp,
                "_list_databases",
                AsyncMock(return_value=["cortext", "hyperpack"]),
            ),
            patch.object(cplus_mcp, "_run_cypher", run_cypher),
        ):
            _, records = await cplus_mcp._run_cypher_first(
                "RETURN 1",
                {},
                ["cortext"],
            )

        self.assertEqual(
            [call.args[2] for call in run_cypher.await_args_list],
            ["cortext", "hyperpack"],
        )
        self.assertEqual(records, [{"db": "cortext"}, {"db": "hyperpack"}])

    async def test_scoped_query_never_falls_back_to_another_database(self):
        run_cypher = AsyncMock()
        with (
            patch.object(
                cplus_mcp,
                "_list_databases",
                AsyncMock(return_value=["default-graph"]),
            ),
            patch.object(cplus_mcp, "_run_cypher", run_cypher),
            patch.object(cplus_mcp, "DEFAULT_GRAPH_DB", "default-graph"),
        ):
            with self.assertRaisesRegex(RuntimeError, "No database candidates"):
                await cplus_mcp._run_cypher_first(
                    "RETURN 1",
                    {"project_id": "missing-project"},
                    ["missing-project"],
                )

        run_cypher.assert_not_awaited()

    async def test_graph_driver_receives_no_sibling_paths_by_default(self):
        primary_path = Path("/tmp/_default_test_data.rdb")
        driver = SimpleNamespace()
        create_driver = AsyncMock(return_value=driver)

        with (
            patch.object(cplus_mcp, "DEFAULT_GRAPH_PROVIDER", "falkordb"),
            patch.dict(cplus_mcp.os.environ, {"FALKORDB_PATH": str(primary_path)}),
            patch.object(cplus_mcp, "get_shared_graph_driver", create_driver),
        ):
            cplus_mcp._graph_driver = None
            resolved = await cplus_mcp._get_graph_driver()

        self.assertIs(resolved, driver)
        config = create_driver.await_args.args[1]
        # Phase 02: boot path passes no sibling paths so MCP A does not
        # lease instance B's file.
        self.assertEqual(config["additional_paths"], [])
        cplus_mcp._graph_driver = None

    async def test_graph_driver_prefers_explicit_remote_uri(self):
        driver = SimpleNamespace()
        create_driver = AsyncMock(return_value=driver)

        with (
            patch.object(cplus_mcp, "DEFAULT_GRAPH_PROVIDER", "falkordb"),
            patch.dict(
                cplus_mcp.os.environ,
                {
                    "FALKORDB_URI": "redis://graph.internal:6379",
                    "FALKORDB_PATH": "/tmp/stale-local.rdb",
                },
                clear=False,
            ),
            patch.object(cplus_mcp, "get_shared_graph_driver", create_driver),
        ):
            cplus_mcp._graph_driver = None
            resolved = await cplus_mcp._get_graph_driver()

        self.assertIs(resolved, driver)
        config = create_driver.await_args.args[1]
        self.assertEqual(config["uri"], "redis://graph.internal:6379")
        self.assertIsNone(config["path"])
        cplus_mcp._graph_driver = None

    async def test_list_databases_falls_back_to_driver_when_no_siblings(self):
        """With no sibling .rdb files discovered, defer to the primary driver."""
        driver = SimpleNamespace(
            list_databases=AsyncMock(return_value=["cplus_test", "cortext"]),
            path=Path("/tmp/_none.rdb"),
        )

        with (
            patch.object(cplus_mcp, "DEFAULT_GRAPH_PROVIDER", "falkordb"),
            patch.object(
                cplus_mcp,
                "_get_graph_driver",
                AsyncMock(return_value=driver),
            ),
        ):
            self.assertEqual(
                await cplus_mcp._list_databases(),
                ["cplus_test", "cortext"],
            )

        driver.list_databases.assert_awaited_once_with()
