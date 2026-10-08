import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
DOC_TINY = ROOT / "doc-tiny"
MODULE_PATH = DOC_TINY / "graphrag_ingest_langextract.py"

if str(DOC_TINY) not in sys.path:
    sys.path.insert(0, str(DOC_TINY))

SPEC = importlib.util.spec_from_file_location("graphrag_ingest_langextract", MODULE_PATH)
assert SPEC and SPEC.loader
INGEST = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INGEST)

from cortex_harness.dev import _doc_stage_flags, _sync_doc_folder


class PointIdTests(unittest.TestCase):
    def test_point_id_is_deterministic_for_same_inputs(self):
        first = INGEST._point_id("poc_main", "docs__guide.md", 7)
        second = INGEST._point_id("poc_main", "docs__guide.md", 7)
        self.assertEqual(first, second)

    def test_point_id_differs_across_paragraphs_sources_and_projects(self):
        base = INGEST._point_id("poc_main", "docs__guide.md", 7)
        self.assertNotEqual(base, INGEST._point_id("poc_main", "docs__guide.md", 8))
        self.assertNotEqual(base, INGEST._point_id("poc_main", "docs__other.md", 7))
        self.assertNotEqual(base, INGEST._point_id("other", "docs__guide.md", 7))

    def test_point_id_handles_unicode_source_and_missing_project(self):
        value = INGEST._point_id(None, "マスタ__画面レイアウト.md", 0)
        self.assertEqual(value, INGEST._point_id(None, "マスタ__画面レイアウト.md", 0))


class StageFlagMappingTests(unittest.TestCase):
    def test_stage_to_ingestor_flags(self):
        self.assertEqual(_doc_stage_flags("graph"), ["--skip-qdrant"])
        self.assertEqual(_doc_stage_flags("qdrant"), ["--skip-graph"])
        self.assertEqual(_doc_stage_flags("both"), [])


class SyncDocFolderStageSaveTests(unittest.TestCase):
    def _run_folder(self, stage: str, save_calls: list) -> dict:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            document = folder / "guide.md"
            document.write_text("# Guide\n\n" + "word " * 400, encoding="utf-8")

            with mock.patch("cortex_harness.dev._load_state", return_value=None), \
                 mock.patch("cortex_harness.dev._run_with_retry", return_value=0) as retry, \
                 mock.patch("cortex_harness.dev._save_state", side_effect=lambda *a, **k: save_calls.append(a)), \
                 mock.patch("cortex_harness.dev._find_doc_files", return_value=[document]), \
                 mock.patch("cortex_harness.dev._build_file_hashes", return_value={"guide.md": "x"}), \
                 mock.patch("cortex_harness.dev._git_head", return_value=None):
                result = _sync_doc_folder(
                    project_path=folder,
                    folder=str(folder),
                    env={"QDRANT_DOC_PATH": "http://localhost:6333"},
                    python=sys.executable,
                    project={"code": "poc_main"},
                    force_mode="full",
                    entity_provider="gliner",
                    dry_run=False,
                    preview=False,
                    stage=stage,
                )
            self.assertEqual(result["status"], "ok")
            self.assertEqual(retry.call_count, 1)
        return result

    def test_graph_stage_runs_but_never_saves_baseline(self):
        save_calls: list = []
        self._run_folder("graph", save_calls)
        self.assertEqual(save_calls, [])

    def test_qdrant_stage_saves_baseline(self):
        save_calls: list = []
        self._run_folder("qdrant", save_calls)
        self.assertEqual(len(save_calls), 1)

    def test_both_stage_saves_baseline(self):
        save_calls: list = []
        self._run_folder("both", save_calls)
        self.assertEqual(len(save_calls), 1)


class IngestorArgparseTests(unittest.TestCase):
    def test_rejects_skip_qdrant_and_skip_graph_together(self):
        argv = [
            "graphrag_ingest_langextract.py",
            "--md", "whatever.md",
            "--skip-qdrant", "--skip-graph",
        ]
        with mock.patch.object(sys, "argv", argv):
            with self.assertRaises(SystemExit) as caught:
                INGEST.main()
        self.assertIn("mutually exclusive", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
