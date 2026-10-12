import unittest

from tools.graph.driver.falkordb_driver import FalkorDBDriver, _normalize_query


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


class NormalizeQueryTests(unittest.TestCase):
    def test_empty_importing_subquery_is_rewritten_to_plain_call(self):
        # Neo4j 5 accepts ``CALL () { ... }``; FalkorDB rejects the
        # parenthesized form outright with "Invalid input '('".
        query = "CALL () {\nMATCH (a) RETURN a\nUNION\nMATCH (b) RETURN b\n}\nRETURN a"
        self.assertTrue(_normalize_query(query).startswith("CALL {"))
        self.assertNotIn("CALL ()", _normalize_query(query))

    def test_importing_variable_subquery_gets_with_clause(self):
        query = "CALL (ep) { MATCH (ep:X) RETURN ep } WITH ep RETURN ep"
        self.assertIn("CALL { WITH ep", _normalize_query(query))
