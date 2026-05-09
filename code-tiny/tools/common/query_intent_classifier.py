"""
query_intent_classifier.py
──────────────────────────
Classify a free-text search query into one of three intents and return the
corresponding signal weight profile.

Intents
───────
  semantic   — The user wants *conceptually similar* code.
               Examples: "find similar logic", "what does X do",
                         "explain the authentication flow"

  structural — The user wants *structural relationships* in the call graph.
               Examples: "who calls X", "callers of validateToken",
                         "dependencies of UserService", "where is X used"

  temporal   — The user wants *recently changed or fresh* code.
               Examples: "recent changes", "last modified", "dirty nodes",
                         "what changed this week"

  default    — Fallback when no intent can be determined.

Classification strategy
────────────────────────
1. Exact keyword matching: a curated list of keywords per intent is checked
   against the lowercased query.  First match wins.
2. Regex pattern matching: lightweight patterns that catch multi-word phrasings
   not captured by the keyword list.
3. Fallback: "default" intent with balanced weights.

The classifier is pure-Python with zero dependencies — no ML model or network
call required.  It is designed to be fast (< 1 ms) and deterministic.

Public API
───────────────────────────────────────────────────────
  from tools.common.query_intent_classifier import (
      classify_query,
      get_weight_profile,
      INTENT_SEMANTIC,
      INTENT_STRUCTURAL,
      INTENT_TEMPORAL,
  )

  intent  = classify_query("who calls the validateToken function")
  # → "structural"

  weights = get_weight_profile(intent)
  # → {"semantic": 0.20, "keyword": 0.10, "graph": 0.50, ...}

  # One-shot:
  weights = get_weight_profile(classify_query(query))
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from tools.common.retrieval_scorer import (
    DEFAULT_WEIGHTS,
    WEIGHT_PROFILES,
    WEIGHTS_SEMANTIC,
    WEIGHTS_STRUCTURAL,
    WEIGHTS_TEMPORAL,
)

# ─────────────────────────────────────────────────────────────
# Intent constants
# ─────────────────────────────────────────────────────────────

INTENT_SEMANTIC    = "semantic"
INTENT_STRUCTURAL  = "structural"
INTENT_TEMPORAL    = "temporal"
INTENT_DEFAULT     = "default"

_ALL_INTENTS = (INTENT_SEMANTIC, INTENT_STRUCTURAL, INTENT_TEMPORAL, INTENT_DEFAULT)

# ─────────────────────────────────────────────────────────────
# Keyword lists
# ─────────────────────────────────────────────────────────────

# Ordered by specificity — the first match wins within each intent tier.
_STRUCTURAL_KEYWORDS: List[str] = [
    "who calls",
    "callers of",
    "callers",
    "call graph",
    "call site",
    "call sites",
    "called by",
    "calls",
    "where is",
    "where are",
    "dependencies of",
    "dependency",
    "dependents",
    "who uses",
    "used by",
    "uses of",
    "references to",
    "reference graph",
    "imports",
    "importers",
    "inherits from",
    "extends",
    "implements",
    "overrides",
    "subclasses",
    "subclass",
    "parent class",
    "superclass",
    "interface",
    "related to",
    "connected to",
    "neighbors of",
    "path from",
    "path to",
    "trace from",
    "trace to",
    "flow from",
    "graph neighbors",
    "graph proximity",
    "entry points",
    "entry point",
]

_TEMPORAL_KEYWORDS: List[str] = [
    "recent changes",
    "recently changed",
    "recently modified",
    "recently updated",
    "last modified",
    "last updated",
    "last changed",
    "just updated",
    "just changed",
    "dirty",
    "outdated",
    "stale",
    "fresh",
    "freshness",
    "what changed",
    "what has changed",
    "new changes",
    "latest changes",
    "latest version",
    "this week",
    "today",
    "yesterday",
    "since yesterday",
    "since last",
    "update history",
    "modified files",
    "changed files",
    "uncommitted",
    "incremental",
]

_SEMANTIC_KEYWORDS: List[str] = [
    "similar to",
    "similar logic",
    "like",
    "related logic",
    "semantically",
    "what does",
    "what do",
    "explain",
    "describe",
    "how does",
    "how do",
    "purpose of",
    "meaning of",
    "intent of",
    "equivalent to",
    "analogous",
    "find similar",
    "looks like",
    "same as",
    "concept",
    "conceptually",
]

# ─────────────────────────────────────────────────────────────
# Regex patterns (applied after keyword matching)
# ─────────────────────────────────────────────────────────────

# (pattern, intent)
_PATTERNS: List[Tuple[re.Pattern, str]] = [
    # Structural
    (re.compile(r"\bwho\s+(calls|uses|imports|references)\b",  re.I), INTENT_STRUCTURAL),
    (re.compile(r"\bcall(er|ers|ed|ing)?\s+(of|graph)\b",      re.I), INTENT_STRUCTURAL),
    (re.compile(r"\bdepend(s|enc(y|ies))?\s+of\b",             re.I), INTENT_STRUCTURAL),
    (re.compile(r"\bwhere\s+is\s+.+\s+used\b",                 re.I), INTENT_STRUCTURAL),
    (re.compile(r"\b(trace|path|flow)\s+(from|to|between)\b",  re.I), INTENT_STRUCTURAL),
    # Temporal
    (re.compile(r"\b(recent(ly)?|last|latest)\s+(change|update|modif)",      re.I), INTENT_TEMPORAL),
    (re.compile(r"\b(dirty|stale|fresh|outdated|uncommitted)\b",              re.I), INTENT_TEMPORAL),
    (re.compile(r"\b(this|last)\s+(week|month|day|commit)\b",                re.I), INTENT_TEMPORAL),
    # Semantic
    (re.compile(r"\b(similar|like|analogous|equivalent)\s+(to|logic|code)\b", re.I), INTENT_SEMANTIC),
    (re.compile(r"\b(explain|describe|what\s+(does|is|do))\b",               re.I), INTENT_SEMANTIC),
]

# ─────────────────────────────────────────────────────────────
# Classifier
# ─────────────────────────────────────────────────────────────


def classify_query(query: str) -> str:
    """
    Classify *query* into one of the four intent strings.

    Returns one of: "semantic", "structural", "temporal", "default".
    """
    q = (query or "").strip().lower()
    if not q:
        return INTENT_DEFAULT

    # 1. Keyword matching — structural first (most distinctive), then temporal,
    #    then semantic (most generic — must come last to reduce false positives).
    if _matches_any(q, _STRUCTURAL_KEYWORDS):
        return INTENT_STRUCTURAL
    if _matches_any(q, _TEMPORAL_KEYWORDS):
        return INTENT_TEMPORAL
    if _matches_any(q, _SEMANTIC_KEYWORDS):
        return INTENT_SEMANTIC

    # 2. Regex patterns
    for pattern, intent in _PATTERNS:
        if pattern.search(q):
            return intent

    return INTENT_DEFAULT


def classify_query_explain(query: str) -> Dict[str, object]:
    """
    Like ``classify_query`` but returns a dict explaining the decision.

    Returns::

        {
          "query":   "who calls validateToken",
          "intent":  "structural",
          "matched": "keyword: 'who calls'",
        }
    """
    q = (query or "").strip().lower()
    if not q:
        return {"query": query, "intent": INTENT_DEFAULT, "matched": "empty query"}

    for kw in _STRUCTURAL_KEYWORDS:
        if kw in q:
            return {"query": query, "intent": INTENT_STRUCTURAL, "matched": f"keyword: '{kw}'"}
    for kw in _TEMPORAL_KEYWORDS:
        if kw in q:
            return {"query": query, "intent": INTENT_TEMPORAL, "matched": f"keyword: '{kw}'"}
    for kw in _SEMANTIC_KEYWORDS:
        if kw in q:
            return {"query": query, "intent": INTENT_SEMANTIC, "matched": f"keyword: '{kw}'"}

    for pattern, intent in _PATTERNS:
        m = pattern.search(q)
        if m:
            return {"query": query, "intent": intent, "matched": f"regex: '{pattern.pattern}'"}

    return {"query": query, "intent": INTENT_DEFAULT, "matched": "fallback"}


def get_weight_profile(intent: str) -> Dict[str, float]:
    """
    Return the signal weight profile dict for the given *intent* string.

    Falls back to ``DEFAULT_WEIGHTS`` for unknown intents.
    """
    return dict(WEIGHT_PROFILES.get(intent, DEFAULT_WEIGHTS))


# ─────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────


def _matches_any(q: str, keywords: List[str]) -> bool:
    """Return True if any keyword appears in the lowercased query string."""
    return any(kw in q for kw in keywords)
