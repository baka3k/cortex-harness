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
    async def test_list_databases_uses_falkordb_driver_discovery(self):
        driver = SimpleNamespace(
            list_databases=AsyncMock(return_value=["cplus_test", "cortext"]),
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
