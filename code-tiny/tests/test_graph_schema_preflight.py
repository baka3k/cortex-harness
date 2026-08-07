from __future__ import annotations

import unittest

from tools.graph.schema import (
    CODE_GRAPH_SCHEMA,
    GraphSchemaManifest,
    SchemaIndex,
    SchemaPreflightError,
    ensure_schema,
)


class _PreflightDriver:
    provider = "falkordb"

    def __init__(self, *, fail_create: bool = False) -> None:
        self.fail_create = fail_create
        self.created = []
        self.installed = []
        self.inspect_calls = 0

    async def create_indexes(self, indexes, database=None):
        if self.fail_create:
            raise RuntimeError("ddl rejected")
        self.created.append((indexes, database))
        self.installed.extend(indexes)

    async def inspect_indexes(self, database=None):
        self.inspect_calls += 1
        return [
            {
                "label": index["label"],
                "properties": (
                    index["property"]
                    if isinstance(index["property"], list)
                    else [index["property"]]
                ),
                "index_type": index.get("type", "range"),
                "entity_type": "node",
                "status": "OPERATIONAL",
            }
            for index in self.installed
        ]


class GraphSchemaPreflightTests(unittest.IsolatedAsyncioTestCase):
    def test_manifest_rejects_unsafe_or_duplicate_indexes(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsafe Cypher label"):
            SchemaIndex("File`) MATCH (n) DETACH DELETE n //", ("id",))
        duplicate = SchemaIndex("File", ("id",))
        with self.assertRaisesRegex(ValueError, "duplicate indexes"):
            GraphSchemaManifest("bad", 1, (duplicate, duplicate))

    async def test_preflight_creates_verifies_and_caches_required_indexes(self) -> None:
        driver = _PreflightDriver()
        first = await ensure_schema(driver, CODE_GRAPH_SCHEMA, database="code")
        second = await ensure_schema(driver, CODE_GRAPH_SCHEMA, database="code")

        self.assertIs(first, second)
        self.assertEqual(len(driver.created), len(CODE_GRAPH_SCHEMA.indexes))
        self.assertEqual(driver.inspect_calls, 2)
        self.assertEqual(first.required_count, len(CODE_GRAPH_SCHEMA.indexes))
        self.assertEqual(first.verified_count, first.required_count)

    async def test_preflight_skips_ddl_when_manifest_is_already_operational(self) -> None:
        driver = _PreflightDriver()
        driver.installed.extend(CODE_GRAPH_SCHEMA.driver_indexes())

        result = await ensure_schema(driver, CODE_GRAPH_SCHEMA, database="code")

        self.assertEqual(driver.created, [])
        self.assertEqual(driver.inspect_calls, 1)
        self.assertEqual(result.verified_count, len(CODE_GRAPH_SCHEMA.indexes))

    async def test_preflight_creates_only_the_missing_index(self) -> None:
        driver = _PreflightDriver()
        driver.installed.extend(CODE_GRAPH_SCHEMA.driver_indexes()[1:])

        await ensure_schema(driver, CODE_GRAPH_SCHEMA, database="code")

        self.assertEqual(len(driver.created), 1)
        self.assertEqual(driver.created[0][0], CODE_GRAPH_SCHEMA.driver_indexes()[:1])

    async def test_required_index_creation_failure_is_fatal(self) -> None:
        driver = _PreflightDriver(fail_create=True)
        with self.assertRaisesRegex(SchemaPreflightError, "creation failed"):
            await ensure_schema(driver, CODE_GRAPH_SCHEMA, database="code")
        self.assertFalse(getattr(driver, "_schema_preflight_cache", {}))

    async def test_non_operational_index_times_out_without_caching(self) -> None:
        driver = _PreflightDriver()

        async def not_ready(database=None):
            return [
                {"label": "Project", "properties": ["project_id"], "status": "BUILDING"}
            ]

        driver.inspect_indexes = not_ready
        with self.assertRaisesRegex(SchemaPreflightError, "not operational"):
            await ensure_schema(
                driver,
                CODE_GRAPH_SCHEMA,
                database="code",
                timeout_seconds=0.05,
                poll_interval_seconds=0,
            )
        self.assertFalse(getattr(driver, "_schema_preflight_cache", {}))

    async def test_missing_inspection_capability_is_fatal(self) -> None:
        class CreateOnlyDriver:
            provider = "falkordb"

            async def create_indexes(self, indexes, database=None):
                return None

        driver = CreateOnlyDriver()
        with self.assertRaisesRegex(SchemaPreflightError, "does not implement inspect_indexes"):
            await ensure_schema(driver, CODE_GRAPH_SCHEMA, database="code")
        self.assertFalse(getattr(driver, "_schema_preflight_cache", {}))

    async def test_inspection_is_bounded_by_the_preflight_deadline(self) -> None:
        driver = _PreflightDriver()

        async def slow_inspection(database=None):
            await __import__("asyncio").sleep(1)
            return []

        driver.inspect_indexes = slow_inspection
        with self.assertRaisesRegex(SchemaPreflightError, "inspection exceeded"):
            await ensure_schema(driver, CODE_GRAPH_SCHEMA, timeout_seconds=0.01)

    async def test_wrong_index_type_does_not_satisfy_required_range_index(self) -> None:
        driver = _PreflightDriver()

        async def wrong_type(database=None):
            return [
                {
                    "label": index.label,
                    "properties": list(index.properties),
                    "index_type": "fulltext",
                    "entity_type": "node",
                    "status": "OPERATIONAL",
                }
                for index in CODE_GRAPH_SCHEMA.indexes
            ]

        driver.inspect_indexes = wrong_type
        with self.assertRaisesRegex(SchemaPreflightError, "not operational"):
            await ensure_schema(driver, CODE_GRAPH_SCHEMA, database="code", timeout_seconds=0.05)
        self.assertFalse(getattr(driver, "_schema_preflight_cache", {}))

    async def test_wrong_entity_type_does_not_satisfy_required_node_index(self) -> None:
        driver = _PreflightDriver()

        async def wrong_entity(database=None):
            return [
                {
                    "label": index.label,
                    "properties": list(index.properties),
                    "index_type": index.index_type,
                    "entity_type": "relationship",
                    "status": "OPERATIONAL",
                }
                for index in CODE_GRAPH_SCHEMA.indexes
            ]

        driver.inspect_indexes = wrong_entity
        with self.assertRaisesRegex(SchemaPreflightError, "not operational"):
            await ensure_schema(driver, CODE_GRAPH_SCHEMA, database="code", timeout_seconds=0.05)
        self.assertFalse(getattr(driver, "_schema_preflight_cache", {}))

    async def test_failed_required_index_fails_immediately_without_caching(self) -> None:
        driver = _PreflightDriver()

        async def failed(database=None):
            return [
                {
                    "label": index.label,
                    "properties": list(index.properties),
                    "index_type": index.index_type,
                    "entity_type": index.entity_type,
                    "status": "FAILED" if index.label == "Function" else "OPERATIONAL",
                }
                for index in CODE_GRAPH_SCHEMA.indexes
            ]

        driver.inspect_indexes = failed
        with self.assertRaisesRegex(SchemaPreflightError, "contains failed required indexes"):
            await ensure_schema(driver, CODE_GRAPH_SCHEMA, database="code", timeout_seconds=30)
        self.assertFalse(getattr(driver, "_schema_preflight_cache", {}))


if __name__ == "__main__":
    unittest.main()
