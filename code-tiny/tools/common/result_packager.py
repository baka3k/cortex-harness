"""
result_packager.py
──────────────────
Structured result packaging with per-node explainability for the Graph Explorer
semantic search system.

Converts a flat ``List[ScoredResult]`` (from ``IntelligentRetrievalEngine``)
into the structured response envelope defined in the system specification:

  {
    "matched_nodes":   [...],  # all top-K results with per-node reasons
    "entry_points":    [...],  # subset: exported or high-proximity seed nodes
    "related_paths":   [...],  # neighbor paths discovered via graph expansion
    "explanation":     str,    # human-readable summary of why results were found
    "confidence":      float,  # 0.0–1.0 overall retrieval confidence
    "query_analysis":  {...},  # structured understanding (from QueryUnderstanding)
  }

Per-node reason strings explain WHICH signals drove retrieval, e.g.:
  "Node matched via semantic similarity (0.87) and keyword overlap.
   It is directly connected to 2 seed nodes in the call graph."

Public API
──────────
  from tools.common.result_packager import ResultPackager, PackedResult
  from tools.common.retrieval_scorer import ScoredResult

  packed = ResultPackager.pack(
      scored_results,
      query_understanding=u,          # optional QueryUnderstanding
      mode="hybrid",
  )
  # → PackedResult (or .to_dict())
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from tools.common.retrieval_scorer import ScoredResult

# Threshold below which a signal is ignored in human-readable reasons
_SIGNAL_THRESHOLD = 0.15

# Signals ordered from most to least "human interesting"
_SIGNAL_LABELS: List[tuple] = [
    ("semantic",   "semantic similarity"),
    ("keyword",    "keyword match"),
    ("graph",      "graph proximity"),
    ("confidence", "semantic confidence"),
    ("usage",      "usage importance"),
    ("freshness",  "recent modification"),
]

# Nodes with graph_proximity or hop_distance=0 (seeds) are entry points
_ENTRY_POINT_PROXIMITY_THRESHOLD = 0.60


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _build_node_reason(scored: ScoredResult) -> str:
    """
    Produce a human-readable explanation for why this node was retrieved.

    Uses the ``explanation`` dict from ``RetrievalScorer`` (debug=True mode)
    which contains per-signal raw values.
    """
    expl = scored.explanation or {}
    node = scored.node or {}

    # Gather active signals (above threshold)
    active: List[str] = []
    for sig_key, sig_label in _SIGNAL_LABELS:
        val = float(expl.get(sig_key, node.get(sig_key, 0.0)))
        if val >= _SIGNAL_THRESHOLD:
            active.append(f"{sig_label} ({val:.2f})")

    name = node.get("name") or node.get("qualified_name") or scored.node_id
    file_path = node.get("file_path", "")

    parts: List[str] = []

    if active:
        if len(active) == 1:
            parts.append(f"Retrieved via {active[0]}.")
        else:
            parts.append(f"Retrieved via {', '.join(active[:-1])} and {active[-1]}.")
    else:
        parts.append("Retrieved as a related graph neighbor.")

    # Graph expansion attribution
    graph_val = float(expl.get("graph", node.get("graph", 0.0)))
    hop = node.get("hop_distance")
    if hop == 0:
        parts.append("This is a direct seed node (entry point).")
    elif hop == 1:
        parts.append("This is a 1-hop neighbor of a seed node.")
    elif hop == 2:
        parts.append("This is a 2-hop neighbor discovered via graph expansion.")
    elif graph_val >= _SIGNAL_PROXIMITY_HIGH:
        parts.append("This node is closely connected to the matched cluster.")

    if file_path:
        parts.append(f"Located in {file_path}.")

    return " ".join(parts)

_SIGNAL_PROXIMITY_HIGH = 0.50


def _is_entry_point(scored: ScoredResult) -> bool:
    """
    Heuristic: a node is an entry point if it is:
    - A seed node (hop_distance=0 or graph_proximity=1.0), OR
    - Exported / public, OR
    - Has high usage signal
    """
    node = scored.node or {}
    expl = scored.explanation or {}

    hop = node.get("hop_distance")
    if hop == 0:
        return True

    graph_val = float(expl.get("graph", node.get("graph", 0.0)))
    if graph_val >= _ENTRY_POINT_PROXIMITY_THRESHOLD:
        return True

    if node.get("exported"):
        return True

    usage = float(expl.get("usage", node.get("usage", 0.0)))
    if usage >= 0.70:
        return True

    kind = str(node.get("kind", "")).lower()
    if kind in ("entrypoint", "public_method", "exported_function", "api_handler"):
        return True

    return False


def _build_related_path(scored: ScoredResult) -> Optional[Dict[str, Any]]:
    """
    If a node was discovered via graph expansion (hop > 0), build a path record
    showing how it connects back to the seed cluster.
    """
    node = scored.node or {}
    hop = node.get("hop_distance")
    if not hop or hop == 0:
        return None

    return {
        "node_id":       scored.node_id,
        "name":          node.get("name") or scored.node_id,
        "qualified_name":node.get("qualified_name", ""),
        "file_path":     node.get("file_path", ""),
        "hop_distance":  hop,
        "via":           "graph_expansion",
        "score":         round(scored.score, 4),
    }


def _compute_confidence(scored_results: List[ScoredResult]) -> float:
    """
    Estimate overall retrieval confidence from score distribution.

    Formula:
    - If no results: 0.0
    - If top score >= 0.8: high confidence
    - Use mean of top-3 scores, scaled to [0, 1]
    """
    if not scored_results:
        return 0.0

    top_scores = [r.score for r in scored_results[:5]]
    if not top_scores:
        return 0.0

    mean_top = sum(top_scores) / len(top_scores)
    # Clamp to [0, 1] — scores from RetrievalScorer are already in this range
    return min(1.0, max(0.0, round(mean_top, 3)))


def _build_explanation_summary(
    scored_results: List[ScoredResult],
    query_analysis: Optional[Dict[str, Any]],
    mode: str,
) -> str:
    """
    Produce a high-level human-readable explanation for the entire result set.
    """
    if not scored_results:
        return "No matching nodes found for the given query."

    n_total     = len(scored_results)
    n_entry     = sum(1 for r in scored_results if _is_entry_point(r))
    n_expanded  = sum(1 for r in scored_results if (r.node or {}).get("hop_distance", 0) > 0)
    confidence  = _compute_confidence(scored_results)
    top_name    = (scored_results[0].node or {}).get("name") or scored_results[0].node_id

    parts = []

    # Mode description
    mode_desc = {
        "semantic":       "semantic vector search",
        "hybrid":         "hybrid (semantic + keyword) search",
        "graph_expanded": "graph-expanded search (semantic + keyword + neighbor traversal)",
    }.get(mode, "multi-strategy search")

    parts.append(f"Found {n_total} matching node(s) using {mode_desc}.")

    if query_analysis:
        signals = query_analysis.get("domain_signals", [])
        if signals:
            parts.append(f"Query involves domain signals: {', '.join(signals)}.")
        entities = query_analysis.get("entities", [])
        if entities:
            parts.append(f"Key entities identified: {', '.join(entities[:5])}.")

    parts.append(f"Top match: '{top_name}' (confidence {confidence:.0%}).")

    if n_entry > 0:
        parts.append(f"{n_entry} node(s) identified as entry points.")

    if n_expanded > 0:
        parts.append(
            f"{n_expanded} additional node(s) discovered via call-graph expansion."
        )

    return " ".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# PackedResult dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class MatchedNode:
    """A single result node with enriched metadata and explanation."""
    node_id:        str
    name:           str
    qualified_name: str
    kind:           str
    file_path:      str
    score:          float
    reason:         str
    is_entry_point: bool
    hop_distance:   int
    signals:        Dict[str, float] = field(default_factory=dict)
    properties:     Dict[str, Any]  = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id":        self.node_id,
            "name":           self.name,
            "qualified_name": self.qualified_name,
            "kind":           self.kind,
            "file_path":      self.file_path,
            "score":          round(self.score, 4),
            "reason":         self.reason,
            "is_entry_point": self.is_entry_point,
            "hop_distance":   self.hop_distance,
            "signals":        {k: round(v, 4) for k, v in self.signals.items()},
            "properties":     self.properties,
        }


@dataclass
class PackedResult:
    """
    Structured result envelope returned by ``ResultPackager.pack()``.

    Fields
    ------
    matched_nodes:  All top-K nodes with per-node reason strings.
    entry_points:   Subset of matched_nodes identified as entry points.
    related_paths:  Nodes discovered via graph expansion with hop metadata.
    explanation:    Human-readable summary of the full retrieval.
    confidence:     Overall retrieval confidence [0.0, 1.0].
    query_analysis: Structured query understanding (from QueryUnderstanding.to_dict()).
    mode:           Retrieval mode used (semantic / hybrid / graph_expanded).
    """
    matched_nodes:  List[MatchedNode]         = field(default_factory=list)
    entry_points:   List[MatchedNode]         = field(default_factory=list)
    related_paths:  List[Dict[str, Any]]      = field(default_factory=list)
    explanation:    str                        = ""
    confidence:     float                      = 0.0
    query_analysis: Dict[str, Any]             = field(default_factory=dict)
    mode:           str                        = "hybrid"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "matched_nodes":  [n.to_dict() for n in self.matched_nodes],
            "entry_points":   [n.to_dict() for n in self.entry_points],
            "related_paths":  self.related_paths,
            "explanation":    self.explanation,
            "confidence":     self.confidence,
            "query_analysis": self.query_analysis,
            "mode":           self.mode,
        }


# ─────────────────────────────────────────────────────────────────────────────
# ResultPackager — main public class
# ─────────────────────────────────────────────────────────────────────────────

class ResultPackager:
    """
    Convert a flat list of ``ScoredResult`` objects into a structured, explainable
    ``PackedResult`` envelope.

    Usage
    -----
    ::

        packed = ResultPackager.pack(
            scored_results,
            query_understanding=u,   # QueryUnderstanding instance
            mode="graph_expanded",
        )
        response = packed.to_dict()
    """

    @staticmethod
    def pack(
        scored_results: List[ScoredResult],
        query_understanding: Optional[Any] = None,   # QueryUnderstanding
        mode: str = "hybrid",
    ) -> PackedResult:
        """
        Build a ``PackedResult`` from scored retrieval results.

        Parameters
        ----------
        scored_results:     Output of ``IntelligentRetrievalEngine.search()``.
        query_understanding: Optional ``QueryUnderstanding`` instance.
        mode:               One of "semantic" | "hybrid" | "graph_expanded".
        """
        query_analysis: Dict[str, Any] = {}
        if query_understanding is not None and hasattr(query_understanding, "to_dict"):
            query_analysis = query_understanding.to_dict()

        matched_nodes:  List[MatchedNode]    = []
        entry_points:   List[MatchedNode]    = []
        related_paths:  List[Dict[str, Any]] = []

        for scored in scored_results:
            node = scored.node or {}
            expl = scored.explanation or {}

            # Build signal snapshot (pull from explanation or node directly)
            signals: Dict[str, float] = {}
            for sig_key, _ in _SIGNAL_LABELS:
                v = expl.get(sig_key, node.get(sig_key, 0.0))
                if v:
                    signals[sig_key] = round(float(v), 4)

            reason        = _build_node_reason(scored)
            is_entry      = _is_entry_point(scored)
            hop_distance  = int(node.get("hop_distance", 0))

            mn = MatchedNode(
                node_id        = scored.node_id,
                name           = node.get("name") or scored.node_id,
                qualified_name = node.get("qualified_name", ""),
                kind           = node.get("kind", ""),
                file_path      = node.get("file_path", ""),
                score          = scored.score,
                reason         = reason,
                is_entry_point = is_entry,
                hop_distance   = hop_distance,
                signals        = signals,
                properties     = {
                    k: v for k, v in node.items()
                    if k not in ("name", "qualified_name", "kind", "file_path",
                                 "hop_distance", "semantic", "keyword", "graph",
                                 "freshness", "confidence", "usage")
                },
            )
            matched_nodes.append(mn)

            if is_entry:
                entry_points.append(mn)

            # Related paths (graph-expanded nodes)
            path_record = _build_related_path(scored)
            if path_record:
                related_paths.append(path_record)

        confidence  = _compute_confidence(scored_results)
        explanation = _build_explanation_summary(scored_results, query_analysis, mode)

        return PackedResult(
            matched_nodes  = matched_nodes,
            entry_points   = entry_points,
            related_paths  = related_paths,
            explanation    = explanation,
            confidence     = confidence,
            query_analysis = query_analysis,
            mode           = mode,
        )
