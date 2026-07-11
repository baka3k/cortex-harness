import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY_DIR = ROOT / "code-tiny"
if str(CODE_TINY_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_TINY_DIR))

from tools.common import intelligent_retrieval as ir  # noqa: E402
from tools.common.intelligent_retrieval import IntelligentRetrievalEngine  # noqa: E402


class QdrantCollectionScopeTests(unittest.TestCase):
    def test_resolve_qdrant_collections_prefers_exact_collection(self):
        available = [
            "cortext",
            "cortext_2e464a08ee__python_functions",
            "cortext_mess",
        ]
        with patch.object(ir, "_qdrant_collection_names", return_value=available):
            self.assertEqual(
                ir._resolve_qdrant_collections("http://qdrant", "cortext"),
                ["cortext"],
            )

    def test_resolve_qdrant_collections_expands_project_scope(self):
        available = [
            "cortext_2e464a08ee__python_functions",
            "cortext_2e464a08ee__ts_functions",
            "cortext_mess",
            "other_2e464a08ee__python_functions",
            "cortext_notes",
        ]
        with patch.object(ir, "_qdrant_collection_names", return_value=available):
            self.assertEqual(
                ir._resolve_qdrant_collections("http://qdrant", "cortext"),
                [
                    "cortext_2e464a08ee__python_functions",
                    "cortext_2e464a08ee__ts_functions",
                    "cortext_mess",
                ],
            )

    def test_retrieve_qdrant_searches_scope_collections_and_merges(self):
        available = [
            "cortext_2e464a08ee__python_functions",
            "cortext_2e464a08ee__ts_functions",
        ]
        search_hits = {
            "cortext_2e464a08ee__python_functions": [
                {
                    "id": "py",
                    "score": 0.7,
                    "payload": {"symbol_id": "node-py", "name": "scan_doc"},
                }
            ],
            "cortext_2e464a08ee__ts_functions": [
                {
                    "id": "ts",
                    "score": 0.9,
                    "payload": {"symbol_id": "node-ts", "name": "scanDoc"},
                }
            ],
        }

        with patch.object(ir, "_qdrant_collection_names", return_value=available):
            with patch.object(ir, "_qdrant_search", side_effect=lambda _url, col, _vec, _k: search_hits[col]):
                engine = IntelligentRetrievalEngine(
                    qdrant_url="http://qdrant",
                    collection="cortext",
                    embedder=lambda _query: [0.1, 0.2, 0.3],
                )

                candidates = engine._retrieve_qdrant("scan docs", "cortext", 10)

        self.assertEqual([candidate["node_id"] for candidate in candidates], ["node-ts", "node-py"])


if __name__ == "__main__":
    unittest.main()
