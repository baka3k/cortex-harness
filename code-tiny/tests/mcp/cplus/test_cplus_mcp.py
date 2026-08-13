import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


_MODULE_PATH = (
    Path(__file__).resolve().parents[3] / "mcp" / "cplus" / "cplus_mcp.py"
)
_SPEC = importlib.util.spec_from_file_location("cplus_mcp_under_test", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
cplus_mcp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cplus_mcp)


class CPlusMCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_databases_aggregates_all_instance_rdb_files(self):
        """Discovery augments the primary driver's graphs with sibling
        instance files. The primary path itself is skipped (already covered
        by ``driver.list_databases``) so each .rdb is only opened once."""
        primary_path = Path("/tmp/_default_test_data.rdb")
        sibling_path = Path("/tmp/_hyperpack_test_data.rdb")
        driver = SimpleNamespace(
            list_databases=AsyncMock(return_value=["cplus_test", "cortext"]),
            path=primary_path,
        )

        async def fake_list_in_file(path):
            # Only the sibling path should be opened; the primary path is
            # covered by driver.list_databases above.
            if path.name == "_hyperpack_test_data.rdb":
                return ["hyperpack"]
            return []

        with (
            patch.object(cplus_mcp, "DEFAULT_GRAPH_PROVIDER", "falkordb"),
            patch.object(
                cplus_mcp,
                "_get_graph_driver",
                AsyncMock(return_value=driver),
            ),
            patch.object(
                cplus_mcp,
                "_discover_falkordb_data_files",
                return_value=[primary_path, sibling_path],
            ),
            patch.object(
                cplus_mcp,
                "_list_graphs_in_file",
                side_effect=fake_list_in_file,
            ),
        ):
            names = await cplus_mcp._list_databases()

        # Primary driver contributes cplus_test + cortext; hyperpack comes
        # from the sibling path via discovery.
        self.assertEqual(names, ["cplus_test", "cortext", "hyperpack"])
        # Primary driver is queried once; we don't re-open its .rdb.
        driver.list_databases.assert_awaited_once_with()

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
            patch.object(
                cplus_mcp,
                "_discover_falkordb_data_files",
                return_value=[],
            ),
        ):
            self.assertEqual(
                await cplus_mcp._list_databases(),
                ["cplus_test", "cortext"],
            )

        driver.list_databases.assert_awaited_once_with()
