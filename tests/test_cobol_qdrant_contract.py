import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
FIXTURE = ROOT / "tests" / "fixtures" / "cobol-application"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.cobol.pipeline import analyze_project  # noqa: E402
from tools.cobol.qdrant import _validate_target, point_id, semantic_documents  # noqa: E402


class CobolQdrantContractTest(unittest.TestCase):
    def test_documents_use_stable_ids_and_project_scoped_payloads(self):
        facts, _ = analyze_project(FIXTURE, project_id="fixture")
        first = semantic_documents(facts)
        second = semantic_documents(facts)
        self.assertEqual(first, second)
        self.assertTrue(first)
        self.assertTrue(all(item["payload"]["project_id"] == "fixture" for item in first))
        self.assertTrue(all(item["payload"]["language"] == "cobol" for item in first))
        self.assertEqual(first[0]["id"], point_id(first[0]["node_id"]))

    def test_qdrant_target_rejects_path_injection_and_embedded_credentials(self):
        _validate_target("http://localhost:6333", "fixture-code-cobol")
        with self.assertRaises(ValueError):
            _validate_target("file:///tmp/qdrant", "fixture")
        with self.assertRaises(ValueError):
            _validate_target("http://user:secret@localhost:6333", "fixture")
        with self.assertRaises(ValueError):
            _validate_target("http://localhost:6333", "../collection")


if __name__ == "__main__":
    unittest.main()
