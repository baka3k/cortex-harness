"""
signal_normalizer.py
────────────────────
Normalize heterogeneous retrieval signals to a [0, 1] range so they can be
combined in a weighted scoring formula without any one signal dominating.

Public API
──────────────────────────────────────────────────────
  from tools.common.signal_normalizer import normalize_signals, min_max_normalize

  # Normalize a batch of values for a single signal dimension
  normed = min_max_normalize([0.1, 0.5, 0.9, 1.2])   # → [0.0, 0.4, 0.8, 1.0]

  # Normalize a single scalar relative to known bounds
  v = clamp(raw_score, lo=0.0, hi=1.0)

  # Normalize a full signal dict for one candidate
  normed_signals = normalize_signals(
      {"semantic": 0.83, "keyword": 14.2, "graph": 3, "freshness": 0.6,
       "confidence": 0.71, "usage": 0.42},
      signal_bounds={"keyword": (0.0, 20.0), "graph": (0.0, 10.0)},
  )

Design notes
────────────
- Min-max normalization is applied per batch for multi-value lists (e.g. all
  keyword scores across a result set before scoring).
- For single-candidate scoring the caller passes ``signal_bounds`` — optional
  per-signal (lo, hi) tuples that anchor the normalization to domain knowledge.
  Signals already in [0,1] pass through unchanged by default.
- Signals outside a bound are clamped; bounds are never violated.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple


# ─────────────────────────────────────────────────────────────
# Default domain bounds for each well-known signal
# ─────────────────────────────────────────────────────────────

# (lo, hi) — values are clamped to this range before normalization.
_DEFAULT_BOUNDS: Dict[str, Tuple[float, float]] = {
    # Qdrant cosine similarity — already [0, 1]
    "semantic":   (0.0, 1.0),
    # BM25 / keyword relevance — unbounded; typical range 0..25
    "keyword":    (0.0, 25.0),
    # Graph proximity — hop-based inverted score; we normalize 0..10 hops down
    "graph":      (0.0, 1.0),
    # Freshness — elapsed-second scalar or pre-computed fraction; [0, 1]
    "freshness":  (0.0, 1.0),
    # Semantic confidence from SemanticInferenceEngine — already [0, 1]
    "confidence": (0.0, 1.0),
    # Usage importance — typically 0..1 from get_usage_signal
    "usage":      (0.0, 1.0),
}


# ─────────────────────────────────────────────────────────────
# Primitive helpers
# ─────────────────────────────────────────────────────────────


def clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *value* to [lo, hi]."""
    if math.isnan(value):
        return 0.0
    return max(lo, min(hi, value))


def _safe_range(lo: float, hi: float) -> float:
    """Return hi - lo, or 1.0 to avoid ZeroDivisionError."""
    r = hi - lo
    return r if r > 1e-12 else 1.0


# ─────────────────────────────────────────────────────────────
# Batch normalization (min-max over a list)
# ─────────────────────────────────────────────────────────────


def min_max_normalize(values: List[float]) -> List[float]:
    """
    Min-max normalize a list of floats to [0, 1].

    Edge cases:
    - Empty list → []
    - Single value → [1.0]  (unambiguously the best candidate)
    - All identical → all 0.0 (no discrimination possible)
    """
    if not values:
        return []
    lo = min(values)
    hi = max(values)
    span = _safe_range(lo, hi)
    if abs(hi - lo) < 1e-12:
        # All values identical — treat as zero (no signal discrimination)
        return [0.0] * len(values)
    return [(v - lo) / span for v in values]


def batch_normalize_signal(
    candidates: List[Dict],
    key: str,
    *,
    lo: Optional[float] = None,
    hi: Optional[float] = None,
    out_key: Optional[str] = None,
) -> List[Dict]:
    """
    In-place min-max normalize `key` across all candidates.

    If `lo` / `hi` are provided the normalization is relative to those anchors
    (helpful when you know the theoretical bounds rather than sample extremes).
    If `out_key` is given the normalized value is stored under that key rather
    than overwriting the source key.

    Returns the mutated list for chaining.
    """
    dest = out_key or key
    raw_values = [float(c.get(key) or 0.0) for c in candidates]

    if lo is not None and hi is not None:
        span = _safe_range(lo, hi)
        normed = [clamp((v - lo) / span, 0.0, 1.0) for v in raw_values]
    else:
        normed = min_max_normalize(raw_values)

    for c, n in zip(candidates, normed):
        c[dest] = round(n, 6)
    return candidates


# ─────────────────────────────────────────────────────────────
# Single-candidate normalization
# ─────────────────────────────────────────────────────────────


def normalize_signals(
    raw: Dict[str, float],
    signal_bounds: Optional[Dict[str, Tuple[float, float]]] = None,
) -> Dict[str, float]:
    """
    Normalize a signal dict for a single candidate to [0, 1].

    ``raw`` keys should be a subset of:
      semantic, keyword, graph, freshness, confidence, usage

    ``signal_bounds`` overrides the built-in defaults per key.  Pass
    ``{key: (lo, hi)}`` only for signals whose range differs from the defaults.

    Returns a new dict with all values in [0, 1].
    """
    bounds = dict(_DEFAULT_BOUNDS)
    if signal_bounds:
        bounds.update(signal_bounds)

    result: Dict[str, float] = {}
    for key, value in raw.items():
        lo, hi = bounds.get(key, (0.0, 1.0))
        span = _safe_range(lo, hi)
        normed = clamp((float(value) - lo) / span, 0.0, 1.0)
        result[key] = round(normed, 6)
    return result


# ─────────────────────────────────────────────────────────────
# Freshness helpers
# ─────────────────────────────────────────────────────────────


def freshness_from_elapsed(
    elapsed_seconds: float,
    half_life_days: float = 30.0,
) -> float:
    """
    Compute freshness score in [0, 1] using exponential decay.

    ``elapsed_seconds`` — seconds since the node was last updated.
    ``half_life_days``  — age at which freshness drops to 0.5.

    A freshly modified node → score ≈ 1.0; very old node → score → 0.0.
    """
    if elapsed_seconds <= 0.0:
        return 1.0
    half_life_s = half_life_days * 86_400.0
    return math.exp(-math.log(2.0) * elapsed_seconds / half_life_s)


def freshness_from_dirty(is_dirty: bool, last_updated_iso: str = "") -> float:
    """
    Convenience wrapper: gives a 1.0 freshness boost when `is_dirty` is True
    (the node has uncommitted or recently changed state), else falls back to
    1.0 for any recently updated ISO-timestamp string and 0.3 as a floor.
    """
    if is_dirty:
        return 1.0
    if last_updated_iso:
        from datetime import datetime, timezone
        try:
            ts = datetime.fromisoformat(last_updated_iso.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            elapsed = (now - ts).total_seconds()
            return freshness_from_elapsed(elapsed)
        except ValueError:
            pass
    return 0.3  # Unknown age — neutral-low fallback
