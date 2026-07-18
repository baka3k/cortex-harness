import asyncio
import contextlib
import io
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.graph.writer.web_framework_writer import WebFrameworkWriter  # noqa: E402
from tools.web_framework.pipeline import analyze_project  # noqa: E402
from tools.web_framework.web_framework_analyzer import main as overlay_main  # noqa: E402


FIXTURE = ROOT / "tests" / "fixtures" / "web-framework-application"


class RecordingDriver:
    def __init__(self):
        self.calls = []

    async def execute_query(self, query, params, database=None):
        self.calls.append((query, params, database))
        return [{"count": len(params.get("rows", []))}], None, None


class WebFrameworkOverlayTest(unittest.TestCase):
    def test_cli_dry_run_emits_fixture_backed_graph_rows(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = overlay_main([
                "--root", str(FIXTURE), "--project-id", "fixture",
                "--framework", "fastapi_django", "--dry-run",
            ])
        payload = json.loads(output.getvalue())

        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["nodes"])
        self.assertTrue(payload["relationships"])

    def test_fastapi_and_django_extract_real_endpoint_facts(self):
        result = analyze_project(FIXTURE, "fixture", ("fastapi", "django"))
        endpoints = {(item.framework, item.http_method, item.path): item for item in result.endpoints}

        fastapi = endpoints[("fastapi", "GET", "/users/{user_id}")]
        django = endpoints[("django", "ALL", "/health/")]
        django_class = endpoints[("django", "ALL", "/users/")]
        self.assertEqual(fastapi.handler_name, "get_user")
        self.assertEqual(fastapi.resolution_status, "resolved")
        self.assertEqual(django.handler_name, "health")
        self.assertTrue(django.handler_file.endswith("django_views.py"))
        self.assertEqual(django_class.handler_name, "UserListView")
        self.assertEqual(django_class.handler_label, "Class")
        self.assertEqual(django_class.resolution_status, "resolved")

    def test_unresolved_handler_does_not_emit_false_handles_edge(self):
        from tools.web_framework.models import EndpointFact, WebAnalysisResult

        endpoint = EndpointFact(
            endpoint_id="endpoint", project_id="fixture", framework="django",
            http_method="GET", path="/missing", file_path="urls.py", start_line=1,
            handler_name="MissingView", resolution_status="unresolved",
        )
        nodes, relationships = WebAnalysisResult("fixture", (endpoint,)).graph_rows()

        self.assertEqual(len(nodes), 1)
        self.assertEqual(relationships, [])

    def test_express_js_extracts_named_handler(self):
        result = analyze_project(FIXTURE, "fixture", ("express_js",))

        self.assertEqual(len(result.endpoints), 1)
        endpoint = result.endpoints[0]
        self.assertEqual((endpoint.http_method, endpoint.path), ("GET", "/orders"))
        self.assertEqual(endpoint.handler_name, "listOrders")
        self.assertEqual(endpoint.resolution_status, "resolved")

    def test_laravel_extracts_controller_method(self):
        result = analyze_project(FIXTURE, "fixture", ("laravel",))

        self.assertEqual(len(result.endpoints), 1)
        endpoint = result.endpoints[0]
        self.assertEqual((endpoint.http_method, endpoint.path), ("POST", "/users"))
        self.assertEqual(endpoint.handler_name, "store")
        self.assertEqual(endpoint.handler_scope, "UserController")
        self.assertTrue(endpoint.handler_file.endswith("UserController.php"))

    def test_graph_rows_and_writer_use_endpoint_handler_contract(self):
        result = analyze_project(FIXTURE, "fixture", ("fastapi", "django", "express_js", "laravel"))
        node_rows, relationship_rows = result.graph_rows()
        driver = RecordingDriver()
        summary = asyncio.run(
            WebFrameworkWriter(driver, database="fixture").write_all(
                node_rows=node_rows,
                relationship_rows=relationship_rows,
            )
        )

        self.assertEqual(summary["nodes"], len(node_rows))
        self.assertEqual(summary["relationships"], len(relationship_rows))
        self.assertTrue(all(row["project_id"] == "fixture" for row in node_rows))
        self.assertTrue(all(row["type"] == "HANDLES" for row in relationship_rows))
        combined_queries = "\n".join(query for query, _, _ in driver.calls)
        self.assertIn("MERGE (node:ApiEndpoint", combined_queries)
        self.assertIn("MERGE (endpoint)-[rel:HANDLES]", combined_queries)


if __name__ == "__main__":
    unittest.main()
