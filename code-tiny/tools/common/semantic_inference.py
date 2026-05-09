"""
Multi-signal semantic inference engine for TypeScript functions.

Architecture
───────────────────────────────────────────────────────────────
Four signal sources (processed in order of override priority):

  1. usage  (weight 0.30) — how callers use the return value
  2. naming (weight 0.40) — verb-prefix patterns in the function name
  3. type   (weight 0.20) — TypeScript return/param type annotations
  4. body   (weight 0.10) — patterns inside the function body

Intent is determined by the highest-priority available signal.
Confidence is always the weighted sum of all four signals.

Language-agnostic intent taxonomy:
  retrieval | mutation | predicate | validation | computation |
  io_read   | io_write | transformation | side_effect |
  deletion  | factory  | unknown

Public API
───────────────────────────────────────────────────────────────
  engine = SemanticInferenceEngine()

  # Single function (no usage context)
  result: SemanticResult = engine.analyze(func_dict)

  # Batch enrichment — mutates function dicts in place
  engine.enrich_corpus(functions, calls)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from tools.common.confidence_scorer import ConfidenceScorer
from tools.common.call_graph_builder import (
    FunctionUsageIndex,
    build_usage_index,
    get_usage_signal,
)

# ─────────────────────────────────────────────────────────────
# Intent taxonomy constants
# ─────────────────────────────────────────────────────────────

INTENT_RETRIEVAL       = "retrieval"
INTENT_MUTATION        = "mutation"
INTENT_PREDICATE       = "predicate"
INTENT_VALIDATION      = "validation"
INTENT_COMPUTATION     = "computation"
INTENT_IO_READ         = "io_read"
INTENT_IO_WRITE        = "io_write"
INTENT_TRANSFORMATION  = "transformation"
INTENT_SIDE_EFFECT     = "side_effect"
INTENT_DELETION        = "deletion"
INTENT_FACTORY         = "factory"
INTENT_UNKNOWN         = "unknown"

# ─────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────


@dataclass
class SemanticResult:
    intent:     str
    summary:    str
    confidence: float
    inferred:   bool
    signals:    Dict[str, float]
    side_effect: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent":      self.intent,
            "summary":     self.summary,
            "confidence":  self.confidence,
            "inferred":    self.inferred,
            "signals":     self.signals,
            "side_effect": self.side_effect,
        }


# ─────────────────────────────────────────────────────────────
# Naming analyzer
# ─────────────────────────────────────────────────────────────

# (pattern, intent, base_confidence)
_VERB_PATTERNS: List[Tuple[str, str, float]] = [
    # ── Retrieval ─────────────────────────────────────────────
    (r"^get",          INTENT_RETRIEVAL,      0.95),
    (r"^fetch",        INTENT_RETRIEVAL,      0.95),
    (r"^retrieve",     INTENT_RETRIEVAL,      0.90),
    (r"^find",         INTENT_RETRIEVAL,      0.90),
    (r"^search",       INTENT_RETRIEVAL,      0.88),
    (r"^query",        INTENT_RETRIEVAL,      0.88),
    (r"^list",         INTENT_RETRIEVAL,      0.85),
    (r"^select",       INTENT_RETRIEVAL,      0.82),
    # ── IO read ───────────────────────────────────────────────
    (r"^load",         INTENT_IO_READ,        0.90),
    (r"^read",         INTENT_IO_READ,        0.90),
    (r"^import",       INTENT_IO_READ,        0.82),
    (r"^download",     INTENT_IO_READ,        0.88),
    # ── Mutation ──────────────────────────────────────────────
    (r"^set",          INTENT_MUTATION,       0.95),
    (r"^update",       INTENT_MUTATION,       0.95),
    (r"^modify",       INTENT_MUTATION,       0.90),
    (r"^change",       INTENT_MUTATION,       0.88),
    (r"^apply",        INTENT_MUTATION,       0.78),
    (r"^assign",       INTENT_MUTATION,       0.85),
    (r"^patch",        INTENT_MUTATION,       0.88),
    (r"^replace",      INTENT_MUTATION,       0.85),
    (r"^reset",        INTENT_MUTATION,       0.82),
    # ── IO write ──────────────────────────────────────────────
    (r"^save",         INTENT_IO_WRITE,       0.90),
    (r"^write",        INTENT_IO_WRITE,       0.90),
    (r"^store",        INTENT_IO_WRITE,       0.90),
    (r"^persist",      INTENT_IO_WRITE,       0.88),
    (r"^upload",       INTENT_IO_WRITE,       0.88),
    (r"^export",       INTENT_IO_WRITE,       0.82),
    (r"^publish",      INTENT_IO_WRITE,       0.82),
    (r"^send",         INTENT_IO_WRITE,       0.85),
    (r"^push",         INTENT_IO_WRITE,       0.75),
    # ── Predicate ─────────────────────────────────────────────
    (r"^is",           INTENT_PREDICATE,      0.95),
    (r"^has",          INTENT_PREDICATE,      0.95),
    (r"^can",          INTENT_PREDICATE,      0.93),
    (r"^should",       INTENT_PREDICATE,      0.93),
    (r"^will",         INTENT_PREDICATE,      0.88),
    (r"^contains",     INTENT_PREDICATE,      0.85),
    (r"^exists",       INTENT_PREDICATE,      0.85),
    (r"^allows",       INTENT_PREDICATE,      0.82),
    (r"^supports",     INTENT_PREDICATE,      0.82),
    # ── Validation ────────────────────────────────────────────
    (r"^check",        INTENT_VALIDATION,     0.88),
    (r"^validate",     INTENT_VALIDATION,     0.95),
    (r"^verify",       INTENT_VALIDATION,     0.90),
    (r"^ensure",       INTENT_VALIDATION,     0.85),
    (r"^assert",       INTENT_VALIDATION,     0.88),
    (r"^require",      INTENT_VALIDATION,     0.75),
    # ── Computation ───────────────────────────────────────────
    (r"^calculate",    INTENT_COMPUTATION,    0.95),
    (r"^compute",      INTENT_COMPUTATION,    0.95),
    (r"^determine",    INTENT_COMPUTATION,    0.85),
    (r"^resolve",      INTENT_COMPUTATION,    0.80),
    (r"^derive",       INTENT_COMPUTATION,    0.82),
    (r"^estimate",     INTENT_COMPUTATION,    0.82),
    (r"^count",        INTENT_COMPUTATION,    0.85),
    (r"^measure",      INTENT_COMPUTATION,    0.82),
    (r"^sum",          INTENT_COMPUTATION,    0.88),
    (r"^average",      INTENT_COMPUTATION,    0.85),
    # ── Factory ───────────────────────────────────────────────
    (r"^create",       INTENT_FACTORY,        0.95),
    (r"^make",         INTENT_FACTORY,        0.90),
    (r"^build",        INTENT_FACTORY,        0.92),
    (r"^construct",    INTENT_FACTORY,        0.90),
    (r"^generate",     INTENT_FACTORY,        0.85),
    (r"^spawn",        INTENT_FACTORY,        0.82),
    (r"^produce",      INTENT_FACTORY,        0.80),
    (r"^instantiate",  INTENT_FACTORY,        0.88),
    (r"^new",          INTENT_FACTORY,        0.75),
    (r"^init(?:ialize)?", INTENT_FACTORY,     0.85),
    # ── Deletion ──────────────────────────────────────────────
    (r"^delete",       INTENT_DELETION,       0.95),
    (r"^remove",       INTENT_DELETION,       0.93),
    (r"^destroy",      INTENT_DELETION,       0.90),
    (r"^clear",        INTENT_DELETION,       0.88),
    (r"^purge",        INTENT_DELETION,       0.88),
    (r"^drop",         INTENT_DELETION,       0.82),
    (r"^discard",      INTENT_DELETION,       0.82),
    (r"^unset",        INTENT_DELETION,       0.80),
    (r"^revoke",       INTENT_DELETION,       0.80),
    # ── Transformation ────────────────────────────────────────
    (r"^parse",        INTENT_TRANSFORMATION, 0.88),
    (r"^transform",    INTENT_TRANSFORMATION, 0.88),
    (r"^convert",      INTENT_TRANSFORMATION, 0.88),
    (r"^format",       INTENT_TRANSFORMATION, 0.85),
    (r"^map",          INTENT_TRANSFORMATION, 0.80),
    (r"^serialize",    INTENT_TRANSFORMATION, 0.90),
    (r"^deserialize",  INTENT_TRANSFORMATION, 0.90),
    (r"^encode",       INTENT_TRANSFORMATION, 0.88),
    (r"^decode",       INTENT_TRANSFORMATION, 0.88),
    (r"^normalize",    INTENT_TRANSFORMATION, 0.85),
    (r"^sanitize",     INTENT_TRANSFORMATION, 0.85),
    (r"^render",       INTENT_TRANSFORMATION, 0.78),
    # ── Side effect ───────────────────────────────────────────
    (r"^handle",       INTENT_SIDE_EFFECT,    0.75),
    (r"^process",      INTENT_SIDE_EFFECT,    0.72),
    (r"^execute",      INTENT_SIDE_EFFECT,    0.78),
    (r"^run",          INTENT_SIDE_EFFECT,    0.72),
    (r"^perform",      INTENT_SIDE_EFFECT,    0.72),
    (r"^do",           INTENT_SIDE_EFFECT,    0.65),
    (r"^invoke",       INTENT_SIDE_EFFECT,    0.78),
    (r"^dispatch",     INTENT_SIDE_EFFECT,    0.78),
    (r"^notify",       INTENT_SIDE_EFFECT,    0.78),
    (r"^trigger",      INTENT_SIDE_EFFECT,    0.78),
    (r"^emit",         INTENT_SIDE_EFFECT,    0.78),
    (r"^fire",         INTENT_SIDE_EFFECT,    0.75),
    (r"^broadcast",    INTENT_SIDE_EFFECT,    0.78),
    (r"^log",          INTENT_SIDE_EFFECT,    0.80),
    (r"^track",        INTENT_SIDE_EFFECT,    0.75),
    (r"^register",     INTENT_SIDE_EFFECT,    0.75),
    (r"^subscribe",    INTENT_SIDE_EFFECT,    0.78),
    (r"^unsubscribe",  INTENT_SIDE_EFFECT,    0.78),
    # ── Mutation (add/append) ─────────────────────────────────
    (r"^add",          INTENT_MUTATION,       0.85),
    (r"^append",       INTENT_MUTATION,       0.85),
    (r"^insert",       INTENT_MUTATION,       0.85),
    (r"^prepend",      INTENT_MUTATION,       0.83),
    (r"^attach",       INTENT_MUTATION,       0.80),
    (r"^merge",        INTENT_MUTATION,       0.80),
    (r"^inject",       INTENT_MUTATION,       0.75),
    (r"^receive",      INTENT_IO_READ,        0.80),
]

# Pre-compile patterns (case-insensitive)
_COMPILED_PATTERNS: List[Tuple[re.Pattern, str, float]] = [
    (re.compile(pat, re.IGNORECASE), intent, conf)
    for pat, intent, conf in _VERB_PATTERNS
]

# Natural language summary templates
_SUMMARY_TEMPLATES: Dict[str, str] = {
    INTENT_RETRIEVAL:      "Retrieves {subject}",
    INTENT_IO_READ:        "Reads {subject} from external source",
    INTENT_MUTATION:       "Updates or modifies {subject}",
    INTENT_IO_WRITE:       "Writes {subject} to persistent storage",
    INTENT_PREDICATE:      "Checks whether {subject}",
    INTENT_VALIDATION:     "Validates {subject}",
    INTENT_COMPUTATION:    "Calculates {subject}",
    INTENT_FACTORY:        "Creates a new {subject}",
    INTENT_DELETION:       "Deletes {subject}",
    INTENT_SIDE_EFFECT:    "Performs {subject} operation",
    INTENT_TRANSFORMATION: "Transforms {subject}",
    INTENT_UNKNOWN:        "Performs unknown operation on {subject}",
}

# ─────────────────────────────────────────────────────────────
# Type annotation analyzer
# ─────────────────────────────────────────────────────────────

# (regex on return_type text, intent_hint, confidence)
_RETURN_TYPE_SIGNALS: List[Tuple[re.Pattern, str, float]] = [
    (re.compile(r"^void$",          re.I), INTENT_SIDE_EFFECT,   0.70),
    (re.compile(r"^undefined$",     re.I), INTENT_SIDE_EFFECT,   0.60),
    (re.compile(r"^never$",         re.I), INTENT_VALIDATION,    0.65),
    (re.compile(r"^bool(?:ean)?$",  re.I), INTENT_PREDICATE,     0.85),
    (re.compile(r"Promise\s*<\s*(?:void|undefined)", re.I), INTENT_SIDE_EFFECT, 0.65),
    (re.compile(r"Promise\s*<\s*bool(?:ean)?", re.I), INTENT_PREDICATE, 0.80),
    (re.compile(r"Observable\s*<", re.I), INTENT_IO_READ,        0.60),
    (re.compile(r"Promise\s*<",    re.I), INTENT_IO_READ,        0.55),
]


def _analyze_return_type(return_type: str) -> Tuple[Optional[str], float]:
    """Map TypeScript return type annotation to intent + confidence."""
    if not return_type:
        return None, 0.0
    rt = return_type.strip()
    for pattern, intent, conf in _RETURN_TYPE_SIGNALS:
        if pattern.search(rt):
            return intent, conf
    # Non-void concrete type → likely retrieval
    if rt and rt not in ("any", "unknown", "T", "U", "R"):
        return INTENT_RETRIEVAL, 0.45
    return None, 0.0


def _analyze_param_types(param_types: List[str]) -> Tuple[Optional[str], float]:
    """Extract weak intent hints from parameter type annotations."""
    if not param_types:
        return None, 0.0
    for pt in param_types:
        if not pt:
            continue
        pt_lower = pt.lower()
        if "event" in pt_lower:
            return INTENT_SIDE_EFFECT, 0.40
        if "request" in pt_lower or "req" == pt_lower:
            return INTENT_IO_READ, 0.35
        if "response" in pt_lower or "res" == pt_lower:
            return INTENT_IO_WRITE, 0.35
    return None, 0.0


# ─────────────────────────────────────────────────────────────
# Body analyzer
# ─────────────────────────────────────────────────────────────

_BODY_PATTERNS: Dict[str, List[re.Pattern]] = {
    INTENT_IO_READ: [
        re.compile(r"\bfetch\s*\(",           re.I),
        re.compile(r"\baxios\b",              re.I),
        re.compile(r"\bHttpClient\b",         re.I),
        re.compile(r"\.get\s*\(",             re.I),
        re.compile(r"await\s+\w+\.find",      re.I),
        re.compile(r"\bprisma\.\w+\.find",    re.I),
        re.compile(r"\bknex\(",               re.I),
        re.compile(r"localStorage\.getItem",  re.I),
        re.compile(r"sessionStorage\.getItem",re.I),
        re.compile(r"\bfs\.read",             re.I),
        re.compile(r"\bsupabase\.",           re.I),
    ],
    INTENT_IO_WRITE: [
        re.compile(r"\.post\s*\(",            re.I),
        re.compile(r"\.put\s*\(",             re.I),
        re.compile(r"\.patch\s*\(",           re.I),
        re.compile(r"\.save\s*\(",            re.I),
        re.compile(r"\.create\s*\(",          re.I),
        re.compile(r"\.insert\s*\(",          re.I),
        re.compile(r"\.update\s*\(",          re.I),
        re.compile(r"\.upsert\s*\(",          re.I),
        re.compile(r"localStorage\.setItem",  re.I),
        re.compile(r"sessionStorage\.setItem",re.I),
        re.compile(r"\bfs\.write",            re.I),
    ],
    INTENT_VALIDATION: [
        re.compile(r"\bthrow\s+new\b",        re.I),
        re.compile(r"\bthrow\b",              re.I),
        re.compile(r"throw.*Error",           re.I),
        re.compile(r"if\s*\(.*\)\s*throw",    re.I),
    ],
    INTENT_COMPUTATION: [
        re.compile(r"\bfor\s*\(",             re.I),
        re.compile(r"\bwhile\s*\(",           re.I),
        re.compile(r"\b\.reduce\s*\(",        re.I),
        re.compile(r"\bMath\.",               re.I),
        re.compile(r"[+\-*/]\s*\w",),
        re.compile(r"\bparseInt\b",           re.I),
        re.compile(r"\bparseFloat\b",         re.I),
    ],
    INTENT_SIDE_EFFECT: [
        re.compile(r"\bemit\s*\(",            re.I),
        re.compile(r"\.dispatch\s*\(",        re.I),
        re.compile(r"\bconsole\.",            re.I),
        re.compile(r"setState\s*\(",          re.I),
        re.compile(r"this\.state\s*=",        re.I),
        re.compile(r"\.addEventListener\s*\(",re.I),
        re.compile(r"\bsetTimeout\s*\(",      re.I),
        re.compile(r"\bsetInterval\s*\(",     re.I),
    ],
    INTENT_TRANSFORMATION: [
        re.compile(r"\b\.map\s*\(",           re.I),
        re.compile(r"\b\.filter\s*\(",        re.I),
        re.compile(r"\bJSON\.parse\b",        re.I),
        re.compile(r"\bJSON\.stringify\b",    re.I),
        re.compile(r"\bObject\.assign\b",     re.I),
        re.compile(r"\bSpread\b|\.\.\.",),
    ],
}

# How strongly each body pattern contributes to body signal
_BODY_MATCH_WEIGHT = 0.25  # per matched pattern, capped at 1.0


def _analyze_body(code: str) -> Tuple[Optional[str], float]:
    """
    Scan function body for IO, validation, computation, or side-effect patterns.

    Returns the dominant intent and a 0-1 confidence.
    """
    if not code:
        return None, 0.0

    scores: Dict[str, float] = {}
    for intent, patterns in _BODY_PATTERNS.items():
        count = sum(1 for p in patterns if p.search(code))
        if count:
            scores[intent] = min(count * _BODY_MATCH_WEIGHT, 1.0)

    if not scores:
        return None, 0.0

    best_intent = max(scores, key=lambda k: scores[k])
    return best_intent, round(scores[best_intent], 3)


# ─────────────────────────────────────────────────────────────
# Subject extraction (camelCase / snake_case → words)
# ─────────────────────────────────────────────────────────────

_CAMEL_SPLIT = re.compile(r"([a-z])([A-Z])|([A-Z]+)([A-Z][a-z])")
_ARTICLES     = re.compile(r"^(the|a|an)[\s_]?", re.I)


def _extract_subject(name: str, matched_pattern: re.Pattern) -> str:
    """Remove verb prefix then convert remainder to natural language."""
    remainder = matched_pattern.sub("", name, count=1).strip("_")
    remainder = _ARTICLES.sub("", remainder)

    # camelCase → spaced
    spaced = _CAMEL_SPLIT.sub(r"\1\3 \2\4", remainder)
    spaced = spaced.replace("_", " ")
    spaced = re.sub(r"\s+", " ", spaced).strip().lower()

    return spaced or "data"


# ─────────────────────────────────────────────────────────────
# Summary generator
# ─────────────────────────────────────────────────────────────


def _generate_summary(
    intent: str,
    subject: str,
    *,
    arity: int = 0,
    is_async: bool = False,
    has_comment: bool = False,
) -> str:
    template = _SUMMARY_TEMPLATES.get(intent, "Performs {subject} operation")
    summary = template.format(subject=subject)

    enrichments: List[str] = []
    if arity > 0:
        enrichments.append(f"takes {arity} parameter{'s' if arity > 1 else ''}")
    if is_async:
        enrichments.append("async")
    if enrichments:
        summary += f" ({', '.join(enrichments)})"

    return summary


# ─────────────────────────────────────────────────────────────
# Main engine
# ─────────────────────────────────────────────────────────────


class SemanticInferenceEngine:
    """
    Production-grade semantic inference for TypeScript (and other) functions.

    Usage:
        engine = SemanticInferenceEngine()

        # Single function
        result = engine.analyze(func_dict)

        # Whole corpus (in-place enrichment)
        engine.enrich_corpus(functions, calls)
    """

    def __init__(self) -> None:
        self._scorer = ConfidenceScorer()

    # ── Public: single-function analysis ─────────────────────

    def analyze(
        self,
        func: Dict[str, Any],
        usage_index: Optional[FunctionUsageIndex] = None,
    ) -> SemanticResult:
        """
        Analyze a single function dict and return a SemanticResult.

        Gracefully degrades if fields are missing.
        Never raises.
        """
        try:
            return self._analyze_safe(func, usage_index)
        except Exception:
            # Hard fallback — must never crash
            return SemanticResult(
                intent=INTENT_UNKNOWN,
                summary="Unknown operation",
                confidence=0.0,
                inferred=True,
                signals={"naming": 0.0, "type": 0.0, "usage": 0.0, "body": 0.0},
                side_effect=False,
            )

    # ── Public: batch enrichment ──────────────────────────────

    def enrich_corpus(
        self,
        functions: List[Dict[str, Any]],
        calls: List[Dict[str, Any]],
    ) -> None:
        """
        Enrich a list of function dicts in place with semantic metadata.

        Only enriches functions where comment/summary is empty
        (preserves developer-written docs).

        Sets on each dict:
          - intent         (str)
          - summary        (str)  — if no existing comment
          - inferred_doc   (bool)
          - doc_confidence (float)
          - signals        (dict)
          - side_effect    (bool)

        Also updates 'note' to include semantic context for embedding quality.
        """
        if not functions:
            return

        # Build usage index from call edges
        usage_index = build_usage_index(functions, calls)

        for func in functions:
            try:
                self._enrich_one(func, usage_index)
            except Exception:
                # Isolate per-function crashes
                self._apply_fallback(func)

    # ── Private ───────────────────────────────────────────────

    def _analyze_safe(
        self,
        func: Dict[str, Any],
        usage_index: Optional[FunctionUsageIndex],
    ) -> SemanticResult:
        name        = func.get("name") or ""
        code        = func.get("code") or ""
        comment     = func.get("comment") or ""
        return_type = func.get("return_type") or ""
        param_types = func.get("param_types") or []
        arity       = int(func.get("arity") or 0)
        is_exported = bool(func.get("exported", False))
        is_async    = bool(func.get("is_async", False)) or bool(
            re.search(r"\basync\b", code[:120])
        )
        func_id = func.get("symbol_id") or ""

        # ── Signal 1: naming ─────────────────────────────────
        naming_intent, naming_conf, matched_pat = self._naming_signal(name)

        # ── Signal 2: type annotation ────────────────────────
        type_intent_rt, type_conf_rt = _analyze_return_type(return_type)
        type_intent_pt, type_conf_pt = _analyze_param_types(param_types)
        type_conf   = max(type_conf_rt, type_conf_pt)
        type_intent = type_intent_rt or type_intent_pt

        # ── Signal 3: body ───────────────────────────────────
        body_intent, body_conf = _analyze_body(code)

        # ── Signal 4: usage (requires index) ─────────────────
        if usage_index is not None:
            usage_intent, usage_conf = get_usage_signal(func_id, name, usage_index)
        else:
            usage_intent, usage_conf = None, 0.0

        # ── Determine final intent (priority: usage > type > naming > body) ─
        intent = self._resolve_intent(
            naming_intent, naming_conf,
            type_intent,   type_conf,
            body_intent,   body_conf,
            usage_intent,  usage_conf,
        )

        # ── Build subject for summary ─────────────────────────
        if matched_pat is not None:
            subject = _extract_subject(name, matched_pat)
        else:
            subject = _camel_to_words(name)

        # ── Detect side-effect ───────────────────────────────
        is_side_effect = (
            intent in (INTENT_SIDE_EFFECT, INTENT_IO_WRITE)
            or (return_type.strip().lower() in ("void", "undefined", ""))
            and intent not in (INTENT_PREDICATE, INTENT_RETRIEVAL)
        )

        # ── Generate summary ─────────────────────────────────
        if comment:
            summary  = comment.lstrip("/* \n").splitlines()[0].strip()
            inferred = False
        else:
            summary  = _generate_summary(intent, subject, arity=arity, is_async=is_async)
            inferred = True

        # ── Final confidence ──────────────────────────────────
        signals = {
            "naming": round(naming_conf, 3),
            "type":   round(type_conf, 3),
            "usage":  round(usage_conf, 3),
            "body":   round(body_conf, 3),
        }
        confidence = self._scorer.score_dict(
            signals, func_name=name, is_exported=is_exported
        )

        return SemanticResult(
            intent=intent,
            summary=summary,
            confidence=confidence,
            inferred=inferred,
            signals=signals,
            side_effect=is_side_effect,
        )

    def _enrich_one(
        self,
        func: Dict[str, Any],
        usage_index: FunctionUsageIndex,
    ) -> None:
        result = self._analyze_safe(func, usage_index)

        func["intent"]        = result.intent
        func["inferred_doc"]  = result.inferred
        func["doc_confidence"] = result.confidence
        func["signals"]       = result.signals
        func["side_effect"]   = result.side_effect

        # Only set summary if no developer-written comment
        if not func.get("comment"):
            func["summary"] = result.summary

        # Rebuild note with semantic context for richer embeddings
        func["note"] = _build_semantic_note(
            code        = func.get("code") or "",
            comment     = func.get("comment") or "",
            summary     = func.get("summary") or "",
            intent      = result.intent,
            confidence  = result.confidence,
            inferred    = result.inferred,
        )

    @staticmethod
    def _apply_fallback(func: Dict[str, Any]) -> None:
        func.setdefault("intent",        INTENT_UNKNOWN)
        func.setdefault("inferred_doc",  True)
        func.setdefault("doc_confidence", 0.0)
        func.setdefault("signals",       {"naming": 0.0, "type": 0.0, "usage": 0.0, "body": 0.0})
        func.setdefault("side_effect",   False)
        if not func.get("summary"):
            func["summary"] = "Unknown operation"

    # ── Naming signal ─────────────────────────────────────────

    @staticmethod
    def _naming_signal(
        name: str,
    ) -> Tuple[str, float, Optional[re.Pattern]]:
        """Return (intent, confidence, matched_pattern)."""
        if not name:
            return INTENT_UNKNOWN, 0.0, None

        # Constructor
        if name in ("constructor", "__init__", "initialize"):
            return INTENT_FACTORY, 0.90, None

        # Event handler: onXxx / handleXxx
        on_match = re.match(r"^on([A-Z_])", name)
        if on_match:
            return INTENT_SIDE_EFFECT, 0.72, re.compile(r"^on", re.I)

        for compiled_pat, intent, conf in _COMPILED_PATTERNS:
            if compiled_pat.match(name):
                return intent, conf, compiled_pat

        # No match — short names get a tiny signal
        if len(name) <= 3:
            return INTENT_UNKNOWN, 0.15, None

        return INTENT_UNKNOWN, 0.0, None

    # ── Intent resolution ─────────────────────────────────────

    @staticmethod
    def _resolve_intent(
        naming_intent: str,  naming_conf: float,
        type_intent:   Optional[str], type_conf: float,
        body_intent:   Optional[str], body_conf: float,
        usage_intent:  Optional[str], usage_conf: float,
    ) -> str:
        """
        Determine final intent using priority ordering.

        priority: usage (≥0.50) > type (≥0.65) > naming (≥0.60) > body (≥0.50) > unknown
        """
        # Usage is authoritative when confident
        if usage_intent and usage_conf >= 0.50:
            return usage_intent

        # Type overrides naming for void/bool/Promise patterns
        if type_intent and type_conf >= 0.65:
            # Only override naming if they disagree
            if type_intent != naming_intent:
                return type_intent

        # Naming
        if naming_intent and naming_intent != INTENT_UNKNOWN and naming_conf >= 0.60:
            return naming_intent

        # Body as last resort
        if body_intent and body_conf >= 0.50:
            return body_intent

        return naming_intent if naming_intent != INTENT_UNKNOWN else INTENT_UNKNOWN


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────


def _camel_to_words(name: str) -> str:
    spaced = _CAMEL_SPLIT.sub(r"\1\3 \2\4", name)
    spaced = spaced.replace("_", " ")
    return re.sub(r"\s+", " ", spaced).strip().lower() or "data"


def _build_semantic_note(
    code:       str,
    comment:    str,
    summary:    str,
    intent:     str,
    confidence: float,
    inferred:   bool,
) -> str:
    """Build an enriched note string for Qdrant embedding input."""
    parts: List[str] = []

    if summary:
        label = "Summary (inferred)" if inferred else "Summary"
        parts.append(f"{label}:\n{summary}")

    if intent and intent != INTENT_UNKNOWN:
        parts.append(f"Intent: {intent} (confidence: {confidence:.2f})")

    if comment:
        parts.append(f"Comment:\n{comment}")

    if code:
        parts.append(f"Code:\n{code}")

    return "\n\n".join(parts)
