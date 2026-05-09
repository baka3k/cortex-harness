"""
Frontend Relationship Extractor — Framework-Aware Semantic Inference Engine V1.0

Architecture
──────────────────────────────────────────────────────────────────────────────
Extracts structured graph relationships from frontend codebases by interpreting
code through the lens of framework semantics rather than raw string matching.

Inference pipeline:
  AST Extraction → Framework Semantic Mapping → Intent Detection
    → Relationship Mapping → Contextual Reasoning

Supported relationship types:
  STRUCTURAL  : RENDER
  EXECUTION   : CALLS
  BEHAVIORAL  : NAVIGATE, STATE_UPDATE, SIDE_EFFECT

Supported frameworks:
  React / React-Native   (JSX, hooks, event handlers, router)
  Vue.js                 (template, ref/reactive, lifecycle hooks, vue-router)

Extensibility:
  - New relationship types: add a RelationshipRule to _RULES or register via
    FrontendRelationshipExtractor.register_rule().
  - New frameworks: implement a FrameworkSemanticMapper subclass, register via
    FrontendRelationshipExtractor.register_framework().
  - New semantic rules: extend _FRAMEWORK_SIGNALS per framework key.

Public API
──────────────────────────────────────────────────────────────────────────────
  extractor = FrontendRelationshipExtractor()

  # Single function
  result: ExtractionResult = extractor.extract(func_dict, context)

  # Batch enrichment (resolves cross-function context)
  results: List[ExtractionResult] = extractor.extract_batch(functions, calls)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


# ─────────────────────────────────────────────────────────────────────────────
# Relationship type constants
# ─────────────────────────────────────────────────────────────────────────────

REL_RENDER       = "RENDER"
REL_CALLS        = "CALLS"
REL_NAVIGATE     = "NAVIGATE"
REL_STATE_UPDATE = "STATE_UPDATE"
REL_SIDE_EFFECT  = "SIDE_EFFECT"


# ─────────────────────────────────────────────────────────────────────────────
# Semantic intent constants
# ─────────────────────────────────────────────────────────────────────────────

INTENT_UI_COMPOSITION  = "ui_composition"
INTENT_FUNCTION_CALL   = "function_call"
INTENT_SCREEN_NAVIGATE = "screen_navigate"
INTENT_STATE_MUTATION  = "state_mutation"
INTENT_SIDE_EFFECT     = "side_effect"
INTENT_EVENT_TRIGGER   = "event_trigger"
INTENT_LIFECYCLE       = "lifecycle"
INTENT_UNKNOWN         = "unknown"

# Intent → Relationship mapping (extensible)
_INTENT_TO_REL: Dict[str, str] = {
    INTENT_UI_COMPOSITION:  REL_RENDER,
    INTENT_FUNCTION_CALL:   REL_CALLS,
    INTENT_SCREEN_NAVIGATE: REL_NAVIGATE,
    INTENT_STATE_MUTATION:  REL_STATE_UPDATE,
    INTENT_SIDE_EFFECT:     REL_SIDE_EFFECT,
    INTENT_EVENT_TRIGGER:   REL_SIDE_EFFECT,
    INTENT_LIFECYCLE:       REL_SIDE_EFFECT,
}


# ─────────────────────────────────────────────────────────────────────────────
# Framework signal tables (extensible per framework)
# ─────────────────────────────────────────────────────────────────────────────

# Structure: {framework: {intent: [(pattern, confidence_adjustment)]}}
_FRAMEWORK_SIGNALS: Dict[str, Dict[str, List[Tuple[re.Pattern, float]]]] = {
    "react": {
        INTENT_UI_COMPOSITION: [
            # JSX element usage — most reliable signal
            (re.compile(r"<[A-Z][A-Za-z0-9]*[\s/>]"),          0.95),
            # React.createElement / React.cloneElement
            (re.compile(r"\bReact\.(?:createElement|cloneElement)\s*\("), 0.90),
            # JSX spread (component children)
            (re.compile(r"\{\.\.\.children\}"),                 0.70),
        ],
        INTENT_STATE_MUTATION: [
            # React hooks — state setters
            (re.compile(r"\bsetState\s*\("),                    0.90),
            (re.compile(r"\buse(?:State|Reducer)\s*\("),        0.85),
            # Hook-based setter calls: setXxx(...)
            (re.compile(r"\bset[A-Z]\w*\s*\("),                 0.80),
            # Zustand / Jotai / Valtio stores
            (re.compile(r"\bset\b.*\bState\b"),                 0.65),
            (re.compile(r"\buseAtom\b|\buseStore\b"),           0.70),
            # Redux dispatch
            (re.compile(r"\bdispatch\s*\("),                    0.75),
        ],
        INTENT_SCREEN_NAVIGATE: [
            # React Navigation
            (re.compile(r"\bnavigation\.(?:navigate|push|replace|reset)\b"), 0.95),
            (re.compile(r"\buseNavigation\s*\("),               0.85),
            # React Router v6
            (re.compile(r'\bnavigate\s*\([\'"`/]'),             0.90),
            (re.compile(r"\buseNavigate\s*\("),                 0.85),
            # React Router v5
            (re.compile(r"\bhistory\.(?:push|replace)\s*\("),   0.88),
            (re.compile(r"\buseHistory\s*\("),                  0.80),
            # Expo / Next.js router
            (re.compile(r"\brouter\.(?:push|navigate|replace)\s*\("), 0.90),
            (re.compile(r"\buseRouter\s*\("),                   0.80),
            # Link / Navigate JSX elements
            (re.compile(r"<(?:Link|Navigate)\s"),               0.75),
        ],
        INTENT_SIDE_EFFECT: [
            # React useEffect — side-effects by definition
            (re.compile(r"\buseEffect\s*\("),                   0.90),
            # useCallback / useMemo wrapping side effects
            (re.compile(r"\buseCallback\s*\("),                 0.50),
            # Event subscriptions
            (re.compile(r"\.addEventListener\s*\("),            0.80),
            (re.compile(r"\.removeEventListener\s*\("),         0.80),
            # Timers
            (re.compile(r"\bsetTimeout\s*\(|\bsetInterval\s*\("), 0.75),
            # Focus / blur effects (React Navigation)
            (re.compile(r"\buseFocusEffect\s*\("),              0.85),
        ],
        INTENT_LIFECYCLE: [
            # Class component lifecycle
            (re.compile(r"\bcomponentDidMount\b|\bcomponentWillUnmount\b"), 0.95),
            (re.compile(r"\bcomponentDidUpdate\b"),             0.90),
            (re.compile(r"\bshouldComponentUpdate\b"),          0.88),
        ],
        INTENT_EVENT_TRIGGER: [
            # JSX event props
            (re.compile(r"\bon[A-Z]\w+\s*=\s*\{"),             0.80),
            # Event handler patterns
            (re.compile(r"\bonPress\b|\bonClick\b|\bonChange\b"), 0.85),
            (re.compile(r"\bonSubmit\b|\bonFocus\b|\bonBlur\b"), 0.82),
        ],
    },

    "vue": {
        INTENT_UI_COMPOSITION: [
            # Vue SFC template component usage
            (re.compile(r"<[A-Z][A-Za-z0-9]*(?:\s|/)"),        0.92),
            # Vue dynamic component
            (re.compile(r"\bcomponent\s+:is="),                 0.85),
            # Vue render function
            (re.compile(r"\bh\s*\(\s*[\w'\"]"),                   0.80),
        ],
        INTENT_STATE_MUTATION: [
            # Vue 3 Composition API
            (re.compile(r"\bref\s*\(|\breactive\s*\("),         0.85),
            (re.compile(r"\.value\s*="),                        0.75),
            # Vue 3 stores (Pinia)
            (re.compile(r"\buseStore\b|\bdefineStore\b"),       0.80),
            # Vuex 4
            (re.compile(r"\b\$store\.commit\s*\("),             0.88),
            (re.compile(r"\b\$store\.dispatch\s*\("),           0.85),
            (re.compile(r"\buseStore\(\)\.commit\b"),           0.85),
        ],
        INTENT_SCREEN_NAVIGATE: [
            # Vue Router
            (re.compile(r"\bthis\.\$router\.(?:push|replace|go)\s*\("), 0.95),
            (re.compile(r"\buseRouter\s*\("),                   0.85),
            (re.compile(r"\brouter\.(?:push|replace|go)\s*\("), 0.90),
            # RouterLink / router-link JSX
            (re.compile(r"<router-link\b|<RouterLink\b"),       0.80),
        ],
        INTENT_SIDE_EFFECT: [
            # Vue lifecycle hooks
            (re.compile(r"\bonMounted\s*\(|\bonUnmounted\s*\("), 0.90),
            (re.compile(r"\bonBeforeMount\b|\bonBeforeUnmount\b"), 0.88),
            (re.compile(r"\bwatchEffect\s*\("),                 0.85),
            (re.compile(r"\bwatch\s*\("),                       0.80),
        ],
        INTENT_LIFECYCLE: [
            # Options API lifecycle
            (re.compile(r"\bcreated\s*\(\)|\bmounted\s*\(\)"),  0.95),
            (re.compile(r"\bbeforeDestroy\b|\bdestroyed\b"),    0.90),
            (re.compile(r"\bupdated\s*\(\)"),                   0.85),
        ],
        INTENT_EVENT_TRIGGER: [
            # Vue events
            (re.compile(r"\b\$emit\s*\("),                      0.90),
            (re.compile(r"\bv-on:|@[a-z]"),                     0.85),
            (re.compile(r"\bemit\s*\("),                        0.75),
        ],
    },
}

# React-Native is React with extra navigation signals
_FRAMEWORK_SIGNALS["react-native"] = {
    **_FRAMEWORK_SIGNALS["react"],
    INTENT_SCREEN_NAVIGATE: [
        *_FRAMEWORK_SIGNALS["react"][INTENT_SCREEN_NAVIGATE],
        # StackNavigator-specific
        (re.compile(r"\bnavigation\.goBack\s*\("),              0.88),
        (re.compile(r"\bnavigation\.popToTop\s*\("),            0.85),
        # Expo Router
        (re.compile(r"\bhref\s*=\s*[\w'\"]/"),                  0.70),
    ],
}


# ─────────────────────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class FrameworkContext:
    """Contextual information used to interpret code semantics."""
    framework: str = "react"              # "react" | "vue" | "react-native"
    component_hierarchy: Dict[str, str] = field(default_factory=dict)
    # symbol_id → parent_symbol_id (RENDER ownership chain)
    call_graph: Dict[str, List[str]] = field(default_factory=dict)
    # symbol_id → [callee_symbol_ids]
    data_flow: Dict[str, List[str]] = field(default_factory=dict)
    # symbol_id → [symbols whose data flows into this function]
    known_screens: frozenset = field(default_factory=frozenset)
    # set of component *names* (str) confirmed as screens (react_role == "screen")


@dataclass
class SemanticSignal:
    """A single detected semantic signal from one extraction phase."""
    phase: str          # "framework_map" | "intent_detect" | "context_reason"
    intent: str
    confidence: float
    evidence: str       # short description of what triggered this signal


@dataclass
class RelationshipEdge:
    """A directed semantic relationship between two code symbols."""
    source_id: str
    target_id: str          # may be "" for unresolved targets
    target_name: str        # raw name (always present)
    rel_type: str
    confidence: float
    properties: Dict[str, Any] = field(default_factory=dict)
    # e.g. {"method": "navigate", "trigger": "user", "via": "direct"}


@dataclass
class ExtractionResult:
    """Structured output from relationship extraction for one function."""
    symbol_id: str
    relationships: List[RelationshipEdge]
    signals: List[SemanticSignal]
    framework: str
    # Summary of top-level intents detected
    detected_intents: List[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Relationship extraction rule interface
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class RelationshipRule:
    """
    Declarative rule that maps a detected intent to a relationship extractor.

    A rule is invoked only when the target intent is confirmed for a function.
    The extractor callable receives (func_dict, context, matched_signals) and
    returns a list of RelationshipEdge objects.
    """
    intent: str
    rel_type: str
    extractor: Callable[
        [Dict[str, Any], FrameworkContext, List[SemanticSignal]],
        List[RelationshipEdge],
    ]
    # minimum confidence required to activate this rule
    min_confidence: float = 0.50
    # frameworks this rule applies to; empty set = all frameworks
    frameworks: frozenset = field(default_factory=frozenset)


# ─────────────────────────────────────────────────────────────────────────────
# Built-in extractors (Phase 4 — Relationship Mapping)
# ─────────────────────────────────────────────────────────────────────────────

_RE_JSX_COMPONENT = re.compile(
    r"<([A-Z][A-Za-z0-9]*)[\s/>]",
    re.MULTILINE,
)
_RE_CALLS_GENERIC = re.compile(
    r"\b([a-zA-Z_$][a-zA-Z0-9_$]*)\s*\(",
    re.MULTILINE,
)
_RE_STATE_SETTER = re.compile(
    r"\b(set[A-Z]\w*|setState|dispatch)\s*\(",
    re.MULTILINE,
)
_RE_NAVIGATE_CALL = re.compile(
    r"""(?:
        navigation\.(?:navigate|push|replace|reset)\s*\(\s*["'`](?P<t1>[^"'`\s,)]+)
        |navigate\s*\(\s*["'`](?P<t2>[^"'`\s,)]+)
        |router\.(?:push|navigate|replace)\s*\(\s*["'`](?P<t3>[^"'`\s,)]+)
        |history\.(?:push|replace)\s*\(\s*["'`](?P<t4>[^"'`\s,)]+)
        |\$router\.(?:push|replace)\s*\(\s*["'`](?P<t5>[^"'`\s,)]+)
    )""",
    re.VERBOSE | re.MULTILINE,
)
_RE_EFFECT_DEPS = re.compile(
    r"useEffect\s*\(\s*\(\s*\)\s*=>\s*\{",
    re.DOTALL,
)


def _extract_render_edges(
    func: Dict[str, Any],
    ctx: FrameworkContext,
    signals: List[SemanticSignal],
) -> List[RelationshipEdge]:
    """Extract RENDER edges from JSX component usage in function body.

    Screen→Screen pairs are intentionally skipped: navigation between
    screens is modelled by NAVIGATE edges, not RENDER edges.
    """
    code = func.get("code") or ""
    source_id = func.get("symbol_id") or ""
    source_is_screen = func.get("react_role") == "screen"
    edges: List[RelationshipEdge] = []
    seen: set = set()
    for m in _RE_JSX_COMPONENT.finditer(code):
        comp_name = m.group(1)
        if comp_name in seen:
            continue
        seen.add(comp_name)
        # Skip structural React helpers
        if comp_name in {"Fragment", "StrictMode", "Suspense", "ErrorBoundary"}:
            continue
        # Screen renders Screen → NAVIGATE system handles this, not RENDER
        if source_is_screen and comp_name in ctx.known_screens:
            continue
        edges.append(RelationshipEdge(
            source_id=source_id,
            target_id="",           # resolved in post-processing
            target_name=comp_name,
            rel_type=REL_RENDER,
            confidence=0.90,
            properties={"rendered_name": comp_name},
        ))
    return edges


def _extract_calls_edges(
    func: Dict[str, Any],
    ctx: FrameworkContext,
    signals: List[SemanticSignal],
) -> List[RelationshipEdge]:
    """Extract CALLS edges from function call expressions in the body."""
    code = func.get("code") or ""
    source_id = func.get("symbol_id") or ""
    edges: List[RelationshipEdge] = []
    # Use call graph if available (pre-computed in ts_analyzer)
    callees = ctx.call_graph.get(source_id, [])
    for callee_id in callees:
        edges.append(RelationshipEdge(
            source_id=source_id,
            target_id=callee_id,
            target_name="",
            rel_type=REL_CALLS,
            confidence=0.95,
        ))
    # If no call graph, fall back to text-based extraction (lower confidence)
    if not callees and code:
        seen: set = set()
        for m in _RE_CALLS_GENERIC.finditer(code):
            callee_name = m.group(1)
            if callee_name in seen:
                continue
            seen.add(callee_name)
            _BUILTINS = {
                "if", "for", "while", "switch", "catch", "return",
                "typeof", "instanceof", "new", "await", "yield",
                "console", "require", "import",
            }
            if callee_name in _BUILTINS or len(callee_name) <= 1:
                continue
            edges.append(RelationshipEdge(
                source_id=source_id,
                target_id="",
                target_name=callee_name,
                rel_type=REL_CALLS,
                confidence=0.55,    # textual only — lower confidence
            ))
    return edges


def _extract_navigate_edges(
    func: Dict[str, Any],
    ctx: FrameworkContext,
    signals: List[SemanticSignal],
) -> List[RelationshipEdge]:
    """Extract NAVIGATE edges from navigation call expressions."""
    code = func.get("code") or ""
    source_id = func.get("symbol_id") or ""
    edges: List[RelationshipEdge] = []
    seen: set = set()
    for m in _RE_NAVIGATE_CALL.finditer(code):
        # Pick the first non-None named group
        target = next(
            (v for v in (
                m.group("t1"), m.group("t2"), m.group("t3"),
                m.group("t4"), m.group("t5"),
            ) if v),
            None,
        )
        if not target or target in seen:
            continue
        seen.add(target)
        # Determine method from matched text
        nav_text = m.group(0)
        if "push" in nav_text:
            method = "push"
        elif "replace" in nav_text:
            method = "replace"
        elif "reset" in nav_text:
            method = "reset"
        else:
            method = "navigate"
        edges.append(RelationshipEdge(
            source_id=source_id,
            target_id="",
            target_name=target,
            rel_type=REL_NAVIGATE,
            confidence=0.88,
            properties={"method": method, "trigger_type": "user"},
        ))
    return edges


def _extract_state_update_edges(
    func: Dict[str, Any],
    ctx: FrameworkContext,
    signals: List[SemanticSignal],
) -> List[RelationshipEdge]:
    """Extract STATE_UPDATE edges from state setter/dispatch calls."""
    code = func.get("code") or ""
    source_id = func.get("symbol_id") or ""
    edges: List[RelationshipEdge] = []
    seen: set = set()
    for m in _RE_STATE_SETTER.finditer(code):
        setter = m.group(1)
        if setter in seen:
            continue
        seen.add(setter)
        edges.append(RelationshipEdge(
            source_id=source_id,
            target_id="",
            target_name=setter,
            rel_type=REL_STATE_UPDATE,
            confidence=0.80,
            properties={"setter": setter},
        ))
    return edges


def _extract_side_effect_edges(
    func: Dict[str, Any],
    ctx: FrameworkContext,
    signals: List[SemanticSignal],
) -> List[RelationshipEdge]:
    """Extract SIDE_EFFECT edges: useEffect, event subscriptions, timers."""
    code = func.get("code") or ""
    source_id = func.get("symbol_id") or ""
    edges: List[RelationshipEdge] = []

    if _RE_EFFECT_DEPS.search(code):
        edges.append(RelationshipEdge(
            source_id=source_id,
            target_id="",
            target_name="useEffect",
            rel_type=REL_SIDE_EFFECT,
            confidence=0.90,
            properties={"kind": "react_effect"},
        ))
    return edges


# ─────────────────────────────────────────────────────────────────────────────
# Built-in rule registry
# ─────────────────────────────────────────────────────────────────────────────

_RULES: List[RelationshipRule] = [
    RelationshipRule(
        intent=INTENT_UI_COMPOSITION,
        rel_type=REL_RENDER,
        extractor=_extract_render_edges,
        min_confidence=0.70,
    ),
    RelationshipRule(
        intent=INTENT_FUNCTION_CALL,
        rel_type=REL_CALLS,
        extractor=_extract_calls_edges,
        min_confidence=0.50,
    ),
    RelationshipRule(
        intent=INTENT_SCREEN_NAVIGATE,
        rel_type=REL_NAVIGATE,
        extractor=_extract_navigate_edges,
        min_confidence=0.70,
    ),
    RelationshipRule(
        intent=INTENT_STATE_MUTATION,
        rel_type=REL_STATE_UPDATE,
        extractor=_extract_state_update_edges,
        min_confidence=0.65,
    ),
    RelationshipRule(
        intent=INTENT_SIDE_EFFECT,
        rel_type=REL_SIDE_EFFECT,
        extractor=_extract_side_effect_edges,
        min_confidence=0.70,
    ),
    RelationshipRule(
        intent=INTENT_EVENT_TRIGGER,
        rel_type=REL_SIDE_EFFECT,
        extractor=_extract_side_effect_edges,
        min_confidence=0.60,
    ),
]


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 — Framework semantic mapping
# ─────────────────────────────────────────────────────────────────────────────


def _map_framework_signals(
    func: Dict[str, Any],
    framework: str,
) -> List[SemanticSignal]:
    """
    Phase 2: Map code constructs to semantic intents for the given framework.

    Does NOT rely on a single string match; accumulates evidence across
    all framework-specific signal patterns and returns all triggered signals.
    """
    code = func.get("code") or ""
    if not code:
        return []

    # Resolve to known framework key
    fw_key = _resolve_framework_key(framework)
    fw_signals = _FRAMEWORK_SIGNALS.get(fw_key, {})

    results: List[SemanticSignal] = []
    for intent, patterns in fw_signals.items():
        for pattern, base_conf in patterns:
            if pattern.search(code):
                results.append(SemanticSignal(
                    phase="framework_map",
                    intent=intent,
                    confidence=base_conf,
                    evidence=pattern.pattern[:60],
                ))
    return results


def _resolve_framework_key(framework: str) -> str:
    """Normalize framework name to a key in _FRAMEWORK_SIGNALS."""
    fw = framework.lower().replace(" ", "-").replace("_", "-")
    if fw in _FRAMEWORK_SIGNALS:
        return fw
    if "native" in fw:
        return "react-native"
    if "vue" in fw:
        return "vue"
    return "react"  # default


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 — Intent detection (aggregate signals)
# ─────────────────────────────────────────────────────────────────────────────


def _detect_intents(
    signals: List[SemanticSignal],
    existing_intent: Optional[str] = None,
    threshold: float = 0.50,
) -> List[Tuple[str, float]]:
    """
    Phase 3: Aggregate signals into a ranked list of (intent, confidence) tuples.

    Does NOT hardcode per-intent logic. Instead:
    - Groups signals by intent
    - Averages confidence scores (weighted by evidence count)
    - Applies threshold filtering
    - Incorporates existing semantic intent from SemanticInferenceEngine
    """
    # Aggregate by intent
    intent_scores: Dict[str, List[float]] = {}
    for sig in signals:
        intent_scores.setdefault(sig.intent, []).append(sig.confidence)

    # Integrate upstream semantic intent (from SemanticInferenceEngine)
    if existing_intent and existing_intent != "unknown":
        mapped = _semantic_to_frontend_intent(existing_intent)
        if mapped:
            intent_scores.setdefault(mapped, []).append(0.70)

    # Compute final confidence: max of all signals for each intent
    # (single strong signal is sufficient; multiple signals reinforce)
    ranked: List[Tuple[str, float]] = []
    for intent, scores in intent_scores.items():
        # Take max + averaged bonus for corroborating signals
        base = max(scores)
        bonus = min(0.05 * (len(scores) - 1), 0.10)
        confidence = min(base + bonus, 1.0)
        if confidence >= threshold:
            ranked.append((intent, round(confidence, 3)))

    ranked.sort(key=lambda x: x[1], reverse=True)
    return ranked


def _semantic_to_frontend_intent(semantic_intent: str) -> Optional[str]:
    """Map SemanticInferenceEngine intent taxonomy to frontend intent taxonomy."""
    _MAP: Dict[str, str] = {
        "mutation":     INTENT_STATE_MUTATION,
        "io_write":     INTENT_STATE_MUTATION,
        "side_effect":  INTENT_SIDE_EFFECT,
        "io_read":      INTENT_FUNCTION_CALL,
        "retrieval":    INTENT_FUNCTION_CALL,
        "transformation": INTENT_FUNCTION_CALL,
        "computation":  INTENT_FUNCTION_CALL,
    }
    return _MAP.get(semantic_intent)


# ─────────────────────────────────────────────────────────────────────────────
# Phase 5 — Contextual reasoning
# ─────────────────────────────────────────────────────────────────────────────


def _apply_contextual_reasoning(
    edges: List[RelationshipEdge],
    func: Dict[str, Any],
    ctx: FrameworkContext,
) -> List[RelationshipEdge]:
    """
    Phase 5: Refine extracted edges using component hierarchy and call graph.

    - Resolves edge confidence using hierarchy depth (deeper = less confident)
    - Promotes CALLS to SIDE_EFFECT when the called function has known side effects
    - Handles unknown cases gracefully (lowers confidence, never hallucinate)
    """
    symbol_id = func.get("symbol_id") or ""
    react_role = func.get("react_role") or ""
    refined: List[RelationshipEdge] = []

    for edge in edges:
        edge = _apply_hierarchy_penalty(edge, symbol_id, ctx)
        edge = _promote_side_effect(edge, ctx)
        if react_role not in ("screen", "component", "hook") and edge.rel_type == REL_NAVIGATE:
            # Non-UI functions emitting NAVIGATE — reduce confidence
            edge = RelationshipEdge(
                source_id=edge.source_id,
                target_id=edge.target_id,
                target_name=edge.target_name,
                rel_type=edge.rel_type,
                confidence=round(edge.confidence * 0.75, 3),
                properties=edge.properties,
            )
        refined.append(edge)

    return refined


def _apply_hierarchy_penalty(
    edge: RelationshipEdge,
    symbol_id: str,
    ctx: FrameworkContext,
) -> RelationshipEdge:
    """Reduce confidence for deeply nested component renders."""
    if edge.rel_type != REL_RENDER:
        return edge
    # Count depth in hierarchy
    depth = 0
    parent = ctx.component_hierarchy.get(symbol_id)
    while parent and depth < 5:
        depth += 1
        parent = ctx.component_hierarchy.get(parent)
    if depth > 2:
        penalized = max(0.30, edge.confidence - 0.05 * depth)
        return RelationshipEdge(
            source_id=edge.source_id,
            target_id=edge.target_id,
            target_name=edge.target_name,
            rel_type=edge.rel_type,
            confidence=round(penalized, 3),
            properties=edge.properties,
        )
    return edge


def _promote_side_effect(
    edge: RelationshipEdge,
    ctx: FrameworkContext,
) -> RelationshipEdge:
    """Promote a CALLS edge to SIDE_EFFECT if the callee is a known side-effect function."""
    _SIDE_EFFECT_NAMES = {
        "fetch", "axios", "setTimeout", "setInterval",
        "localStorage", "sessionStorage", "console",
        "EventEmitter", "emit", "dispatch",
    }
    if edge.rel_type == REL_CALLS and edge.target_name in _SIDE_EFFECT_NAMES:
        return RelationshipEdge(
            source_id=edge.source_id,
            target_id=edge.target_id,
            target_name=edge.target_name,
            rel_type=REL_SIDE_EFFECT,
            confidence=min(edge.confidence + 0.10, 1.0),
            properties={**edge.properties, "promoted_from": "CALLS"},
        )
    return edge


# ─────────────────────────────────────────────────────────────────────────────
# Main extractor
# ─────────────────────────────────────────────────────────────────────────────


class FrontendRelationshipExtractor:
    """
    Framework-aware semantic relationship extractor for frontend codebases.

    Inference pipeline:
      Phase 1 (AST, done upstream) → Phase 2 (Framework Semantic Map)
        → Phase 3 (Intent Detection) → Phase 4 (Relationship Mapping)
          → Phase 5 (Contextual Reasoning)

    Extensibility points:
      - register_rule(rule): add a new RelationshipRule
      - register_framework(key, signals): add framework signal table
    """

    def __init__(self) -> None:
        self._rules: List[RelationshipRule] = list(_RULES)

    # ── Extensibility API ─────────────────────────────────────────────────────

    def register_rule(self, rule: RelationshipRule) -> None:
        """Register an additional relationship extraction rule."""
        self._rules.append(rule)

    def register_framework(
        self,
        framework_key: str,
        signals: Dict[str, List[Tuple[re.Pattern, float]]],
    ) -> None:
        """Register a new framework signal table."""
        _FRAMEWORK_SIGNALS[framework_key] = signals

    # ── Public: single function ───────────────────────────────────────────────

    def extract(
        self,
        func: Dict[str, Any],
        ctx: Optional[FrameworkContext] = None,
    ) -> ExtractionResult:
        """
        Extract relationships for a single function dict.

        Gracefully degrades: never raises, assigns lower confidence on ambiguity.

        Args:
            func: Function dict from ts_analyzer or another analyzer
            ctx:  Optional FrameworkContext with call graph + hierarchy info

        Returns:
            ExtractionResult with relationships and signals
        """
        if ctx is None:
            ctx = FrameworkContext()

        symbol_id = func.get("symbol_id") or ""

        try:
            return self._extract_safe(func, ctx, symbol_id)
        except Exception:
            # Hard fallback — never crash
            return ExtractionResult(
                symbol_id=symbol_id,
                relationships=[],
                signals=[SemanticSignal(
                    phase="fallback",
                    intent=INTENT_UNKNOWN,
                    confidence=0.0,
                    evidence="extraction_error",
                )],
                framework=ctx.framework,
                detected_intents=[INTENT_UNKNOWN],
            )

    # ── Public: batch extraction ──────────────────────────────────────────────

    def extract_batch(
        self,
        functions: List[Dict[str, Any]],
        calls: Optional[List[Dict[str, Any]]] = None,
        framework: str = "react",
    ) -> List[ExtractionResult]:
        """
        Extract relationships for a list of functions with shared context.

        The call graph is computed from `calls` and shared across all functions
        to enable Phase 5 contextual reasoning.

        Args:
            functions: List of function dicts
            calls:     Optional call edge dicts [{caller_id, callee_id}]
            framework: Target framework key

        Returns:
            List of ExtractionResult, one per function
        """
        # Build shared context
        # known_screens: names of functions confirmed as screens so that
        # _extract_render_edges can skip Screen→Screen RENDER edges.
        known_screens = frozenset(
            f["name"] for f in functions
            if f.get("react_role") == "screen" and f.get("name")
        )
        ctx = FrameworkContext(framework=framework, known_screens=known_screens)

        # Build call graph from calls list
        if calls:
            for c in calls:
                caller = c.get("caller_id") or ""
                callee = c.get("callee_id") or ""
                if caller and callee:
                    ctx.call_graph.setdefault(caller, []).append(callee)

        # Build component hierarchy from react_role + renders (if available)
        for func in functions:
            fid = func.get("symbol_id") or ""
            react_role = func.get("react_role") or ""
            if react_role in ("screen", "component", "hook"):
                ctx.component_hierarchy.setdefault(fid, "")

        results: List[ExtractionResult] = []
        for func in functions:
            results.append(self.extract(func, ctx))
        return results

    # ── Private ───────────────────────────────────────────────────────────────

    def _extract_safe(
        self,
        func: Dict[str, Any],
        ctx: FrameworkContext,
        symbol_id: str,
    ) -> ExtractionResult:
        framework = ctx.framework

        # ── Phase 2: Framework semantic mapping ──────────────────────────────
        fw_signals = _map_framework_signals(func, framework)

        # Always include CALLS intent if the function has any calls
        code = func.get("code") or ""
        if code and _RE_CALLS_GENERIC.search(code):
            fw_signals.append(SemanticSignal(
                phase="framework_map",
                intent=INTENT_FUNCTION_CALL,
                confidence=0.55,
                evidence="call_expression_detected",
            ))

        # ── Phase 3: Intent detection ─────────────────────────────────────────
        existing_intent = func.get("intent") or ""
        ranked_intents = _detect_intents(fw_signals, existing_intent)
        detected_intent_names = [i for i, _ in ranked_intents]

        # ── Phase 4: Relationship mapping via rules ───────────────────────────
        all_edges: List[RelationshipEdge] = []
        for intent, confidence in ranked_intents:
            for rule in self._rules:
                if rule.intent != intent:
                    continue
                if confidence < rule.min_confidence:
                    continue
                if rule.frameworks and framework not in rule.frameworks:
                    continue
                try:
                    edges = rule.extractor(func, ctx, fw_signals)
                    all_edges.extend(edges)
                except Exception:
                    # Isolate per-rule failures
                    pass

        # Deduplicate edges by (source, target_name, rel_type)
        all_edges = _dedup_edges(all_edges)

        # ── Phase 5: Contextual reasoning ─────────────────────────────────────
        all_edges = _apply_contextual_reasoning(all_edges, func, ctx)

        return ExtractionResult(
            symbol_id=symbol_id,
            relationships=all_edges,
            signals=fw_signals,
            framework=framework,
            detected_intents=detected_intent_names,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _dedup_edges(edges: List[RelationshipEdge]) -> List[RelationshipEdge]:
    """Deduplicate edges by (source_id, target_name or target_id, rel_type).
    When duplicates exist, keep the highest-confidence version."""
    seen: Dict[Tuple, RelationshipEdge] = {}
    for edge in edges:
        key = (edge.source_id, edge.target_name or edge.target_id, edge.rel_type)
        existing = seen.get(key)
        if existing is None or edge.confidence > existing.confidence:
            seen[key] = edge
    return list(seen.values())


# ─────────────────────────────────────────────────────────────────────────────
# Module-level singleton (mirrors SemanticInferenceEngine pattern)
# ─────────────────────────────────────────────────────────────────────────────

_default_extractor: Optional[FrontendRelationshipExtractor] = None


def get_extractor() -> FrontendRelationshipExtractor:
    """Return the module-level singleton extractor (lazy init)."""
    global _default_extractor
    if _default_extractor is None:
        _default_extractor = FrontendRelationshipExtractor()
    return _default_extractor
