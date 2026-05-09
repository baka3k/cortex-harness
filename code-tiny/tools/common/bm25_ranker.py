"""
bm25_ranker.py
──────────────
Lightweight BM25 keyword ranking for hybrid graph search.

Blends BM25 keyword scores with Qdrant vector scores in
IntelligentRetrievalEngine to improve precision for exact-name queries.

Dependencies
────────────
  rank_bm25 >= 0.2.2  (pip install rank-bm25)
  Falls back gracefully (returns empty scores) if not installed.

Public API
──────────
  ranker = BM25Ranker()
  ranker.build_index(documents, text_field="note")   # or "name", "summary"
  scores = ranker.score(query)                        # {symbol_id: float 0-1}
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


def _tokenize(text: str) -> List[str]:
    """Simple whitespace + punctuation tokeniser."""
    text = text.lower()
    return re.findall(r"[a-z0-9_]+", text)


class BM25Ranker:
    """
    BM25 index over a collection of code nodes.

    All scores are normalised to [0, 1] relative to the top document
    in the current query's result set, so they blend cleanly with
    normalised semantic scores.
    """

    def __init__(self) -> None:
        self._symbol_ids: List[str] = []
        self._bm25: Optional[Any] = None
        self._available = False
        try:
            from rank_bm25 import BM25Okapi  # type: ignore
            self._BM25Okapi = BM25Okapi
            self._available = True
        except ImportError:
            self._BM25Okapi = None

    @property
    def available(self) -> bool:
        return self._available

    def build_index(
        self,
        documents: List[Dict[str, Any]],
        text_field: str = "note",
        id_field: str = "symbol_id",
    ) -> None:
        """
        Build BM25 index from a list of document dicts.

        Args:
            documents: List of dicts with at least *id_field* and *text_field*.
            text_field: Dict key whose value supplies the document text.
            id_field:   Dict key used as the document identifier.
        """
        if not self._available:
            return
        self._symbol_ids = [doc.get(id_field, "") for doc in documents]
        corpus = [_tokenize(doc.get(text_field, "") or "") for doc in documents]
        self._bm25 = self._BM25Okapi(corpus)

    def score(self, query: str) -> Dict[str, float]:
        """
        Score all indexed documents for *query*.

        Returns:
            {symbol_id: normalised_score} where scores are in [0, 1].
            Empty dict if the index has not been built or rank-bm25 is
            unavailable.
        """
        if not self._available or self._bm25 is None:
            return {}
        tokens = _tokenize(query)
        if not tokens:
            return {}
        raw_scores: List[float] = self._bm25.get_scores(tokens).tolist()
        max_score = max(raw_scores) if raw_scores else 0.0
        if max_score <= 0:
            return {}
        return {
            sid: min(raw / max_score, 1.0)
            for sid, raw in zip(self._symbol_ids, raw_scores)
            if raw > 0
        }
