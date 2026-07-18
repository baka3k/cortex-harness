import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from validate_retrieval import (  # noqa: E402
    code_collections_for_project,
    evaluate_symbol_linkage,
)


class ValidateRetrievalTests(unittest.TestCase):
    def test_code_collection_selection_excludes_messages_and_other_projects(self):
        collections = [
            "cortext_abc123__python_functions",
            "cortext-code-typescript-abc123",
            "cortext_mess",
            "cortext",
            "other_abc123__python_functions",
        ]

        self.assertEqual(
            code_collections_for_project(collections, "cortext"),
            [
                "cortext-code-typescript-abc123",
                "cortext_abc123__python_functions",
            ],
        )

    def test_symbol_linkage_reports_missing_project_and_location_mismatches(self):
        vector_points = [
            {
                "symbol_id": "ok",
                "project_id": "demo",
                "file_path": "src/ok.py",
                "start_line": 10,
            },
            {"symbol_id": "missing", "project_id": "demo"},
            {"symbol_id": "wrong-project", "project_id": "demo"},
            {
                "symbol_id": "stale-line",
                "project_id": "demo",
                "file_path": "src/stale.py",
                "start_line": 20,
            },
        ]
        graph_nodes = {
            "ok": {"project_id": "demo", "file_path": "src/ok.py", "start_line": 10},
            "wrong-project": {"project_id": "other"},
            "stale-line": {
                "project_id": "demo",
                "file_path": "src/stale.py",
                "start_line": 99,
            },
        }

        result = evaluate_symbol_linkage(vector_points, graph_nodes, "demo")

        self.assertEqual(result["linked"], 3)
        self.assertEqual(result["missing_graph_ids"], ["missing"])
        self.assertEqual(result["project_mismatches"], ["wrong-project"])
        self.assertEqual(result["location_mismatches"], ["stale-line"])
        self.assertFalse(result["ok"])

    def test_symbol_linkage_passes_when_vector_and_graph_identity_match(self):
        point = {
            "symbol_id": "handler",
            "project_id": "demo",
            "file_path": "src/api.py",
            "start_line": 7,
        }
        result = evaluate_symbol_linkage(
            [point],
            {"handler": dict(point)},
            "demo",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["linked"], 1)


if __name__ == "__main__":
    unittest.main()
