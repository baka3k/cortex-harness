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
    def test_unified_mcp_imports_without_optional_neo4j(self):
        probe = textwrap.dedent(
            f"""
            import runpy
            import sys

            sys.modules["neo4j"] = None
            sys.modules["neo4j.exceptions"] = None
            runpy.run_path({str(UNIFIED_MCP)!r}, run_name="graph_mcp_import_probe")
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
