from __future__ import annotations

import ast
import os
import subprocess
import sys
import textwrap
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


_CODE_TINY_ROOT = Path(__file__).resolve().parents[2]
_MCP_DIR = _CODE_TINY_ROOT / "mcp"
for path in (_CODE_TINY_ROOT, _MCP_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from services import workflow_service  # noqa: E402


class MCPSourceProviderBoundaryTest(unittest.TestCase):
    def test_mcp_sources_do_not_import_neo4j_implementations(self) -> None:
        forbidden_modules = {
            "neo4j",
            "tools.graph.driver.neo4j_driver",
        }
        violations: list[str] = []

        for source_path in sorted(_MCP_DIR.rglob("*.py")):
            tree = ast.parse(source_path.read_text(encoding="utf-8"), source_path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    imported = [node.module or ""]
                else:
                    imported = []

                for module_name in imported:
                    if (
                        module_name == "neo4j"
                        or module_name.startswith("neo4j.")
                        or module_name in forbidden_modules
                    ):
                        violations.append(
                            f"{source_path.relative_to(_CODE_TINY_ROOT)}:{node.lineno}: "
                            f"{module_name}"
                        )

                if isinstance(node, (ast.Name, ast.ClassDef)):
                    identifier = node.id if isinstance(node, ast.Name) else node.name
                    if identifier == "Neo4jError":
                        violations.append(
                            f"{source_path.relative_to(_CODE_TINY_ROOT)}:{node.lineno}: "
                            "Neo4jError"
                        )

        self.assertEqual(violations, [])


class MCPRuntimeProviderBoundaryTest(unittest.TestCase):
    def _falkor_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "CODE_GRAPH_PROVIDER": "falkordb",
                "GRAPH_PROVIDER": "falkordb",
                "MCP_GRAPH_PROVIDER": "falkordb",
                "MCP_PRELOAD_EMBEDDER": "0",
                "NEO4J_DB": "must-not-leak-into-falkor",
                "FALKORDB_PATH": "/tmp/cortex-provider-boundary-test.rdb",
            }
        )
        env.pop("FALKORDB_GRAPH", None)
        env.pop("FALKORDB_DATABASE", None)
        return env

    def test_falkor_import_and_query_never_attempt_neo4j_import(self) -> None:
        probe = textwrap.dedent(
            """
            import asyncio
            import builtins
            import importlib.util
            import sys
            from pathlib import Path

            attempted_imports = []
            real_import = builtins.__import__

            def tracking_import(name, globals=None, locals=None, fromlist=(), level=0):
                normalized = name.casefold()
                if (
                    normalized == "neo4j"
                    or normalized.startswith("neo4j.")
                    or normalized.endswith(".neo4j_driver")
                    or normalized.endswith(".require_neo4j")
                ):
                    attempted_imports.append(name)
                return real_import(name, globals, locals, fromlist, level)

            builtins.__import__ = tracking_import
            unified_path = Path(sys.argv[1]).resolve()
            spec = importlib.util.spec_from_file_location(
                "provider_boundary_unified_mcp",
                unified_path,
            )
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)

            java_path = unified_path.parent / "java" / "java_mcp.py"
            java_spec = importlib.util.spec_from_file_location(
                "provider_boundary_java_mcp",
                java_path,
            )
            java_module = importlib.util.module_from_spec(java_spec)
            sys.modules[java_spec.name] = java_module
            java_spec.loader.exec_module(java_module)

            assert module.android_backend.DEFAULT_GRAPH_DB == "hyper_graph"
            assert module.cplus_backend.DEFAULT_GRAPH_DB == "hyper_graph"
            assert module.fast_backend.DEFAULT_GRAPH_DB == "hyper_graph"
            assert java_module.DEFAULT_GRAPH_DB == "hyper_graph"

            factory_calls = []

            class FakeFalkorDriver:
                async def list_databases(self):
                    return ["hyper_graph"]

                async def execute_query(self, query, params, database):
                    node = {
                        "id": "function-1",
                        "name": "needle",
                        "qualified_name": "sample.needle",
                        "file_path": "sample.py",
                    }
                    return ([{"n": node}], None, ["n"])

            async def fake_shared_driver(provider, config):
                factory_calls.append((provider, config))
                return FakeFalkorDriver()

            module.cplus_backend._graph_driver = None
            module.cplus_backend.get_shared_graph_driver = fake_shared_driver

            async def run_query():
                return await module._dispatch_tool(
                    "search_functions",
                    {
                        "parser_type": "python",
                        "project_id": "hyper_graph",
                        "query": "needle",
                        "limit": 1,
                    },
                )

            result = asyncio.run(run_query())
            assert result.get("ok") is True, result
            assert result.get("results"), result
            assert factory_calls, "Falkor driver factory was not called"
            assert all(call[0].value == "falkordb" for call in factory_calls)
            assert attempted_imports == [], attempted_imports
            """
        )
        completed = subprocess.run(
            [sys.executable, "-c", probe, str(_MCP_DIR / "unified_mcp.py")],
            cwd=_CODE_TINY_ROOT,
            env=self._falkor_env(),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )

    def test_unknown_provider_fails_closed_during_startup(self) -> None:
        env = self._falkor_env()
        env.update(
            {
                "CODE_GRAPH_PROVIDER": "typo-provider",
                "GRAPH_PROVIDER": "typo-provider",
                "MCP_GRAPH_PROVIDER": "typo-provider",
            }
        )
        probe = textwrap.dedent(
            """
            import importlib.util
            import sys
            from pathlib import Path

            path = Path(sys.argv[1]).resolve()
            spec = importlib.util.spec_from_file_location("invalid_provider_unified", path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            """
        )
        completed = subprocess.run(
            [sys.executable, "-c", probe, str(_MCP_DIR / "unified_mcp.py")],
            cwd=_CODE_TINY_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("Unsupported graph provider", completed.stderr)


class WorkflowProviderBoundaryTest(unittest.IsolatedAsyncioTestCase):
    async def _run_workflow(self, env: dict[str, str]) -> str:
        observed: dict[str, str] = {}

        async def fake_find_screen_workflows(driver, database, **kwargs):
            observed["database"] = database
            return {"paths": []}

        finder_module = types.ModuleType("tools.ts.workflow_finder")
        finder_module.find_screen_workflows = fake_find_screen_workflows
        driver_provider = AsyncMock(return_value=object())

        with (
            patch.dict(sys.modules, {"tools.ts.workflow_finder": finder_module}),
            patch.dict(workflow_service.os.environ, env, clear=True),
        ):
            await workflow_service.run_find_screen_workflows(
                driver_provider,
                {"project_id": "sample", "node_a": "entry"},
            )

        return observed["database"]

    async def test_falkor_default_ignores_neo4j_database(self) -> None:
        database = await self._run_workflow(
            {
                "CODE_GRAPH_PROVIDER": "falkordb",
                "NEO4J_DB": "must-not-leak-into-falkor",
            }
        )
        self.assertEqual(database, "hyper_graph")

    async def test_explicit_neo4j_keeps_legacy_database_override(self) -> None:
        database = await self._run_workflow(
            {
                "CODE_GRAPH_PROVIDER": "neo4j",
                "NEO4J_DB": "legacy-graph",
            }
        )
        self.assertEqual(database, "legacy-graph")


if __name__ == "__main__":
    unittest.main()
