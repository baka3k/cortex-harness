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
                discovered = falkordb_discovery.discover_falkordb_data_files()

        self.assertEqual(discovered, [first, second])

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

    async def test_graph_driver_receives_all_discovered_instance_files(self):
        primary_path = Path("/tmp/_default_test_data.rdb")
        sibling_path = Path("/tmp/_hyperpack_test_data.rdb")
        driver = SimpleNamespace()
        create_driver = AsyncMock(return_value=driver)

        with (
            patch.object(cplus_mcp, "DEFAULT_GRAPH_PROVIDER", "falkordb"),
            patch.dict(cplus_mcp.os.environ, {"FALKORDB_PATH": str(primary_path)}),
            patch.object(
                cplus_mcp,
                "discover_falkordb_data_files",
                return_value=[primary_path, sibling_path],
            ),
            patch.object(cplus_mcp, "get_shared_graph_driver", create_driver),
        ):
            cplus_mcp._graph_driver = None
            resolved = await cplus_mcp._get_graph_driver()

        self.assertIs(resolved, driver)
        config = create_driver.await_args.args[1]
        self.assertEqual(config["additional_paths"], [primary_path, sibling_path])
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
