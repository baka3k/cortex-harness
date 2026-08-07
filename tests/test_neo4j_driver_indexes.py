import os
import sys
import unittest
from unittest.mock import AsyncMock


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CODE_TINY = os.path.join(REPO_ROOT, "code-tiny")
if CODE_TINY not in sys.path:
    sys.path.insert(0, CODE_TINY)

from tools.graph.driver.neo4j_driver import Neo4jDriver


class Neo4jIndexInspectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_edge_writer_rejects_label_without_identity_index(self):
        driver = object.__new__(Neo4jDriver)
        driver.execute_query = AsyncMock()

        with self.assertRaisesRegex(ValueError, "has no required id index"):
            await driver.batch_write_edges(
                [{"source_id": "a", "target_id": "b", "properties": {}}],
                "CALLS",
                "SyntacticallyValidButUnknown",
                "Function",
            )

        driver.execute_query.assert_not_awaited()

    async def test_inspect_indexes_normalizes_provider_metadata(self):
        driver = object.__new__(Neo4jDriver)
        driver.execute_query = AsyncMock(
            return_value=(
                [
                    {
                        "labelsOrTypes": ["Function"],
                        "properties": ["id"],
                        "type": "RANGE",
                        "entityType": "NODE",
                        "state": "ONLINE",
                    }
                ],
                [],
                None,
            )
        )

        self.assertEqual(
            await driver.inspect_indexes(database="code"),
            [
                {
                    "label": "Function",
                    "properties": ["id"],
                    "index_type": "range",
                    "entity_type": "node",
                    "status": "ONLINE",
                }
            ],
        )
        driver.execute_query.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
