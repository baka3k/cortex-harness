import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.graph.writer.mybatis_writer import MyBatisFactWriter  # noqa: E402
from tools.graph.writer.servlet_jsp_writer import ServletJspFactWriter  # noqa: E402
from tools.graph.writer.spring_writer import SpringFactWriter  # noqa: E402
from tools.mybatis.models import MyBatisFact, MyBatisRelationship, SourceSpan, graph_property_value  # noqa: E402


class CapturingGraphDriver:
    def __init__(self):
        self.calls = []

    async def execute_query(self, query, parameters=None, database=None):
        self.calls.append((query, parameters or {}, database))
        if "deleted_nodes" in query:
            return ([{"deleted_nodes": 3}], [], None)
        return ([{"count": len((parameters or {}).get("rows", [1]))}], [], None)


class FrameworkGraphContractTest(unittest.IsolatedAsyncioTestCase):
    def test_mybatis_graph_property_value_encodes_structured_values(self):
        self.assertEqual(graph_property_value(["a", 1, True, None]), ["a", 1, True, None])
        self.assertEqual(graph_property_value({"b": 2, "a": [1]}), '{"a":[1],"b":2}')

        fact = MyBatisFact(
            kind="MyBatisStatement",
            stable_id="stmt-1",
            name="findAll",
            source=SourceSpan("src/main/resources/mapper.xml"),
            project_id="project",
            project_name="Project",
            properties={"dynamic": {"if": "name != null"}},
        )
        rel = MyBatisRelationship(
            from_label="MyBatisSqlStatement",
            from_id="stmt-1",
            to_label="DatabaseTable",
            to_id="table-1",
            type="READS_FROM",
            project_id="project",
            source=SourceSpan("src/main/resources/mapper.xml"),
            properties={"columns": [{"name": "id"}]},
        )

        self.assertEqual(fact.to_graph_node()["dynamic"], '{"if":"name != null"}')
        self.assertEqual(rel.to_graph_relationship()["columns"], '[{"name":"id"}]')
        self.assertEqual(rel.to_graph_relationship()["from_label"], "MyBatisSqlStatement")
        self.assertEqual(rel.to_graph_relationship()["to_label"], "DatabaseTable")

    async def test_spring_relationship_writer_groups_by_both_endpoint_labels(self):
        driver = CapturingGraphDriver()
        rows = [
            {
                "from_label": "SpringBean",
                "from_id": "bean-1",
                "to_label": "Function",
                "to_id": "fn-1",
                "type": "SEMANTIC_OF",
                "project_id": "project",
                "properties": {},
            },
            {
                "from_label": "Aspect",
                "from_id": "aspect-1",
                "to_label": "Class",
                "to_id": "class-1",
                "type": "SEMANTIC_OF",
                "project_id": "project",
                "properties": {},
            },
        ]

        written = await SpringFactWriter(driver, database="graph").write_relationships(rows)

        self.assertEqual(written, 2)
        self.assertEqual(len(driver.calls), 2)
        queries = "\n".join(query for query, _, _ in driver.calls)
        self.assertIn("MATCH (a:SpringBean {id: row.from_id})", queries)
        self.assertIn("MATCH (b:Function {id: row.to_id})", queries)
        self.assertIn("MATCH (a:Aspect {id: row.from_id})", queries)
        self.assertIn("MATCH (b:Class {id: row.to_id})", queries)
        self.assertIn("a.project_id = row.project_id", queries)
        self.assertIn("b.project_id = row.project_id", queries)

    async def test_mybatis_relationship_writer_groups_by_both_endpoint_labels(self):
        driver = CapturingGraphDriver()
        rows = [
            {
                "from_label": "MyBatisMapper",
                "from_id": "mapper-1",
                "to_label": "Class",
                "to_id": "class-1",
                "type": "SEMANTIC_OF",
                "project_id": "project",
            },
            {
                "from_label": "MyBatisMapperMethod",
                "from_id": "method-1",
                "to_label": "Function",
                "to_id": "fn-1",
                "type": "SEMANTIC_OF",
                "project_id": "project",
            },
        ]

        written = await MyBatisFactWriter(driver, database="graph").write_relationships(rows)

        self.assertEqual(written, 2)
        self.assertEqual(len(driver.calls), 2)
        queries = "\n".join(query for query, _, _ in driver.calls)
        self.assertIn("MATCH (a:MyBatisMapper {id: row.from_id})", queries)
        self.assertIn("MATCH (b:Class {id: row.to_id})", queries)
        self.assertIn("MATCH (a:MyBatisMapperMethod {id: row.from_id})", queries)
        self.assertIn("MATCH (b:Function {id: row.to_id})", queries)
        self.assertIn("a.project_id = row.project_id", queries)
        self.assertIn("b.project_id = row.project_id", queries)

    async def test_framework_relationship_writers_reject_unallowlisted_labels(self):
        row = {
            "from_label": "InjectedLabel",
            "from_id": "source-1",
            "to_label": "Function",
            "to_id": "target-1",
            "type": "SEMANTIC_OF",
            "project_id": "project",
            "properties": {},
        }
        with self.assertRaisesRegex(ValueError, "relationship source label"):
            await SpringFactWriter(CapturingGraphDriver()).write_relationships([row])
        with self.assertRaisesRegex(ValueError, "relationship source label"):
            await MyBatisFactWriter(CapturingGraphDriver()).write_relationships([row])

    async def test_framework_writers_scope_cleanup_to_framework_and_project(self):
        driver = CapturingGraphDriver()

        spring_result = await SpringFactWriter(driver, database="graph").cleanup_files(
            "project",
            ["src\\main\\java\\Controller.java", "src/main/java/Controller.java", ""],
        )
        mybatis_result = await MyBatisFactWriter(driver, database="graph").cleanup_files(
            "project",
            ["src\\main\\resources\\Mapper.xml"],
        )

        self.assertEqual(spring_result["deleted_nodes"], 3)
        self.assertEqual(mybatis_result["deleted_nodes"], 3)
        spring_query, spring_params, spring_db = driver.calls[0]
        mybatis_query, mybatis_params, mybatis_db = driver.calls[1]
        self.assertIn("n.project_id = $project_id", spring_query)
        self.assertIn("n.framework = 'spring'", spring_query)
        self.assertEqual(spring_params["paths"], ["src/main/java/Controller.java"])
        self.assertEqual(spring_db, "graph")
        self.assertIn("n.project_id = $project_id", mybatis_query)
        self.assertIn("n.framework = 'mybatis'", mybatis_query)
        self.assertEqual(mybatis_params["paths"], ["src/main/resources/Mapper.xml"])
        self.assertEqual(mybatis_db, "graph")

    async def test_servlet_jsp_generation_writer_validates_and_promotes_active_generation(self):
        driver = CapturingGraphDriver()
        writer = ServletJspFactWriter(driver, database="graph")
        node = {
            "id": "servlet-1",
            "semantic_id": "servlet-1",
            "symbol_id": "servlet-1",
            "kind": "Servlet",
            "project_id": "project",
            "project_name": "Project",
            "module_id": "web",
            "framework": "servlet_jsp",
            "generation_id": "gen-1",
            "name": "CatalogServlet",
        }
        rel = {
            "id": "rel-1",
            "semantic_id": "rel-1",
            "from_id": "endpoint-1",
            "to_id": "servlet-1",
            "from_label": "ApiEndpoint",
            "to_label": "Servlet",
            "type": "HANDLES",
            "project_id": "project",
            "module_id": "web",
            "framework": "servlet_jsp",
            "generation_id": "gen-1",
            "confidence": 1.0,
            "resolution_status": "resolved",
            "source_file": "src/main/webapp/WEB-INF/web.xml",
            "start_line": 1,
            "end_line": 1,
            "reason": "web.xml mapping",
        }

        staged = await writer.stage_generation(
            project_id="project",
            module_id="web",
            generation_id="gen-1",
            node_rows=[node],
            relationship_rows=[rel],
        )
        promoted = await writer.promote_generation(
            project_id="project",
            module_id="web",
            generation_id="gen-1",
            snapshot_checksum="checksum",
            coverage_status="complete",
        )

        self.assertEqual(staged, {"nodes": 1, "relationships": 1})
        self.assertEqual(promoted, 1)
        self.assertIn("MERGE (state:ServletJspAnalysisState", driver.calls[-1][0])
        self.assertEqual(driver.calls[-1][1]["generation_id"], "gen-1")

    async def test_servlet_jsp_writer_rejects_unallowlisted_labels(self):
        writer = ServletJspFactWriter(CapturingGraphDriver())
        with self.assertRaisesRegex(ValueError, "Unsupported Servlet/JSP node label"):
            await writer.write_fact_nodes(
                [
                    {
                        "id": "bad-1",
                        "semantic_id": "bad-1",
                        "symbol_id": "bad-1",
                        "kind": "InjectedLabel",
                        "project_id": "project",
                        "module_id": "web",
                        "framework": "servlet_jsp",
                        "generation_id": "gen-1",
                    }
                ],
                "gen-1",
            )


if __name__ == "__main__":
    unittest.main()
