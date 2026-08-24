from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.sync import incremental_sync  # noqa: E402


class IncrementalSyncContinueOnErrorTests(unittest.TestCase):
    def _run(self, root: Path, parsers: str, run_side_effect):
        summary_path = root / "summary.json"
        args = incremental_sync.parse_args(
            [
                "--root",
                str(root),
                "--project-id",
                "continue-test",
                "--parsers",
                parsers,
                "--full-scan",
                "--sync-mode",
                "both",
                "--qdrant-url",
                str(root / "qdrant"),
                "--falkordb-path",
                str(root / "code.rdb"),
                "--falkordb-graph",
                "continue-test",
                "--no-sync-messages",
                "--summary-path",
                str(summary_path),
            ]
        )
        journal = SimpleNamespace(path=root / "journal.sqlite")
        with (
            patch.object(incremental_sync, "prepare_graph_args", return_value=True),
            patch.object(
                incremental_sync,
                "_ensure_project_repository_graph",
                new=AsyncMock(return_value=None),
            ),
            patch.object(
                incremental_sync,
                "configure_journal_env",
                return_value=journal,
            ),
            patch.object(
                incremental_sync,
                "_resume_configured_journal",
                new=AsyncMock(return_value=0),
            ),
            patch.object(
                incremental_sync,
                "finalize_journal_from_env",
                return_value=None,
            ),
            patch.object(
                incremental_sync,
                "_run",
                side_effect=run_side_effect,
            ),
        ):
            result = asyncio.run(incremental_sync._run_incremental(args))
        return result, json.loads(summary_path.read_text(encoding="utf-8"))

    def test_primary_failure_continues_other_primary_topology_and_embedding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.py").write_text("print('python')\n", encoding="utf-8")
            (root / "main.js").write_text("console.log('js');\n", encoding="utf-8")
            calls = []

            def run_or_fail(command, **kwargs):
                name = Path(command[1]).name
                graph_disabled = (kwargs.get("env") or {}).get("CORTEX_DISABLE_GRAPH") == "1"
                calls.append((name, graph_disabled))
                if name == "python_analyzer.py" and not graph_disabled:
                    raise RuntimeError("python graph failed")
                return ""

            result, summary = self._run(
                root,
                "python,js,project_topology",
                run_or_fail,
            )

        self.assertEqual(result, 1)
        self.assertEqual(
            calls,
            [
                ("python_analyzer.py", False),
                ("js_analyzer.py", False),
                ("topology_analyzer.py", False),
                ("python_analyzer.py", True),
                ("js_analyzer.py", True),
            ],
        )
        self.assertEqual(
            {item["parser"]: item["status"] for item in summary["primary_parsers"]},
            {"python": "failed", "js": "success"},
        )
        self.assertEqual(summary["topology_overlays"][0]["status"], "success")
        self.assertEqual(
            [item["status"] for item in summary["vector_embeddings"]],
            ["success", "success"],
        )
        self.assertEqual(
            summary["component_failures"][0]["component"],
            "primary:python",
        )
        self.assertTrue(summary["state_after"]["dirty"])

    def test_framework_failure_continues_topology_and_embedding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.py").write_text(
                "from fastapi import FastAPI\napp = FastAPI()\n",
                encoding="utf-8",
            )
            calls = []

            def run_or_fail(command, **kwargs):
                name = Path(command[1]).name
                graph_disabled = (kwargs.get("env") or {}).get("CORTEX_DISABLE_GRAPH") == "1"
                calls.append((name, graph_disabled))
                if name == "web_framework_analyzer.py":
                    raise RuntimeError("framework failed")
                return ""

            result, summary = self._run(
                root,
                "python,fastapi_django,project_topology",
                run_or_fail,
            )

        self.assertEqual(result, 1)
        self.assertEqual(
            calls,
            [
                ("python_analyzer.py", False),
                ("web_framework_analyzer.py", False),
                ("topology_analyzer.py", False),
                ("python_analyzer.py", True),
            ],
        )
        self.assertEqual(summary["framework_overlays"][0]["status"], "failed")
        self.assertEqual(summary["topology_overlays"][0]["status"], "success")
        self.assertEqual(summary["vector_embeddings"][0]["status"], "success")
        self.assertEqual(
            summary["component_failures"][0]["component"],
            "overlay:fastapi_django",
        )

    def test_embedding_failure_continues_next_embedding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.py").write_text("print('python')\n", encoding="utf-8")
            (root / "main.js").write_text("console.log('js');\n", encoding="utf-8")
            calls = []

            def run_or_fail(command, **kwargs):
                name = Path(command[1]).name
                graph_disabled = (kwargs.get("env") or {}).get("CORTEX_DISABLE_GRAPH") == "1"
                calls.append((name, graph_disabled))
                if name == "python_analyzer.py" and graph_disabled:
                    raise RuntimeError("python embedding failed")
                return ""

            result, summary = self._run(root, "python,js", run_or_fail)

        self.assertEqual(result, 1)
        self.assertEqual(
            calls,
            [
                ("python_analyzer.py", False),
                ("js_analyzer.py", False),
                ("python_analyzer.py", True),
                ("js_analyzer.py", True),
            ],
        )
        self.assertEqual(
            [item["status"] for item in summary["vector_embeddings"]],
            ["failed", "success"],
        )
        self.assertEqual(
            summary["component_failures"][0]["component"],
            "embedding:python",
        )

    def test_topology_failure_continues_embedding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.py").write_text("print('python')\n", encoding="utf-8")
            calls = []

            def run_or_fail(command, **kwargs):
                name = Path(command[1]).name
                graph_disabled = (kwargs.get("env") or {}).get("CORTEX_DISABLE_GRAPH") == "1"
                calls.append((name, graph_disabled))
                if name == "topology_analyzer.py":
                    raise RuntimeError("topology failed")
                return ""

            result, summary = self._run(
                root,
                "python,project_topology",
                run_or_fail,
            )

        self.assertEqual(result, 1)
        self.assertEqual(
            calls,
            [
                ("python_analyzer.py", False),
                ("topology_analyzer.py", False),
                ("python_analyzer.py", True),
            ],
        )
        self.assertEqual(summary["topology_overlays"][0]["status"], "failed")
        self.assertEqual(summary["vector_embeddings"][0]["status"], "success")

    def test_multiple_failures_are_accumulated_in_execution_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.py").write_text("print('python')\n", encoding="utf-8")
            (root / "main.js").write_text("console.log('js');\n", encoding="utf-8")

            def run_or_fail(command, **kwargs):
                name = Path(command[1]).name
                graph_disabled = (kwargs.get("env") or {}).get("CORTEX_DISABLE_GRAPH") == "1"
                if name == "python_analyzer.py" and not graph_disabled:
                    raise RuntimeError("python graph failed")
                if name == "js_analyzer.py" and graph_disabled:
                    raise RuntimeError("js embedding failed")
                return ""

            result, summary = self._run(root, "python,js", run_or_fail)

        self.assertEqual(result, 1)
        self.assertEqual(
            [item["component"] for item in summary["component_failures"]],
            ["primary:python", "embedding:js"],
        )
        self.assertEqual(
            [item["status"] for item in summary["vector_embeddings"]],
            ["success", "failed"],
        )


if __name__ == "__main__":
    unittest.main()
