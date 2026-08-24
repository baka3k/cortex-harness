import ast
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UNIFIED_MCP = ROOT / "code-tiny" / "mcp" / "unified_mcp.py"
DOC_MCP = ROOT / "doc-tiny" / "mcp_graph_rag.py"


def _constant_subscript_assignments(path: Path, mapping_name: str) -> dict[str, object]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    assignments: dict[str, object] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not (
            isinstance(target, ast.Subscript)
            and isinstance(target.value, ast.Name)
            and target.value.id == mapping_name
            and isinstance(target.slice, ast.Constant)
            and isinstance(target.slice.value, str)
            and isinstance(node.value, ast.Constant)
        ):
            continue
        assignments[target.slice.value] = node.value.value
    return assignments


def _fastmcp_constructor_keywords(path: Path) -> dict[str, object]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "FastMCP"
    ]
    assert len(calls) == 1
    return {
        keyword.arg: keyword.value.value
        for keyword in calls[0].keywords
        if keyword.arg is not None and isinstance(keyword.value, ast.Constant)
    }


class McpHttpResilienceTests(unittest.TestCase):
    def test_falkor_mcp_startup_and_driver_never_import_neo4j(self):
        probe = textwrap.dedent(
            f"""
            import asyncio
            import builtins
            import os
            import runpy
            import sys
            import tempfile
            from pathlib import Path

            real_import = builtins.__import__

            def reject_neo4j(name, globals=None, locals=None, fromlist=(), level=0):
                forbidden = (
                    name == "neo4j"
                    or name.startswith("neo4j.")
                    or name.endswith(".neo4j_driver")
                    or name.endswith(".require_neo4j")
                )
                if forbidden:
                    raise AssertionError(f"Falkor path attempted forbidden import: {{name}}")
                return real_import(name, globals, locals, fromlist, level)

            builtins.__import__ = reject_neo4j
            os.environ["GRAPH_PROVIDER"] = "falkordb"
            os.environ["CODE_GRAPH_PROVIDER"] = "falkordb"
            os.environ["MCP_GRAPH_PROVIDER"] = "falkordb"
            os.environ["NEO4J_URI"] = "bolt://must-not-leak:7687"
            os.environ["NEO4J_USER"] = "must-not-leak"
            os.environ["NEO4J_PASS"] = "must-not-leak"
            os.environ["NEO4J_DB"] = "must-not-leak"

            runpy.run_path({str(UNIFIED_MCP)!r}, run_name="graph_mcp_import_probe")
            assert not any(key.startswith("NEO4J_") for key in os.environ), {{
                key: "<redacted>"
                for key in os.environ
                if key.startswith("NEO4J_")
            }}

            from tools.graph.core.base import GraphProvider
            from tools.graph.core.factory import GraphDriverFactory

            with tempfile.TemporaryDirectory() as directory:
                driver = asyncio.run(
                    GraphDriverFactory.create_driver(
                        GraphProvider.FALKORDB,
                        {{
                            "path": str(Path(directory) / "falkor.rdb"),
                            "graph": "falkor-primary",
                        }},
                    )
                )
                assert driver.provider is GraphProvider.FALKORDB
                records, _, _ = asyncio.run(
                    driver.execute_query("RETURN 1 AS value", database="falkor-primary")
                )
                assert records and records[0]["value"] == 1, records
                driver.close()
            """
        )

        completed = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_neo4j_dependency_is_loaded_only_after_explicit_selection(self):
        probe = textwrap.dedent(
            f"""
            import asyncio
            import builtins
            import sys

            sys.path.insert(0, {str(ROOT / "code-tiny")!r})

            attempts = []
            real_import = builtins.__import__

            def hide_neo4j(name, globals=None, locals=None, fromlist=(), level=0):
                if name == "neo4j" or name.startswith("neo4j."):
                    attempts.append(name)
                    error = ModuleNotFoundError(f"blocked optional dependency: {{name}}")
                    error.name = name
                    raise error
                return real_import(name, globals, locals, fromlist, level)

            builtins.__import__ = hide_neo4j

            from tools.graph.core.base import GraphProvider
            from tools.graph.core.factory import GraphDriverFactory

            assert attempts == [], f"Neo4j loaded before provider selection: {{attempts}}"
            try:
                asyncio.run(
                    GraphDriverFactory.create_driver(
                        GraphProvider.NEO4J,
                        {{
                            "uri": "bolt://127.0.0.1:7687",
                            "user": "neo4j",
                            "password": "secret",
                        }},
                    )
                )
            except ImportError as exc:
                assert "cortex-harness[neo4j]" in str(exc), str(exc)
            else:
                raise AssertionError("Selecting Neo4j without its extra did not fail")
            assert attempts, "Explicit Neo4j selection never tried to load its dependency"
            """
        )

        completed = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_unified_mcp_avoids_sse_responses_for_local_streamable_http(self):
        options = _constant_subscript_assignments(UNIFIED_MCP, "kwargs")

        self.assertIs(options.get("stateless_http"), True)
        self.assertIs(options.get("json_response"), True)

    def test_doc_mcp_avoids_sse_responses_for_local_streamable_http(self):
        options = _fastmcp_constructor_keywords(DOC_MCP)

        self.assertIs(options.get("stateless_http"), True)
        self.assertIs(options.get("json_response"), True)
