from __future__ import annotations

import unittest

from tools.graph.writer.language_writer import LanguageCodeWriter
from tools.graph.operations.cross_edge_ops import CrossEdgeOperations
from tools.graph.operations.class_ops import ClassNodeOperations
from tools.graph.operations.namespace_ops import NamespaceNodeOperations
from tools.graph.operations.type_ops import TypeNodeOperations
from tools.graph.writer.query_contract import (
    RelationshipGroup,
    compile_relationship_endpoint_audit,
    compile_relationship_upsert,
    group_typed_relations,
)


class _RecordingDriver:
    provider = "falkordb"

    def __init__(self) -> None:
        self.calls = []

    async def execute_query(self, query, parameters=None, database=None):
        parameters = dict(parameters or {})
        self.calls.append((query, parameters, database))
        return ([{"count": len(parameters.get("rows", []))}], [], None)

    async def batch_write_nodes(self, nodes, label, database=None):
        return len(nodes)


def _relation(source_label="File", target_label="Function", rel_type="CONTAINS"):
    return {
        "source_label": source_label,
        "target_label": target_label,
        "rel_type": rel_type,
        "source_id": "same-id",
        "target_id": "same-id",
        "properties": {"project_id": "cortext"},
    }


class RelationshipQueryContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_cross_edge_api_validates_and_executes(self) -> None:
        driver = _RecordingDriver()
        created = await CrossEdgeOperations.link_code_to_document(
            driver,
            "fn-1",
            "paragraph-1",
        )

        self.assertTrue(created)
        self.assertIn("MATCH (code:Function", driver.calls[0][0])
        self.assertIn("MATCH (doc:Paragraph", driver.calls[0][0])

    async def test_direct_dynamic_edge_apis_reject_unknown_identity_labels(self) -> None:
        driver = _RecordingDriver()
        with self.assertRaisesRegex(ValueError, "has no required id index"):
            await TypeNodeOperations.link_type_usage(
                driver, "user", "UnknownEntity", "type"
            )
        with self.assertRaisesRegex(ValueError, "has no required id index"):
            await NamespaceNodeOperations.link_entity_to_namespace(
                driver, "entity", "UnknownEntity", "namespace"
            )
        with self.assertRaisesRegex(ValueError, "unsafe Cypher relationship type"):
            await ClassNodeOperations.link_class_inheritance(
                driver, "child", "parent", "EXTENDS] DELETE r"
            )
        self.assertEqual(driver.calls, [])

    def test_compiler_qualifies_both_endpoint_labels(self) -> None:
        query = compile_relationship_upsert(RelationshipGroup("File", "Function", "CONTAINS"))
        self.assertIn("MATCH (a:File {id: row.source_id, project_id_normalized:", query)
        self.assertIn("MATCH (b:Function {id: row.target_id, project_id_normalized:", query)
        self.assertNotIn("MATCH (a {id:", query)
        self.assertNotIn("MATCH (b {id:", query)

    def test_endpoint_audit_is_scoped_read_only_and_reports_match_counts(self) -> None:
        query = compile_relationship_endpoint_audit(
            RelationshipGroup("File", "Function", "CONTAINS")
        )
        self.assertIn("OPTIONAL MATCH (a:File", query)
        self.assertIn("OPTIONAL MATCH (b:Function", query)
        self.assertIn("project_id_normalized: row.project_id_normalized", query)
        self.assertIn("source_matches", query)
        self.assertIn("target_matches", query)
        self.assertNotIn("MERGE", query)
        self.assertNotIn("SET ", query)

    def test_grouping_lifts_and_normalizes_project_scope(self) -> None:
        groups = group_typed_relations([_relation()])
        row = next(iter(groups.values()))[0]
        self.assertEqual(row["project_id"], "cortext")
        self.assertEqual(row["project_id_normalized"], "cortext")
        self.assertEqual(row["_contract_row_position"], 0)

    def test_duplicate_relation_rows_keep_distinct_audit_ordinals(self) -> None:
        rows = next(iter(group_typed_relations([_relation(), _relation()]).values()))
        self.assertEqual(
            [row["_contract_row_position"] for row in rows],
            [0, 1],
        )

    def test_grouping_includes_both_labels_and_relationship_type(self) -> None:
        groups = group_typed_relations(
            [
                _relation("File", "Function", "CONTAINS"),
                _relation("Type", "Function", "CONTAINS"),
                _relation("File", "Function", "DECLARES"),
            ]
        )
        self.assertEqual(len(groups), 3)
        self.assertEqual(
            {group.state_key for group in groups},
            {
                "relations:File:CONTAINS:Function",
                "relations:Type:CONTAINS:Function",
                "relations:File:DECLARES:Function",
            },
        )

    def test_missing_or_unsafe_identifiers_fail_before_query_execution(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires source_label"):
            group_typed_relations([{"source_id": "a", "target_id": "b", "rel_type": "CALLS"}])
        with self.assertRaisesRegex(ValueError, "unsafe Cypher relationship type"):
            group_typed_relations([_relation(rel_type="CALLS] DELETE r")])
        with self.assertRaisesRegex(ValueError, "has no required id index"):
            group_typed_relations([_relation(source_label="SyntacticallyValidButUnknown")])
        unscoped = _relation()
        unscoped["properties"] = {}
        with self.assertRaisesRegex(ValueError, "requires project_id"):
            group_typed_relations([unscoped])

    def test_shell_and_cplus_fallback_endpoint_labels_are_registered(self) -> None:
        groups = group_typed_relations(
            [
                _relation("ShellScript", "ShellFunction", "CONTAINS"),
                _relation("Type", "Type", "CONTAINS"),
            ]
        )
        self.assertEqual(
            {group.state_key for group in groups},
            {
                "relations:ShellScript:CONTAINS:ShellFunction",
                "relations:Type:CONTAINS:Type",
            },
        )

    async def test_writer_emits_one_labeled_query_per_label_triple(self) -> None:
        driver = _RecordingDriver()
        writer = LanguageCodeWriter(driver, database="code", batch_size=100)
        written = await writer.write_relations_typed(
            [
                _relation("File", "Function", "CONTAINS"),
                _relation("Type", "Function", "CONTAINS"),
            ]
        )

        self.assertEqual(written, 2)
        audit_queries = [query for query, _, _ in driver.calls if "OPTIONAL MATCH" in query]
        mutation_queries = [query for query, _, _ in driver.calls if "MERGE (a)-[r:" in query]
        self.assertEqual(len(audit_queries), 2)
        self.assertEqual(len(mutation_queries), 2)
        queries = "\n".join(query for query, _, _ in driver.calls)
        self.assertIn("MATCH (a:File", queries)
        self.assertIn("MATCH (a:Type", queries)

    async def test_writer_fails_when_endpoint_counts_do_not_reconcile(self) -> None:
        driver = _RecordingDriver()

        async def unresolved(query, parameters=None, database=None):
            if "OPTIONAL MATCH" in query:
                return (
                    [
                        {
                            "source_id": "same-id",
                            "target_id": "same-id",
                            "project_id_normalized": "cortext",
                            "source_matches": 1,
                            "target_matches": 0,
                        }
                    ],
                    [],
                    None,
                )
            return ([{"count": 0}], [], None)

        driver.execute_query = unresolved
        writer = LanguageCodeWriter(driver, database="code")
        with self.assertRaisesRegex(RuntimeError, "target_matches.*0"):
            await writer.write_relations_typed([_relation()])

    async def test_write_all_infers_legacy_producer_labels_before_streaming(self) -> None:
        driver = _RecordingDriver()
        writer = LanguageCodeWriter(driver, database="code")
        await writer.write_all(
            files=[{"id": "main.py", "project_id": "demo"}],
            functions=[{"id": "demo::run"}],
            relations=[
                {
                    "source_id": "main.py",
                    "target_id": "demo::run",
                    "rel_type": "CONTAINS",
                    "properties": {},
                }
            ],
        )

        queries = "\n".join(query for query, _, _ in driver.calls)
        self.assertIn("MATCH (a:File {id: row.source_id, project_id_normalized:", queries)
        self.assertIn("MATCH (b:Function {id: row.target_id, project_id_normalized:", queries)

    async def test_write_all_rejects_unresolvable_labels_before_any_mutation(self) -> None:
        driver = _RecordingDriver()
        writer = LanguageCodeWriter(driver, database="code")
        with self.assertRaisesRegex(ValueError, "cannot infer target_label"):
            await writer.write_all(
                files=[{"id": "main.py", "project_id": "demo"}],
                relations=[
                    {
                        "source_id": "main.py",
                        "target_id": "missing",
                        "rel_type": "IMPORTS",
                        "properties": {},
                    }
                ],
            )
        self.assertEqual(driver.calls, [])

    async def test_write_all_reports_external_inheritance_as_optional(self) -> None:
        driver = _RecordingDriver()
        writer = LanguageCodeWriter(driver, database="code")
        counts = await writer.write_all(
            types=[{"id": "Child"}],
            relations=[
                {
                    "source_id": "Child",
                    "target_id": "ExternalBase",
                    "rel_type": "INHERITS_FROM",
                    "properties": {"base_name": "ExternalBase"},
                }
            ],
        )

        self.assertEqual(counts["unresolved_relations"], 1)
        self.assertFalse(any("INHERITS_FROM" in query for query, _, _ in driver.calls))


if __name__ == "__main__":
    unittest.main()
