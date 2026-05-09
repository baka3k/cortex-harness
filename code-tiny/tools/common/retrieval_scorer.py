"""
retrieval_scorer.py
───────────────────
Unified multi-signal weighted scoring engine for context-aware code retrieval.

Implements the scoring formula:
    score = w1*semantic + w2*keyword + w3*graph + w4*freshness
            + w5*confidence + w6*usage

All input signals must be pre-normalized to [0, 1] before scoring.
Use ``signal_normalizer.normalize_signals`` to prepare raw values.

Public API
──────────────────────────────────────────────────────
  from tools.common.retrieval_scorer import (
      RetrievalScorer,
      ScoredResult,
      DEFAULT_WEIGHTS,
      WEIGHT_PROFILES,
  )

  scorer = RetrievalScorer(weights=DEFAULT_WEIGHTS)

  results = scorer.score_all(
      candidates=[
          {"node_id": "...", "semantic": 0.91, "keyword": 0.6,
           "graph": 0.7, "freshness": 0.8, "confidence": 0.75, "usage": 0.5},
          ...
      ],
      top_k=10,
      debug=True,   # include per-signal breakdown
  )
  # → List[ScoredResult]

Debug / explainability mode (debug=True)
──────────────────────────────────────────
  Each ScoredResult.explanation contains:
    {
      "semantic":   0.91,
      "keyword":    0.6,
      "graph":      0.7,
      "freshness":  0.8,
      "confidence": 0.75,
      "usage":      0.5,
      "weighted_contributions": {
          "semantic":   0.318,   # 0.35 * 0.91
          "keyword":    0.12,    ...
      }
    }
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ─────────────────────────────────────────────────────────────
# Weight profiles
# ─────────────────────────────────────────────────────────────

#: Default balanced weights.
DEFAULT_WEIGHTS: Dict[str, float] = {
    "semantic":   0.35,
    "keyword":    0.20,
    "graph":      0.20,
    "freshness":  0.10,
    "confidence": 0.10,
    "usage":      0.05,
}

#: Semantic-heavy profile — use when query expresses conceptual intent.
WEIGHTS_SEMANTIC: Dict[str, float] = {
    "semantic":   0.50,
    "keyword":    0.15,
    "graph":      0.10,
    "freshness":  0.05,
    "confidence": 0.15,
    "usage":      0.05,
}

#: Structural profile — use for "who calls X" / dependency queries.
WEIGHTS_STRUCTURAL: Dict[str, float] = {
    "semantic":   0.20,
    "keyword":    0.10,
    "graph":      0.50,
    "freshness":  0.05,
    "confidence": 0.10,
    "usage":      0.05,
}

#: Temporal profile — use for "recently changed" / freshness-driven queries.
WEIGHTS_TEMPORAL: Dict[str, float] = {
    "semantic":   0.20,
    "keyword":    0.15,
    "graph":      0.10,
    "freshness":  0.45,
    "confidence": 0.05,
    "usage":      0.05,
}

#: Named registry for lookup by intent string.
WEIGHT_PROFILES: Dict[str, Dict[str, float]] = {
    "semantic":    WEIGHTS_SEMANTIC,
    "structural":  WEIGHTS_STRUCTURAL,
    "temporal":    WEIGHTS_TEMPORAL,
    "default":     DEFAULT_WEIGHTS,
}

# ─────────────────────────────────────────────────────────────
# ScoredResult
# ─────────────────────────────────────────────────────────────

_SIGNAL_KEYS = ("semantic", "keyword", "graph", "freshness", "confidence", "usage")


@dataclass
class ScoredResult:
    """A ranked candidate with an aggregated score and optional explanation."""
    node_id:     str
    score:       float
    node:        Dict[str, Any]
    explanation: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "node_id": self.node_id,
            "score":   round(self.score, 6),
            "node":    self.node,
        }
        if self.explanation:
            d["explanation"] = self.explanation
        return d


# ─────────────────────────────────────────────────────────────
# Weight validation
# ─────────────────────────────────────────────────────────────


def _normalize_weights(weights: Dict[str, float]) -> Dict[str, float]:
    """
    Return a copy of *weights* normalized so values sum to 1.0.

    This allows callers to pass weights that don't already add up to 1
    (e.g. when a signal is absent and effectively has 0 weight).
    """
    total = sum(weights.values())
    if total <= 0:
        # Fallback: equal weight for all known signals
        n = len(_SIGNAL_KEYS)
        return {k: 1.0 / n for k in _SIGNAL_KEYS}
    return {k: v / total for k, v in weights.items()}


# ─────────────────────────────────────────────────────────────
# RetrievalScorer
# ─────────────────────────────────────────────────────────────


class RetrievalScorer:
    """
    Compute a unified relevance score for each candidate document/code node.

    Parameters
    ──────────
    weights : Signal weight dict.  Values need not sum to 1 — they are
              normalized internally.  Use ``WEIGHT_PROFILES`` for named presets.
    """

    def __init__(self, weights: Optional[Dict[str, float]] = None) -> None:
        self._weights = _normalize_weights(weights or DEFAULT_WEIGHTS)

    # ── main scoring ──────────────────────────────────────────

    def score_candidate(
        self,
        candidate: Dict[str, Any],
        *,
        debug: bool = False,
    ) -> ScoredResult:
        """
        Compute a weighted score for a single *candidate* dict.

        The candidate dict must contain pre-normalized signal values under the
        keys: semantic, keyword, graph, freshness, confidence, usage.
        Missing signals default to 0.0.

        Returns a ``ScoredResult`` with an optional explanation when
        ``debug=True``.
        """
        raw_signals: Dict[str, float] = {
            k: float(candidate.get(k) or 0.0) for k in _SIGNAL_KEYS
        }

        weighted_sum = sum(
            self._weights.get(k, 0.0) * v
            for k, v in raw_signals.items()
        )

        explanation: Dict[str, Any] = {}
        if debug:
            explanation = {
                **raw_signals,
                "weighted_contributions": {
                    k: round(self._weights.get(k, 0.0) * v, 6)
                    for k, v in raw_signals.items()
                },
            }

        node_id = str(candidate.get("node_id") or candidate.get("symbol_id") or "")
        return ScoredResult(
            node_id=node_id,
            score=round(weighted_sum, 6),
            node=candidate,
            explanation=explanation,
        )

    def score_all(
        self,
        candidates: List[Dict[str, Any]],
        top_k: int = 10,
        *,
        debug: bool = False,
    ) -> List[ScoredResult]:
        """
        Score and rank all *candidates*, returning the top-*top_k* results.

        ``candidates`` is a list of dicts, each containing pre-normalized signal
        values.  Ranking is descending by score.
        """
        scored = [self.score_candidate(c, debug=debug) for c in candidates]
        scored.sort(key=lambda r: r.score, reverse=True)
        return scored[:top_k]

    # ── weight management ─────────────────────────────────────

    @property
    def weights(self) -> Dict[str, float]:
        """Read-only view of the current (normalized) weights."""
        return dict(self._weights)

    def with_weights(self, weights: Dict[str, float]) -> "RetrievalScorer":
        """Return a new scorer with updated weights (immutable)."""
        return RetrievalScorer(weights=weights)

    def with_profile(self, profile_name: str) -> "RetrievalScorer":
        """Return a new scorer using a named profile from ``WEIGHT_PROFILES``."""
        profile = WEIGHT_PROFILES.get(profile_name, DEFAULT_WEIGHTS)
        return RetrievalScorer(weights=profile)

    # ── bulk signal injection ─────────────────────────────────

    @staticmethod
    def inject_signals(
        candidates: List[Dict[str, Any]],
        signal_map: Dict[str, Dict[str, float]],
    ) -> List[Dict[str, Any]]:
        """
        Merge pre-computed signal values into candidate dicts.

        ``signal_map`` keys are node IDs; values are dicts of signal name →
        normalized float.  Returns a new list with signals merged in.

        Example::

            signal_map = {
                "proj:a.ts:foo": {"graph": 0.8, "freshness": 0.9},
                "proj:a.ts:bar": {"graph": 0.4, "freshness": 0.5},
            }
            enriched = RetrievalScorer.inject_signals(candidates, signal_map)
        """
        result = []
        for c in candidates:
            nid = str(c.get("node_id") or c.get("symbol_id") or "")
            extra = signal_map.get(nid, {})
            if extra:
                merged = dict(c)
                merged.update(extra)
                result.append(merged)
            else:
                result.append(c)
        return result
