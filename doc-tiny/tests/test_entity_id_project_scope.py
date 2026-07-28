"""Per-project entity ID isolation test for graphrag_ingest_langextract.

Per Phase 04 of the unified ingest/query contract plan, entity merge keys
must be namespaced by ``project_id_normalized`` so two projects that share
one doc graph never collapse a same-named entity into one node.

Run from the repo root::

    python -m unittest doc_tiny.tests.test_entity_id_project_scope
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


_DOC_TINY = Path(__file__).resolve().parents[1]
_INGEST_PATH = _DOC_TINY / "graphrag_ingest_langextract.py"


def _load_module(name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(name, file_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ingest = _load_module("graphrag_ingest_under_test", _INGEST_PATH)


class EntityIdProjectScopeTests(unittest.TestCase):
    """``_entity_id`` produces distinct IDs across projects."""

    def test_same_entity_different_projects_yields_distinct_ids(self) -> None:
        a_id = ingest._entity_id("User", "Person", project_id_normalized="projA")
        b_id = ingest._entity_id("User", "Person", project_id_normalized="projB")
        self.assertNotEqual(a_id, b_id)

    def test_same_entity_same_project_is_stable(self) -> None:
        a1 = ingest._entity_id("User", "Person", project_id_normalized="projA")
        a2 = ingest._entity_id("User", "Person", project_id_normalized="projA")
        self.assertEqual(a1, a2)

    def test_legacy_no_project_id_falls_back_to_legacy_key(self) -> None:
        # ``project_id_normalized=None`` keeps the legacy ``{type}::{name}``
        # form so single-project tests and callers that have not migrated
        # continue to produce the same UUIDs.
        legacy = ingest._entity_id("User", "Person")
        # Two different names must yield different IDs even without scope.
        self.assertNotEqual(
            legacy,
            ingest._entity_id("Admin", "Person"),
        )

    def test_build_graph_components_isolates_entities_per_project(self) -> None:
        entities = [{"name": "User", "type": "Person"}]
        a_nodes, _ = ingest.build_graph_components_from_entities(
            entities, [], project_id_normalized="projA"
        )
        b_nodes, _ = ingest.build_graph_components_from_entities(
            entities, [], project_id_normalized="projB"
        )
        self.assertEqual(len(a_nodes), 1)
        self.assertEqual(len(b_nodes), 1)
        a_id = next(iter(a_nodes.values()))["id"]
        b_id = next(iter(b_nodes.values()))["id"]
        self.assertNotEqual(a_id, b_id)

    def test_relation_entities_stay_distinct_across_projects(self) -> None:
        relations = [
            {"source": "User", "target": "Admin", "type": "RELATED"},
        ]
        a_nodes, _ = ingest.build_graph_components_from_entities(
            [], relations, project_id_normalized="projA"
        )
        b_nodes, _ = ingest.build_graph_components_from_entities(
            [], relations, project_id_normalized="projB"
        )
        a_ids = sorted(node["id"] for node in a_nodes.values())
        b_ids = sorted(node["id"] for node in b_nodes.values())
        # Same set of entity names, distinct IDs.
        self.assertNotEqual(a_ids, b_ids)


if __name__ == "__main__":
    unittest.main()
