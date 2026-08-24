from __future__ import annotations

import asyncio
import json
import subprocess
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
    def _run(
        self,
        root: Path,
        parsers: str,
        run_side_effect,
        *,
        finalize_side_effect=None,
    ):
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
                side_effect=finalize_side_effect,
            ),
            patch.object(
                incremental_sync,
                "_run",
                side_effect=run_side_effect,
            ),
        ):
            result = asyncio.run(incremental_sync._run_incremental(args))
        return result, json.loads(summary_path.read_text(encoding="utf-8"))

    @staticmethod
    def _child_failure(command, message: str) -> subprocess.CalledProcessError:
        return subprocess.CalledProcessError(
            1,
            command,
            output="",
            stderr=message,
        )

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
                    raise self._child_failure(command, "python graph failed")
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
                    raise self._child_failure(command, "framework failed")
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
                    raise self._child_failure(command, "python embedding failed")
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
                    raise self._child_failure(command, "topology failed")
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
                    raise self._child_failure(
                        command,
                        "Traceback (most recent call last):\nRuntimeError: python graph failed",
                    )
                if name == "js_analyzer.py" and graph_disabled:
                    raise self._child_failure(command, "js embedding failed")
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

    def test_highest_severity_failure_controls_final_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.py").write_text("print('python')\n", encoding="utf-8")
            (root / "main.js").write_text("console.log('js');\n", encoding="utf-8")

            def run_or_fail(command, **kwargs):
                name = Path(command[1]).name
                graph_disabled = (kwargs.get("env") or {}).get("CORTEX_DISABLE_GRAPH") == "1"
                if name == "python_analyzer.py" and not graph_disabled:
                    raise self._child_failure(
                        command,
                        "Traceback (most recent call last):\nRuntimeError: python defect",
                    )
                if name == "js_analyzer.py" and graph_disabled:
                    raise self._child_failure(
                        command,
                        "relationship batch integrity failure: expected=2 matched=1",
                    )
                return ""

            result, summary = self._run(root, "python,js", run_or_fail)

        self.assertEqual(result, 1)
        self.assertEqual(
            [item["failure_class"] for item in summary["component_failures"]],
            ["internal_defect", "integrity"],
        )
        self.assertTrue(all(item["continued"] for item in summary["component_failures"]))
        self.assertEqual(
            len(summary["component_failures"][0]["details"]["issue_fingerprint"]),
            16,
        )
        self.assertEqual(len(summary["component_failures"][0]["artifacts"]), 1)
        self.assertEqual(
            summary["run_result"]["failure"]["class"],
            "integrity",
        )

    def test_journal_failure_stops_before_later_analyzers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.py").write_text("print('python')\n", encoding="utf-8")
            (root / "main.js").write_text("console.log('js');\n", encoding="utf-8")
            calls = []

            def run_successfully(command, **kwargs):
                calls.append(Path(command[1]).name)
                return ""

            result, summary = self._run(
                root,
                "python,js",
                run_successfully,
                finalize_side_effect=RuntimeError("journal did not drain"),
            )

        self.assertEqual(result, 1)
        self.assertEqual(calls, ["python_analyzer.py"])
        self.assertEqual(summary["primary_parsers"][0]["status"], "failed")
        self.assertEqual(summary["parsers"][0]["status"], "failed")
        self.assertEqual(
            summary["component_failures"][0]["component"],
            "primary:python",
        )
        self.assertFalse(summary["component_failures"][0]["continued"])
        self.assertEqual(
            summary["component_failures"][0]["failure_class"],
            "journal_recovery",
        )
        self.assertEqual(
            summary["run_result"]["failure"]["class"],
            "journal_recovery",
        )


if __name__ == "__main__":
    unittest.main()
