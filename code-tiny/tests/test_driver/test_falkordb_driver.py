import unittest

from tools.graph.driver.falkordb_driver import FalkorDBDriver


class _FakeFalkorDBClient:
    def list_graphs(self):
        return [b"cplus_test", "cortext", b"\xff"]


class FalkorDBDriverTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_databases_decodes_byte_graph_names(self):
        driver = FalkorDBDriver.__new__(FalkorDBDriver)
        driver._client = _FakeFalkorDBClient()
        driver._database = "cortext"

        self.assertEqual(
            await driver.list_databases(),
            ["cplus_test", "cortext"],
        )
