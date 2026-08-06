import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.common.project_scope import (  # noqa: E402
    enrich_project_scope,
    matches_project_scope,
    prepare_project_scope_parameters,
    project_id_lookup_key,
    qdrant_project_filter,
)


SPEC = importlib.util.spec_from_file_location(
    "backfill_project_scope_keys",
    CODE_TINY / "scripts" / "backfill_project_scope_keys.py",
)
assert SPEC is not None and SPEC.loader is not None
BACKFILL = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BACKFILL
SPEC.loader.exec_module(BACKFILL)


class ProjectScopeContractTests(unittest.TestCase):
    def test_case_variants_share_one_lookup_key(self):
        self.assertEqual(
            {project_id_lookup_key(value) for value in ("HIEP", "hiep", "hiEp")},
            {"hiep"},
        )
        self.assertNotEqual(project_id_lookup_key("hiep-2"), "hiep")
        self.assertIsNone(project_id_lookup_key("  "))

    def test_raw_identity_is_preserved_while_nested_payloads_are_enriched(self):
        source = {
            "project_id": " HIEP ",
            "project_id_normalized": "stale",
            "rows": [{"project_id": "hiEp"}],
        }

        enriched = enrich_project_scope(source)

        self.assertEqual(enriched["project_id"], " HIEP ")
        self.assertEqual(enriched["project_id_normalized"], "hiep")
        self.assertEqual(enriched["rows"][0]["project_id"], "hiEp")
        self.assertEqual(enriched["rows"][0]["project_id_normalized"], "hiep")
        self.assertEqual(source["project_id_normalized"], "stale")
        self.assertNotIn("project_id_normalized", source["rows"][0])

    def test_graph_parameters_fold_only_for_normalized_predicates(self):
        normalized = prepare_project_scope_parameters(
            "MATCH (n) WHERE n.project_id_normalized = $project_id_normalized RETURN n",
            {"project_id": "HiEp", "project_id_normalized": "stale"},
        )
        legacy = prepare_project_scope_parameters(
            "MATCH (n) WHERE n.project_id = $project_id RETURN n",
            {"project_id": "HiEp"},
        )

        self.assertEqual(normalized["project_id"], "HiEp")
        self.assertEqual(normalized["project_id_normalized"], "hiep")
        self.assertEqual(legacy["project_id"], "HiEp")
        self.assertEqual(legacy["project_id_normalized"], "hiep")

    def test_qdrant_and_python_filters_are_case_insensitive(self):
        self.assertEqual(
            qdrant_project_filter("HiEp"),
            {
                "must": [{
                    "key": "project_id_normalized",
                    "match": {"value": "hiep"},
                }]
            },
        )
        self.assertTrue(matches_project_scope({"project_id": "HIEP"}, "hiep"))
        self.assertTrue(
            matches_project_scope(
                {"project_id": "Other", "project_id_normalized": "hiep"},
                "hiEp",
            )
        )
        self.assertFalse(matches_project_scope({"project_id": "hiep-2"}, "hiep"))


class _GraphDriver:
    def __init__(self):
        self.updates = []
        self.indexes = []

    async def execute_query(self, query, parameters=None, database=None):
        if "RETURN n.project_id AS project_id" in query:
            return (
                [
                    {"project_id": "HIEP", "project_id_normalized": None, "count": 2},
                    {"project_id": "hiep", "project_id_normalized": "hiep", "count": 1},
                    {"project_id": None, "project_id_normalized": None, "count": 4},
                ],
                [],
                None,
            )
        if "RETURN DISTINCT labels(n) AS labels" in query:
            return ([{"labels": ["Function", "File"]}], [], None)
        if "SET n.project_id_normalized" in query:
            params = dict(parameters or {})
            self.updates.append(params)
            count = 2 if params.get("raw") == "HIEP" else 0
            return ([{"count": count}], [], None)
        raise AssertionError(query)

    async def create_indexes(self, indexes, database=None):
        self.indexes.extend(indexes)


class _LocalQdrantStore:
    def __init__(self):
        self.points = [
            {"id": 1, "payload": {"project_id": "HIEP", "name": "one"}},
            {
                "id": 2,
                "payload": {
                    "project_id": "hiep",
                    "project_id_normalized": "hiep",
                    "name": "two",
                },
            },
            {"id": 3, "payload": {"project_id": "hiEp", "name": "three"}},
            {"id": 4, "payload": {"name": "unscoped"}},
        ]
        self.scroll_requests = []
        self.payload_updates = []
        self.index_updates = []

    def scroll(
        self,
        collection_name,
        *,
        scroll_filter,
        limit,
        offset,
        with_payload,
        with_vectors,
    ):
        self.scroll_requests.append(
            {
                "collection_name": collection_name,
                "scroll_filter": scroll_filter,
                "limit": limit,
                "offset": offset,
                "with_payload": with_payload,
                "with_vectors": with_vectors,
            }
        )
        start = int(offset or 0)
        end = min(start + int(limit), len(self.points))
        next_offset = end if end < len(self.points) else None
        return self.points[start:end], next_offset

    def set_payload(self, collection, payload, *, points, wait):
        self.payload_updates.append(
            {"collection": collection, "payload": dict(payload), "points": list(points), "wait": wait}
        )
        point_ids = set(points)
        for point in self.points:
            if point["id"] in point_ids:
                point["payload"].update(payload)

    def create_payload_index(self, collection, field_name, *, wait):
        self.index_updates.append(
            {"collection": collection, "field_name": field_name, "wait": wait}
        )


class ProjectScopeBackfillTests(unittest.IsolatedAsyncioTestCase):
    async def test_graph_backfill_reports_collisions_and_is_idempotent_by_predicate(self):
        driver = _GraphDriver()

        result = await BACKFILL.backfill_graph(
            driver,
            database="cortext",
            apply=True,
        )

        self.assertEqual(result.eligible, 3)
        self.assertEqual(result.missing_project_id, 4)
        self.assertEqual(result.already_normalized, 1)
        self.assertEqual(result.needs_update, 2)
        self.assertEqual(result.updated, 2)
        self.assertEqual(result.collisions, {"hiep": ["HIEP", "hiep"]})
        self.assertEqual(
            driver.updates,
            [
                {"raw": "HIEP", "normalized": "hiep"},
                {"raw": "hiep", "normalized": "hiep"},
            ],
        )
        self.assertEqual(
            driver.indexes,
            [
                {"label": "File", "property": "project_id_normalized"},
                {"label": "Function", "property": "project_id_normalized"},
            ],
        )

    async def test_graph_apply_still_ensures_indexes_when_data_is_current(self):
        driver = _GraphDriver()

        async def current_execute(query, parameters=None, database=None):
            if "RETURN n.project_id AS project_id" in query:
                return ([{"project_id": "HIEP", "project_id_normalized": "hiep", "count": 1}], [], None)
            if "RETURN DISTINCT labels(n) AS labels" in query:
                return ([{"labels": ["Function"]}], [], None)
            raise AssertionError(query)

        driver.execute_query = current_execute
        result = await BACKFILL.backfill_graph(driver, database="cortext", apply=True)

        self.assertEqual(result.needs_update, 0)
        self.assertEqual(driver.indexes, [{"label": "Function", "property": "project_id_normalized"}])

    def test_qdrant_backfill_is_paginated_and_repeatable(self):
        session = _LocalQdrantStore()

        first = BACKFILL.backfill_qdrant_collection(
            session,
            qdrant_url="local-code-store",
            collection="code",
            apply=True,
            page_size=2,
            batch_size=1,
            timeout=5,
        )
        second = BACKFILL.backfill_qdrant_collection(
            session,
            qdrant_url="local-code-store",
            collection="code",
            apply=True,
            page_size=2,
            batch_size=1,
            timeout=5,
        )

        self.assertEqual(first.eligible, 3)
        self.assertEqual(first.missing_project_id, 1)
        self.assertEqual(first.already_normalized, 1)
        self.assertEqual(first.needs_update, 2)
        self.assertEqual(first.updated, 2)
        self.assertEqual(first.collisions, {"hiep": ["HIEP", "hiEp", "hiep"]})
        self.assertEqual(second.needs_update, 0)
        self.assertEqual(second.updated, 0)
        self.assertTrue(all(request["with_vectors"] is False for request in session.scroll_requests))
        self.assertEqual(len(session.payload_updates), 2)
        self.assertEqual(len(session.index_updates), 2)
        self.assertTrue(all(point["payload"].get("name") for point in session.points))


if __name__ == "__main__":
    unittest.main()
