"""Regression tests cho bm25 scoring fix (2026-09-13).

Trước fix: weight ``"bm25"`` được inject vào scorer weights (làm đổi
denominator của ``_normalize_weights``) nhưng giá trị bm25 của candidate
không bao giờ được cộng vào score vì ``score_candidate`` chỉ duyệt
``_SIGNAL_KEYS`` (6 signals).
"""

import unittest

from tools.common.retrieval_scorer import RetrievalScorer


class Bm25ScoringTests(unittest.TestCase):
    def test_bm25_weight_contributes_to_score(self):
        candidate = {"node_id": "n1", "semantic": 0.8, "bm25": 0.5}
        scorer = RetrievalScorer(weights={"semantic": 1.0, "bm25": 1.0})
        # normalized: semantic=0.5, bm25=0.5 → score = 0.5*0.8 + 0.5*0.5 = 0.65
        result = scorer.score_candidate(candidate)
        self.assertAlmostEqual(result.score, 0.65, places=9)

    def test_no_bm25_weight_means_no_contribution(self):
        candidate = {"node_id": "n1", "semantic": 0.8, "bm25": 0.5}
        scorer = RetrievalScorer(weights={"semantic": 1.0})
        result = scorer.score_candidate(candidate)
        self.assertAlmostEqual(result.score, 0.8, places=9)

    def test_bm25_weight_dilutes_other_signals(self):
        candidate = {"node_id": "n1", "semantic": 0.8}
        scorer = RetrievalScorer(weights={"semantic": 1.0, "bm25": 0.15})
        # normalized: semantic=1/1.15 — candidate không có bm25 → chỉ phần semantic
        expected = round((1.0 / 1.15) * 0.8, 6)
        result = scorer.score_candidate(candidate)
        self.assertAlmostEqual(result.score, expected, places=9)

    def test_explanation_includes_bm25_when_weighted(self):
        candidate = {"node_id": "n1", "semantic": 0.8, "bm25": 0.5}
        scorer = RetrievalScorer(weights={"semantic": 1.0, "bm25": 1.0})
        result = scorer.score_candidate(candidate, debug=True)
        self.assertIn("bm25", result.explanation)
        self.assertIn("bm25", result.explanation["weighted_contributions"])
        self.assertAlmostEqual(
            result.explanation["weighted_contributions"]["bm25"], 0.25, places=9
        )

    def test_explanation_omits_bm25_without_weight(self):
        candidate = {"node_id": "n1", "semantic": 0.8, "bm25": 0.5}
        scorer = RetrievalScorer(weights={"semantic": 1.0})
        result = scorer.score_candidate(candidate, debug=True)
        self.assertNotIn("bm25", result.explanation)
        self.assertNotIn("bm25", result.explanation["weighted_contributions"])


if __name__ == "__main__":
    unittest.main()
