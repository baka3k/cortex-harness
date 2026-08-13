from __future__ import annotations

import asyncio
import json
import os
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
from tools.common.harness_config import load_harness_config  # noqa: E402


class IncrementalSyncPhaseModeTests(unittest.TestCase):
    def test_graph_phase_empty_vector_sentinel_survives_project_config_loading(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / ".cortext-harness" / "config" / "dev.json"
            config.parent.mkdir(parents=True)
            config.write_text(
                json.dumps(
                    {
                        "project": {"code": "phase-test"},
                        "code": {
                            "env": {
                                "QDRANT_CODE_PATH": str(root / "qdrant"),
                                "QDRANT_COLLECTION": "phase-test",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            graph_env = incremental_sync._graph_phase_env(
                {"QDRANT_CODE_PATH": str(root / "qdrant")}
            )

            with patch.dict(os.environ, graph_env, clear=True):
                load_harness_config(str(config))
                self.assertEqual(os.environ["QDRANT_CODE_PATH"], "")
                self.assertEqual(os.environ["QDRANT_COLLECTION"], "")

    def _run_mode(self, mode: str):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        (root / "main.py").write_text("print('phase test')\n", encoding="utf-8")
        summary_path = root / "summary.json"
        args = incremental_sync.parse_args(
            [
                "--root", str(root),
                "--project-id", "phase-test",
                "--project-name", "Phase Test",
                "--parsers", "python,project_topology",
                "--full-scan",
                "--sync-mode", mode,
                "--qdrant-url", str(root / "qdrant"),
                "--falkordb-path", str(root / "code.rdb"),
                "--falkordb-graph", "phase-test",
                "--no-sync-messages",
                "--summary-path", str(summary_path),
            ]
        )
        calls = []

        def capture(command, **kwargs):
            calls.append((Path(command[1]).name, list(command), dict(kwargs.get("env") or {})))
            if (kwargs.get("env") or {}).get("CORTEX_DISABLE_GRAPH") == "1":
                return "[SCAN_RESULT] parser=python files=1 vectors=3 vector_status=success\n"
            return "[SCAN_RESULT] parser=python files=1 functions=0 classes=0\n"

        journal = SimpleNamespace(path=root / "journal.sqlite")
        with (
            patch.object(incremental_sync, "prepare_graph_args", return_value=True),
            patch.object(
                incremental_sync,
                "_ensure_project_repository_graph",
                new=AsyncMock(return_value=None),
            ) as graph_setup,
            patch.object(incremental_sync, "configure_journal_env", return_value=journal) as configure_journal,
            patch.object(
                incremental_sync,
                "_resume_configured_journal",
                new=AsyncMock(return_value=0),
            ),
            patch.object(incremental_sync, "finalize_journal_from_env", return_value=None),
            patch.object(incremental_sync, "_run", side_effect=capture),
        ):
            result = asyncio.run(incremental_sync._run_incremental(args))

        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        return temporary, result, calls, summary, graph_setup, configure_journal

    def test_both_orders_topology_before_graph_disabled_embedding(self):
        temporary, result, calls, summary, graph_setup, configure_journal = self._run_mode("both")
        self.addCleanup(temporary.cleanup)

        self.assertEqual(result, 0)
        self.assertEqual(
            [name for name, _command, _env in calls],
            ["python_analyzer.py", "topology_analyzer.py", "python_analyzer.py"],
        )
        graph_env = calls[0][2]
        vector_env = calls[2][2]
        self.assertEqual(graph_env["QDRANT_CODE_PATH"], "")
        self.assertEqual(graph_env["QDRANT_URL"], "")
        self.assertEqual(vector_env["CORTEX_DISABLE_GRAPH"], "1")
        self.assertEqual(summary["sync_mode"], "both")
        self.assertEqual(summary["vector_embeddings"][0]["vector_status"], "success")
        graph_setup.assert_awaited_once()
        self.assertEqual(configure_journal.call_count, 2)

    def test_graph_mode_does_not_run_embedding_or_expose_qdrant(self):
        temporary, result, calls, summary, graph_setup, _configure_journal = self._run_mode("graph")
        self.addCleanup(temporary.cleanup)

        self.assertEqual(result, 0)
        self.assertEqual(
            [name for name, _command, _env in calls],
            ["python_analyzer.py", "topology_analyzer.py"],
        )
        self.assertTrue(
            all(env["QDRANT_CODE_PATH"] == "" for _name, _command, env in calls)
        )
        self.assertEqual(summary["vector_embeddings"], [])
        graph_setup.assert_awaited_once()

    def test_vector_failure_keeps_successful_topology_visible_in_summary(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "main.py").write_text("print('phase test')\n", encoding="utf-8")
        summary_path = root / "summary.json"
        args = incremental_sync.parse_args(
            [
                "--root", str(root),
                "--project-id", "phase-test",
                "--parsers", "python,project_topology",
                "--full-scan",
                "--sync-mode", "both",
                "--qdrant-url", str(root / "qdrant"),
                "--falkordb-path", str(root / "code.rdb"),
                "--summary-path", str(summary_path),
            ]
        )

        def run_or_fail(_command, **kwargs):
            if (kwargs.get("env") or {}).get("CORTEX_DISABLE_GRAPH") == "1":
                raise RuntimeError("embedding failed")
            return ""

        journal = SimpleNamespace(path=root / "journal.sqlite")
        with (
            patch.object(incremental_sync, "prepare_graph_args", return_value=True),
            patch.object(
                incremental_sync,
                "_ensure_project_repository_graph",
                new=AsyncMock(return_value=None),
            ),
            patch.object(incremental_sync, "configure_journal_env", return_value=journal),
            patch.object(
                incremental_sync,
                "_resume_configured_journal",
                new=AsyncMock(return_value=0),
            ),
            patch.object(incremental_sync, "finalize_journal_from_env", return_value=None),
            patch.object(incremental_sync, "_run", side_effect=run_or_fail),
        ):
            result = asyncio.run(incremental_sync._run_incremental(args))

        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        self.assertEqual(result, 1)
        self.assertEqual(summary["topology_overlays"][0]["status"], "success")
        self.assertEqual(summary["vector_embeddings"][0]["status"], "failed")
        self.assertEqual(summary["primary_parsers"][0]["vector_status"], "failed")

    def test_embedding_mode_never_sets_up_or_journals_graph(self):
        temporary, result, calls, summary, graph_setup, configure_journal = self._run_mode("embedding")
        self.addCleanup(temporary.cleanup)

        self.assertEqual(result, 0)
        self.assertEqual([name for name, _command, _env in calls], ["python_analyzer.py"])
        self.assertEqual(calls[0][2]["CORTEX_DISABLE_GRAPH"], "1")
        self.assertEqual(summary["primary_parsers"], [])
        self.assertEqual(summary["topology_overlays"], [])
        graph_setup.assert_not_awaited()
        configure_journal.assert_not_called()

    def test_specialized_modes_require_full_scan(self):
        for mode in ("graph", "embedding"):
            with self.subTest(mode=mode), self.assertRaises(SystemExit):
                incremental_sync.parse_args(
                    ["--root", str(ROOT), "--sync-mode", mode]
                )


if __name__ == "__main__":
    unittest.main()
