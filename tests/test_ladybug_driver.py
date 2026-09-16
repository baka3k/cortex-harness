"""LadybugDB graph provider tests.

Pure-rewrite tests always run; live-database tests are skipped when the
``ladybug`` package is not installed.

Run from the repo root::

    pytest tests/test_ladybug_driver.py
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for entry in (REPO_ROOT / "code-tiny", REPO_ROOT / "doc-tiny"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from tools.graph.core.base import GraphProvider
from tools.graph.core.provider_contract import (
    isolate_graph_provider_environment,
    normalize_graph_provider_name,
)
from tools.graph.schema import ladybug_schema


try:
    import ladybug as _ladybug  # noqa: F401

    LADYBUG_AVAILABLE = True
except ImportError:  # pragma: no cover - environment dependent
    LADYBUG_AVAILABLE = False


class ProviderRegistrationTests(unittest.TestCase):
    def test_enum_value(self) -> None:
        self.assertEqual(GraphProvider.LADYBUG.value, "ladybug")

    def test_normalize_accepts_ladybug_aliases(self) -> None:
        for alias in ("ladybug", "LadybugDB", "ladybug-db", "LADYBUGDB"):
            self.assertEqual(normalize_graph_provider_name(alias), "ladybug")

    def test_normalize_rejects_unknown(self) -> None:
        with self.assertRaises(ValueError):
            normalize_graph_provider_name("graphdb")

    def test_isolation_strips_other_providers_for_ladybug(self) -> None:
        env = {
            "GRAPH_PROVIDER": "ladybug",
            "FALKORDB_PATH": "/tmp/x.rdb",
            "NEO4J_URI": "bolt://x",
            "FALKORDB_GRAPH": "proj_graph",
            "DOC_FALKORDB_GRAPH": "proj_doc",
            "LADYBUG_PATH": "/tmp/x.lbdb",
        }
        provider = isolate_graph_provider_environment(
            env, "ladybug", scoped_key="CODE_GRAPH_PROVIDER"
        )
        self.assertEqual(provider, "ladybug")
        self.assertEqual(env["GRAPH_PROVIDER"], "ladybug")
        self.assertEqual(env["CODE_GRAPH_PROVIDER"], "ladybug")
        self.assertNotIn("FALKORDB_PATH", env)
        self.assertNotIn("NEO4J_URI", env)
        # Graph names are provider-neutral carriers ladybug keeps reading.
        self.assertEqual(env["FALKORDB_GRAPH"], "proj_graph")
        self.assertEqual(env["DOC_FALKORDB_GRAPH"], "proj_doc")
        self.assertEqual(env["LADYBUG_PATH"], "/tmp/x.lbdb")

    def test_isolation_strips_ladybug_for_falkordb(self) -> None:
        env = {"GRAPH_PROVIDER": "falkordb", "LADYBUG_PATH": "/tmp/x.lbdb"}
        provider = isolate_graph_provider_environment(
            env, "falkordb", scoped_key="CODE_GRAPH_PROVIDER"
        )
        self.assertEqual(provider, "falkordb")
        self.assertNotIn("LADYBUG_PATH", env)


class SchemaCompilationTests(unittest.TestCase):
    def test_node_ddl_contains_pk_and_spill(self) -> None:
        ddl = ladybug_schema.compile_node_ddl("Function")
        self.assertIn("CREATE NODE TABLE IF NOT EXISTS `Function`", ddl)
        self.assertIn("`id` STRING PRIMARY KEY", ddl)
        self.assertIn(f"`{ladybug_schema.SPILL_PROPERTY}` STRING", ddl)
        self.assertIn("`start_line` INT64", ddl)

    def test_node_ddl_backticks_reserved_columns(self) -> None:
        ddl = ladybug_schema.compile_rel_ddl("HAS_STEP", (("Workflow", "Function"),))
        self.assertIn("`order` INT64", ddl)

    def test_pk_overrides_follow_identity_registry(self) -> None:
        for label, pk in (
            ("Project", "project_id"),
            ("Workflow", "workflow_id"),
            ("CallSite", "site_id"),
            ("BuildConfiguration", "config_fingerprint"),
        ):
            ddl = ladybug_schema.compile_node_ddl(label)
            self.assertIn(f"`{pk}` STRING PRIMARY KEY", ddl)

    def test_paragraph_uses_composite_merge_key(self) -> None:
        ddl = ladybug_schema.compile_node_ddl("Paragraph")
        self.assertIn("`__pk` STRING PRIMARY KEY", ddl)
        self.assertIn("`source_id` STRING", ddl)
        merge = ladybug_schema.composite_merge_key("Paragraph")
        self.assertEqual(merge, ("source_id", "paragraph_id"))
        value = ladybug_schema.merge_key_composite_value(
            "Paragraph", {"source_id": "doc1", "paragraph_id": 3}
        )
        self.assertEqual(value, "doc1::3")

    def test_unknown_label_falls_back_to_default_shape(self) -> None:
        ddl = ladybug_schema.compile_node_ddl("UnknownThing")
        self.assertIn("`id` STRING PRIMARY KEY", ddl)
        self.assertIn("`project_id_normalized` STRING", ddl)

    def test_rel_ddl_multi_endpoint(self) -> None:
        ddl = ladybug_schema.compile_rel_ddl(
            "CALLS", ladybug_schema.rel_spec("CALLS")[0]
        )
        self.assertIn("FROM `Function` TO `Function`", ddl)


class QueryRewriteTests(unittest.TestCase):
    """Rewrite pipeline tests that need no database."""

    @classmethod
    def setUpClass(cls) -> None:
        if not LADYBUG_AVAILABLE:
            raise unittest.SkipTest("ladybug package not installed")
        from tools.graph.driver.ladybug_driver import LadybugDBDriver

        cls.tempdir = tempfile.mkdtemp(prefix="ladybug-rewrite-")
        cls.driver = LadybugDBDriver(
            path=str(Path(cls.tempdir) / "rewrite.lbdb"),
            database="test",
            instance_id="t",
            owner_id="t",
        )

    @classmethod
    def tearDownClass(cls) -> None:
        driver = getattr(cls, "driver", None)
        if driver is not None:
            driver.close()
        shutil.rmtree(cls.tempdir, ignore_errors=True)

    def prepare(self, query: str, params=None):
        return self.driver._prepare_ladybug_query(query, dict(params or {}))

    def test_reserved_param_rename(self) -> None:
        query, params = self.prepare(
            "MATCH (a) WHERE a.id = $start MATCH (b) WHERE b.id = $end "
            "RETURN a, b LIMIT $limit",
            {"start": "x", "end": "y", "limit": 5},
        )
        self.assertNotIn("$end", query)
        self.assertNotIn("$start ", query)
        self.assertIn("$__lb_end", query)
        self.assertIn("$__lb_start", query)
        self.assertIn("$__lb_limit", query)  # reserved in LIMIT position too
        self.assertEqual(params["__lb_end"], "y")
        self.assertEqual(params["__lb_start"], "x")
        self.assertEqual(params["__lb_limit"], 5)

    def test_list_comprehension_lowered_client_side(self) -> None:
        query, params = self.prepare(
            "WITH [t IN $modules | toLower(t)] AS modules RETURN modules",
            {"modules": ["A.py", "B.PY"]},
        )
        self.assertNotIn("[t IN", query)
        self.assertIn("WITH $modules AS modules", query)
        self.assertEqual(params["modules"], ["a.py", "b.py"])

    def test_call_guard_flattened(self) -> None:
        query, _ = self.prepare(
            "UNWIND $rows AS row "
            "CALL { WITH row MATCH (c:Function {id: row.x}) RETURN c LIMIT 1 } "
            "RETURN count(c)",
            {"rows": [{"x": "1"}]},
        )
        self.assertNotIn("CALL {", query)
        self.assertIn("MATCH (c:Function {id: row.x})", query)

    def test_set_merge_row_expands_columns_with_spill(self) -> None:
        query, params = self.prepare(
            "UNWIND $rows AS row MERGE (node:SpringBean {id: row.id}) "
            "SET node += row RETURN count(node)",
            {"rows": [{"id": "s1", "name": "svc", "extra": 1}]},
        )
        self.assertNotIn("+=", query)
        self.assertIn("node.name = row.name", query)
        self.assertIn(f"node.{ladybug_schema.SPILL_PROPERTY} = row.__spill", query)
        row = params["rows"][0]
        self.assertEqual(json.loads(row["__spill"]), {"extra": 1})

    def test_set_merge_row_props_becomes_spill(self) -> None:
        query, params = self.prepare(
            "UNWIND $rows AS row "
            "MATCH (a:Function {id: row.a}) MATCH (b:Function {id: row.b}) "
            "MERGE (a)-[r:CALLS {site_id: row.s}]->(b) "
            "SET r += row.props RETURN count(r)",
            {"rows": [{"a": "1", "b": "2", "s": "s1", "props": {"ev": "x"}}]},
        )
        self.assertNotIn("+=", query)
        self.assertIn(
            f"r.{ladybug_schema.SPILL_PROPERTY} = row.__props_spill", query
        )
        self.assertEqual(json.loads(params["rows"][0]["__props_spill"]), {"ev": "x"})

    def test_set_merge_coalesce_props(self) -> None:
        query, params = self.prepare(
            "UNWIND $rows AS row MERGE (a)-[r:REL {id: row.id}]->(b) "
            "SET r += coalesce(row.properties, {}) RETURN count(r)",
            {"rows": [{"id": "1", "properties": {"conf": 0.5}}]},
        )
        self.assertNotIn("+=", query)
        self.assertEqual(
            json.loads(params["rows"][0]["__properties_spill"]), {"conf": 0.5}
        )

    def test_set_merge_param_map(self) -> None:
        query, params = self.prepare(
            "MERGE (p:Paragraph {source_id: $s, paragraph_id: $i}) SET p += $props",
            {"s": "d", "i": 1, "props": {"section": "x"}},
        )
        self.assertNotIn("+=", query)
        self.assertIn(f"p.{ladybug_schema.SPILL_PROPERTY} = $__spill_props", query)
        self.assertEqual(json.loads(params["__spill_props"]), {"section": "x"})

    def test_foreach_detach_delete_unwound(self) -> None:
        query, _ = self.prepare(
            "MATCH (n:File) WITH collect(n) AS nodes "
            "FOREACH (node IN nodes | DETACH DELETE node)",
        )
        self.assertNotIn("FOREACH", query)
        self.assertIn("UNWIND nodes AS node DETACH DELETE node", query)

    def test_datetime_substituted(self) -> None:
        query, params = self.prepare(
            "MERGE (n:File {id: $id}) SET n.updated_at = datetime()",
            {"id": "f"},
        )
        self.assertNotIn("datetime()", query)
        self.assertIn("$__ladybug_now", query)
        self.assertTrue(params["__ladybug_now"].endswith("Z"))

    def test_labels_index_rewritten(self) -> None:
        query, _ = self.prepare(
            "MATCH (e) RETURN labels(e)[0] AS entity_label LIMIT 5"
        )
        self.assertIn("label(e) AS entity_label", query)

    def test_project_scope_predicate_untouched(self) -> None:
        template = (
            "MATCH (n:Function) WHERE ($project_id IS NULL OR "
            "n.project_id_normalized STARTS WITH $project_id_normalized) "
            "RETURN n LIMIT $limit"
        )
        query, params = self.prepare(
            template, {"project_id": None, "limit": 5}
        )
        # R1-R4 contract: the predicate must survive byte-identically and the
        # normalized sibling parameter is injected.
        self.assertIn("($project_id IS NULL OR", query)
        self.assertIn("STARTS WITH $project_id_normalized)", query)
        self.assertIsNone(params["project_id_normalized"])

    def test_set_merge_rel_row_keeps_typed_columns(self) -> None:
        """mybatis-style ``SET r += row`` must keep rel routing columns."""
        query, params = self.prepare(
            "UNWIND $rows AS row "
            "MATCH (a:Mapper {id: row.mapper_id}) MATCH (b:Table {id: row.table_id}) "
            "MERGE (a)-[r:EXECUTES_SQL]->(b) SET r += row, r.framework = 'mybatis' "
            "RETURN count(r)",
            {
                "rows": [
                    {
                        "mapper_id": "m1",
                        "table_id": "t1",
                        "id": "e1",
                        "project_id": "p1",
                        "project_id_normalized": "p1",
                        "statement": "SELECT 1",
                    }
                ]
            },
        )
        self.assertNotIn("+=", query)
        # Typed rel columns are assigned from the row, not only spilled.
        self.assertIn("r.project_id = row.project_id", query)
        self.assertIn("r.id = row.id", query)
        self.assertIn(f"r.{ladybug_schema.SPILL_PROPERTY} = row.__spill", query)
        row = params["rows"][0]
        self.assertEqual(row["project_id"], "p1")
        spill = json.loads(row["__spill"])
        self.assertEqual(spill["statement"], "SELECT 1")
        # Non-column row fields spill (FalkorDB ``SET r += row`` parity).
        self.assertEqual(spill["mapper_id"], "m1")

    def test_rel_merge_by_id_property_binds(self) -> None:
        """aspnet/servlet-jsp merge rels by ``{id: row.id}`` — live check."""
        self.driver.execute_query_sync(
            "UNWIND $rows AS row MERGE (f:Function {id: row.id}) RETURN count(f)",
            {"rows": [{"id": "n1"}, {"id": "n2"}]},
        )
        records, _, _ = self.driver.execute_query_sync(
            "UNWIND $rows AS row "
            "MATCH (a:Function {id: row.src}) MATCH (b:Function {id: row.dst}) "
            "MERGE (a)-[r:BINDS_LATE {id: row.id}]->(b) "
            "SET r += coalesce(row.properties, {}) RETURN count(r) AS count",
            {"rows": [{"src": "n1", "dst": "n2", "id": "x1", "properties": {"conf": 0.5}}]},
        )
        self.assertEqual(records[0]["count"], 1)

    def test_preserved_marker_never_leaks_into_caller_rows(self) -> None:
        rows = [{"id": "a", "project_id_normalized": "p"}]
        self.driver._prepare_ladybug_query(
            "UNWIND $rows AS row CREATE (n:T {id: row.id}) RETURN count(n)",
            {"rows": rows},
        )
        self.assertNotIn("__lb_preserved_normalized", rows[0])
        self.assertEqual(rows[0]["project_id_normalized"], "p")

    def test_paragraph_merge_gets_synthetic_pk(self) -> None:
        query, params = self.prepare(
            "MERGE (p:Paragraph {source_id: $source_id, paragraph_id: $paragraph_id})",
            {"source_id": "doc1", "paragraph_id": 7},
        )
        self.assertIn("__pk: $__cmp_pk_0", query)
        self.assertEqual(params["__cmp_pk_0"], "doc1::7")


@unittest.skipUnless(LADYBUG_AVAILABLE, "ladybug package not installed")
class LadybugDBDriverLiveTests(unittest.IsolatedAsyncioTestCase):
    """Integration against a real embedded LadybugDB catalog."""

    def setUp(self) -> None:
        from tools.graph.driver.ladybug_driver import LadybugDBDriver

        self.tempdir = tempfile.mkdtemp(prefix="ladybug-live-")
        self.driver = LadybugDBDriver(
            path=str(Path(self.tempdir) / "live.lbdb"),
            database="hyper_graph",
            instance_id="t",
            owner_id="code",
        )

    def tearDown(self) -> None:
        self.driver.close()
        shutil.rmtree(self.tempdir, ignore_errors=True)

    async def test_batch_write_and_project_rules(self) -> None:
        nodes = [
            {
                "id": "fn1",
                "name": "auth",
                "qualified_name": "mod.auth",
                "file_path": "src/bank/main.py",
                "start_line": 10,
                "project_id": "bank_Cplus",
                "project_id_normalized": "bank_cplus",
                "dynamic_payload": {"k": "v"},
            },
            {
                "id": "fn2",
                "name": "pay",
                "project_id": "web_front",
                "project_id_normalized": "web_front",
            },
        ]
        count = await self.driver.batch_write_nodes(nodes, "Function")
        self.assertEqual(count, 2)

        # R1: unscoped sees everything.
        unscoped = await self.driver.find_nodes_by_ids(["fn1", "fn2"])
        self.assertEqual({n["id"] for n in unscoped}, {"fn1", "fn2"})
        # R2-R4: scoped + case-insensitive + prefix.
        scoped = await self.driver.find_nodes_by_ids(
            ["fn1", "fn2"], project_id="BANK"
        )
        self.assertEqual([n["id"] for n in scoped], ["fn1"])
        none_scope = await self.driver.find_nodes_by_ids(
            ["fn1"], project_id="zzz"
        )
        self.assertEqual(none_scope, [])

        node = await self.driver.find_node_by_id("fn1")
        self.assertEqual(node["name"], "auth")
        self.assertEqual(node["start_line"], 10)
        self.assertEqual(node["dynamic_payload"], {"k": "v"})  # spill round-trip

    async def test_calls_edge_with_site_and_spill(self) -> None:
        await self.driver.batch_write_nodes(
            [
                {"id": "a", "project_id": "p1", "project_id_normalized": "p1"},
                {"id": "b", "project_id": "p1", "project_id_normalized": "p1"},
            ],
            "Function",
        )
        # The exact write_calls_with_site writer pattern.
        await self.driver.execute_query(
            """
            UNWIND $rows AS row
            CALL {
                WITH row
                MATCH (caller:Function {id: row.caller_id,
                                        project_id_normalized: row.project_id_normalized})
                RETURN caller
                LIMIT 1
            }
            CALL {
                WITH row
                MATCH (callee:Function {id: row.callee_id,
                                        project_id_normalized: row.project_id_normalized})
                RETURN callee
                LIMIT 1
            }
            MERGE (caller)-[r:CALLS {site_id: row.site_id}]->(callee)
            SET r += row.props, r.updated_at = datetime()
            RETURN count(r) as count
            """,
            {
                "rows": [
                    {
                        "caller_id": "a",
                        "callee_id": "b",
                        "site_id": "s1",
                        "project_id": "p1",
                        "project_id_normalized": "p1",
                        "props": {"evidence": "direct"},
                    }
                ]
            },
        )
        records, _, _ = await self.driver.execute_query(
            "MATCH (a:Function)-[r:CALLS]->(b:Function) RETURN a, b, r"
        )
        self.assertEqual(len(records), 1)
        rel = records[0]["r"]
        self.assertEqual(rel["_type"], "CALLS")
        self.assertEqual(rel["site_id"], "s1")
        self.assertEqual(rel["evidence"], "direct")

    async def test_schema_preflight_via_manifest(self) -> None:
        from tools.graph.schema import CODE_GRAPH_SCHEMA

        result = await self.driver.ensure_schema(CODE_GRAPH_SCHEMA)
        self.assertGreater(result.verified_count, 0)
        indexes = await self.driver.inspect_indexes()
        by_label = {idx["label"] for idx in indexes}
        self.assertIn("Function", by_label)

    async def test_subgraph_and_paths(self) -> None:
        await self.driver.batch_write_nodes(
            [
                {"id": "a", "project_id_normalized": "p", "name": "a"},
                {"id": "b", "project_id_normalized": "p", "name": "b"},
            ],
            "Function",
        )
        await self.driver.batch_write_edges(
            [{"source_id": "a", "target_id": "b", "properties": {"call_type": "d"}}],
            "CALLS",
            "Function",
            "Function",
        )
        paths = await self.driver.query_function_subgraph(
            "a", ["CALLS"], direction="out", max_depth=2, project_id=None
        )
        self.assertEqual(len(paths), 1)
        self.assertEqual({n["id"] for n in paths[0]["nodes"]}, {"a", "b"})
        # find_function_paths uses the reserved-word $end parameter.
        fn_paths = await self.driver.find_function_paths(
            "a", "b", ["CALLS"], max_depth=4, project_id=None
        )
        self.assertEqual(len(fn_paths), 1)

    async def test_self_heal_unknown_label(self) -> None:
        await self.driver.execute_query(
            "UNWIND $rows AS row MERGE (n:TotallyNewLabel {id: row.id}) "
            "SET n += row RETURN count(n)",
            {"rows": [{"id": "x1", "note": "n"}]},
        )
        node = await self.driver.find_node_by_id("x1")
        self.assertIsNotNone(node)
        self.assertEqual(node["note"], "n")

    async def test_verify_connection_and_counts(self) -> None:
        self.assertTrue(await self.driver.verify_connection())
        await self.driver.batch_write_nodes(
            [{"id": "only", "project_id_normalized": "p"}], "Function"
        )
        self.assertEqual(await self.driver.get_node_count("Function"), 1)


@unittest.skipUnless(LADYBUG_AVAILABLE, "ladybug package not installed")
class DocStoreTests(unittest.TestCase):
    def test_normalize_provider_ladybug(self) -> None:
        from graph_store import normalize_provider

        self.assertEqual(normalize_provider("ladybug"), "ladybug")
        self.assertEqual(normalize_provider("LadybugDB"), "ladybug")
        with self.assertRaises(ValueError):
            normalize_provider("nope")

    def test_ladybug_store_session_roundtrip(self) -> None:
        from graph_store import LadybugDBGraphStore
        from tools.graph.driver.ladybug_driver import LadybugDBDriver

        tempdir = tempfile.mkdtemp(prefix="ladybug-docstore-")
        try:
            driver = LadybugDBDriver(
                path=str(Path(tempdir) / "doc.lbdb"),
                database="proj_doc",
                instance_id="t",
                owner_id="doc",
            )
            store = LadybugDBGraphStore(driver, "proj_doc")
            self.assertEqual(store.provider, "ladybug")
            with store.session() as session:
                session.run(
                    "MERGE (d:Document {id: $doc_id}) "
                    "SET d.name = $name, d.project_id = $pid, "
                    "d.project_id_normalized = $pidn",
                    {
                        "doc_id": "d1",
                        "name": "Spec",
                        "pid": "bank",
                        "pidn": "bank",
                    },
                )
                result = session.run(
                    "MATCH (d:Document) WHERE ($project_id IS NULL OR "
                    "d.project_id_normalized STARTS WITH $project_id_normalized) "
                    "RETURN d",
                    {"project_id": "BANK", "project_id_normalized": "bank"},
                )
                self.assertIsNotNone(result.single())
            view = store.for_graph("other_doc")
            self.assertIsNotNone(view)
            self.assertFalse(view._owns_driver)
            store.close()
        finally:
            shutil.rmtree(tempdir, ignore_errors=True)


class StoragePathTests(unittest.TestCase):
    def test_resolve_storage_ladybug_paths(self) -> None:
        from cortex_harness.storage import resolve_storage

        resolved = resolve_storage(Path(self.tempdir) if hasattr(self, "tempdir") else Path.cwd())
        self.assertIn("ladybug", str(resolved.ladybug_code_path))
        self.assertTrue(str(resolved.ladybug_code_path).endswith("data.lbdb"))
        self.assertIn("doc", str(resolved.ladybug_doc_path))
        self.assertEqual(
            resolved.ladybug_path_for_role("code"), resolved.ladybug_code_path
        )
        self.assertEqual(
            resolved.ladybug_path_for_role("doc"), resolved.ladybug_doc_path
        )

    def test_resolve_storage_ladybug_env_override(self) -> None:
        import os

        from cortex_harness.storage import resolve_storage

        tempdir = tempfile.mkdtemp(prefix="ladybug-storage-")
        try:
            old = os.environ.get("LADYBUG_CODE_PATH")
            os.environ["LADYBUG_CODE_PATH"] = str(Path(tempdir) / "custom.lbdb")
            try:
                resolved = resolve_storage(Path(tempdir))
                self.assertEqual(
                    resolved.ladybug_code_path, (Path(tempdir) / "custom.lbdb").resolve()
                )
            finally:
                if old is None:
                    os.environ.pop("LADYBUG_CODE_PATH", None)
                else:
                    os.environ["LADYBUG_CODE_PATH"] = old
        finally:
            shutil.rmtree(tempdir, ignore_errors=True)


class SharedRuntimeKeyTests(unittest.TestCase):
    def test_driver_key_is_path_based(self) -> None:
        from tools.graph.core.shared_runtime import _driver_key

        key = _driver_key(
            GraphProvider.LADYBUG,
            {"path": "/tmp/a.lbdb", "instance_id": "i", "owner_id": "o"},
        )
        self.assertEqual(key[0], "ladybug")
        self.assertTrue(any("a.lbdb" in part for part in key if isinstance(part, str)))


if __name__ == "__main__":
    unittest.main()
