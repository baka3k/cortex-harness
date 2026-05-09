"""
Confidence scoring for semantic function inference.

Weighted formula:
    confidence = 0.40 * naming + 0.20 * type + 0.30 * usage + 0.10 * body

Modifiers applied after weighting:
  - Exported function   +0.03
  - Generic name        ×0.85
  - Short name (≤3)     ×0.75

Result clamped to [0.0, 1.0].
"""

from __future__ import annotations

import re
from typing import Dict

# ---------------------------------------------------------------------------
# Weights — must sum to 1.0
# ---------------------------------------------------------------------------

SIGNAL_WEIGHTS: Dict[str, float] = {
    "naming": 0.40,
    "type":   0.20,
    "usage":  0.30,
    "body":   0.10,
}

# ---------------------------------------------------------------------------
# Generic / low-quality name tokens
# ---------------------------------------------------------------------------

_GENERIC_TOKENS = frozenset({
    "data", "stuff", "thing", "value", "object", "obj", "item", "result",
    "response", "res", "info", "detail", "content", "payload", "input",
    "output", "temp", "tmp", "foo", "bar", "baz", "test", "util", "utils",
    "helper", "handler", "run", "do", "exec", "func", "fn",
    # Common generic verb-stems that carry no domain meaning
    "handle", "process", "manage", "perform", "execute",
})

# Verb prefixes that are themselves meaningful (not generic)
_VERB_PREFIXES = frozenset({
    "get", "fetch", "retrieve", "find", "search", "query", "list", "select",
    "load", "read", "download", "import", "set", "update", "modify", "change",
    "apply", "assign", "patch", "replace", "reset", "save", "write", "store",
    "persist", "upload", "export", "publish", "send", "push", "is", "has",
    "can", "should", "will", "check", "validate", "verify", "ensure", "assert",
    "calculate", "compute", "determine", "resolve", "derive", "estimate",
    "count", "measure", "sum", "create", "make", "build", "construct",
    "generate", "spawn", "produce", "init", "initialize", "delete", "remove",
    "destroy", "clear", "purge", "drop", "parse", "transform", "convert",
    "format", "map", "serialize", "deserialize", "encode", "decode",
    "normalize", "sanitize", "render", "handle", "emit", "dispatch", "notify",
    "trigger", "log", "add", "append", "insert", "merge", "on",
})

# Split camelCase / snake_case into tokens
_SPLIT_PATTERN = re.compile(r"[_\s]+|(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _name_tokens(name: str) -> frozenset[str]:
    return frozenset(t.lower() for t in _SPLIT_PATTERN.split(name) if t)


def is_generic_name(name: str) -> bool:
    """Return True if the function name's subject tokens are entirely generic.

    Verb prefixes (get, set, fetch, handle...) are ignored when determining
    genericity — only the *subject* part of the name is evaluated.
    e.g. 'handleData' → subject=['data'] → generic=True
         'getUserProfile' → subject=['user', 'profile'] → generic=False
    """
    tokens = list(_name_tokens(name))
    if not tokens:
        return True

    # Strip recognized verb prefix token
    subject_tokens = [t for t in tokens if t not in _VERB_PREFIXES]

    # If entirely made up of verb tokens (e.g. "doRun"), treat all tokens as subject
    if not subject_tokens:
        subject_tokens = tokens

    meaningful = frozenset(subject_tokens) - {"the", "a", "an", "my", "our", "new"}
    return bool(meaningful) and meaningful.issubset(_GENERIC_TOKENS)


# ---------------------------------------------------------------------------
# Scorer
# ---------------------------------------------------------------------------


class ConfidenceScorer:
    """Combine per-signal scores into a final confidence value."""

    def __init__(self, weights: Dict[str, float] | None = None) -> None:
        self._weights = weights or SIGNAL_WEIGHTS

    def score(
        self,
        naming: float,
        type_: float,
        usage: float,
        body: float,
        *,
        func_name: str = "",
        is_exported: bool = False,
    ) -> float:
        """
        Compute weighted confidence.

        Args:
            naming:      0-1 signal from naming heuristics.
            type_:       0-1 signal from type annotation analysis.
            usage:       0-1 signal from call-site context analysis.
            body:        0-1 signal from function body patterns.
            func_name:   Function name (used for modifier checks).
            is_exported: Whether the function is exported/public.

        Returns:
            Float in [0.0, 1.0].
        """
        raw = (
            self._weights.get("naming", 0.40) * naming
            + self._weights.get("type",   0.20) * type_
            + self._weights.get("usage",  0.30) * usage
            + self._weights.get("body",   0.10) * body
        )

        # ── Modifiers ──────────────────────────────────────────────────
        if is_exported:
            raw = min(raw + 0.03, 1.0)

        if func_name:
            if len(func_name) <= 3:
                raw *= 0.75
            elif is_generic_name(func_name):
                raw *= 0.85

        return round(min(max(raw, 0.0), 1.0), 3)

    # Convenience: score from a signals dict
    def score_dict(
        self,
        signals: Dict[str, float],
        *,
        func_name: str = "",
        is_exported: bool = False,
    ) -> float:
        return self.score(
            naming=signals.get("naming", 0.0),
            type_=signals.get("type", 0.0),
            usage=signals.get("usage", 0.0),
            body=signals.get("body", 0.0),
            func_name=func_name,
            is_exported=is_exported,
        )
