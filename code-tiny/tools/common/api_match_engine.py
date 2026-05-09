"""Advanced Multi-Signal API Contract Matching Engine — V2.0

Architecture
────────────
Current V1 dùng đúng 2 tín hiệu: path structure + HTTP method.
V2 combine 5 independent signals thành một ensemble score có trọng số:

    Signal 1 — Path Structure      weight=0.45  (dominant gate)
    Signal 2 — HTTP Method         weight=0.10  (hard gate + soft boost)
    Signal 3 — Name Token Overlap  weight=0.20  (verb-group normalised)
    Signal 4 — Module Context      weight=0.15  (domain term extraction)
    Signal 5 — Path Var Semantics  weight=0.10  (parameter name compat)
                                   ─────────────
    Raw score ∈ [0, 1.0]           sum = 1.00

Final confidence = raw_score (already calibrated to [0, 1])

PathIndex
─────────
Inverted static-segment index for O(k) candidate retrieval.

Given FE call  /api/users/:id  →  static = {api, users}
- Find all BE endpoints where ALL of {api, users} appear as static segments
- O(1) set intersection vs O(n) linear scan
- Eliminates 80-95% of candidates before scoring

Enriched Data Models
────────────────────
ApiCallEnriched   — adds function_name, function_intent, file_path
ApiEndpointEnriched — adds handler_names, controller_class, file_path

These are populated from Neo4j joins before scoring.

Signal 3 Details (Name Token Overlap)
──────────────────────────────────────
  FE function:   fetchUserProfile  → tokens: [fetch, user, profile]
  BE handler:    getUserById       → tokens: [get,   user, by, id]

  Verb normalisation via _VERB_GROUPS:
    fetch → get,  load → get,  create → post,  insert → post, …
  Normalised FE: [get, user, profile]
  Normalised BE: [get, user, by, id]
  Jaccard({get,user,profile}, {get,user,by,id}) = |{get,user}| / |{get,user,profile,by,id}| = 0.40

  Stop-word removal prevents false boosts from generic tokens.

Signal 4 Details (Module Context)
──────────────────────────────────
  FE file:  src/api/userApi.ts          → domain tokens: {user}
  BE file:  src/controllers/user.controller.ts → domain tokens: {user}
  FE controller (if known): UserController   → domain tokens: {user}
  Jaccard({user}, {user}) = 1.0

Signal 5 Details (Path Var Semantics)
──────────────────────────────────────
  /api/users/:id        vs  /api/users/:userId
  Stems: "" id → ""  vs  "user" id → "user"
  Different stems → score 0.5  (penalize but don't reject)

  /api/orders/:orderId  vs  /api/orders/:id
  Stems: "order"        vs  ""
  One contains the other → score 0.8

Explainability
──────────────
Every MatchResult carries signal_scores dict:
  { "path": 0.85, "method": 0.10, "name": 0.40, "module": 1.0, "pathvar": 0.8 }
This is stored on the MATCHES relationship in Neo4j for debugging.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

# ─── Import path normaliser ───────────────────────────────────────────────────
import os, sys
_ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from tools.common.url_normalizer import path_match_score, normalize_http_method


# ─────────────────────────────────────────────────────────────────────────────
# Verb normalisation groups
# ─────────────────────────────────────────────────────────────────────────────

# Maps every synonym → canonical verb bucket.
# This lets  fetchUser ↔ getUserById  both resolve to "get + user".
_VERB_GROUPS: Dict[str, str] = {}
for _canon, _synonyms in {
    "get":    {"get", "fetch", "load", "retrieve", "read", "find", "query",
               "list", "search", "show", "display", "obtain", "lookup"},
    "create": {"create", "add", "insert", "save", "post", "register",
               "submit", "new", "make", "build", "generate", "produce"},
    "update": {"update", "put", "patch", "modify", "edit", "change",
               "set", "replace", "alter", "amend"},
    "delete": {"delete", "remove", "destroy", "drop", "clear", "cancel",
               "deactivate", "disable", "purge"},
    "auth":   {"login", "logout", "authenticate", "authorize", "refresh",
               "token", "signin", "signout", "signup", "register"},
}.items():
    for _s in _synonyms:
        _VERB_GROUPS[_s] = _canon

# Tokens that carry no domain meaning — ignored in signal 3 and 4
_STOP_TOKENS: Set[str] = {
    "by", "of", "the", "a", "an", "in", "for", "to", "and", "or", "with",
    "id", "ids", "all", "data", "info", "item", "items", "request", "response",
    "dto", "model", "entity", "object", "result", "payload", "body", "params",
    "param", "type", "types", "base", "default", "current",
}

# Path segment names that are structural (not domain-specific)
_GENERIC_PATH_TOKENS: Set[str] = {
    "src", "app", "module", "modules", "api", "apis", "service", "services",
    "controller", "controllers", "handler", "handlers", "route", "routes",
    "middleware", "index", "common", "shared", "utils", "util", "helpers",
    "helper", "lib", "libs", "v1", "v2", "v3", "v4", "core", "main",
    "internal", "external", "public", "private",
}


# ─────────────────────────────────────────────────────────────────────────────
# Token utilities
# ─────────────────────────────────────────────────────────────────────────────

def _tokenize(name: str) -> List[str]:
    """Split camelCase / PascalCase / snake_case / kebab-case into lowercase tokens.

    Examples
    --------
    >>> _tokenize("fetchUserProfile")
    ['fetch', 'user', 'profile']
    >>> _tokenize("get-orders-by-id")
    ['get', 'orders', 'by', 'id']
    >>> _tokenize("UserController")
    ['user', 'controller']
    """
    # Insert space before uppercase letter that follows a lowercase letter
    name = re.sub(r'([a-z])([A-Z])', r'\1 \2', name)
    # Insert space before uppercase sequence followed by lowercase (e.g. "getUserID" → "get user ID")
    name = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1 \2', name)
    # Replace non-alphanumeric with space
    name = re.sub(r'[^a-zA-Z0-9]', ' ', name)
    return [t.lower() for t in name.split() if len(t) > 1]


def _normalize_token(tok: str) -> str:
    """Map token to its canonical verb bucket, or return it unchanged."""
    return _VERB_GROUPS.get(tok, tok)


def _domain_tokens(name: str, extra: str = "") -> Set[str]:
    """Extract meaningful domain tokens from a name/path string.

    Filters out generic API structure tokens and stop words.
    """
    parts = re.split(r'[/\\\-_.]', name.replace('\\', '/').lower())
    tokens: Set[str] = set()
    for part in parts:
        # Remove file extension
        part = re.sub(r'\.[a-z]+$', '', part)
        for tok in _tokenize(part):
            if (
                len(tok) > 2
                and tok not in _STOP_TOKENS
                and tok not in _GENERIC_PATH_TOKENS
            ):
                tokens.add(tok)
    if extra:
        for tok in _tokenize(extra):
            if len(tok) > 2 and tok not in _STOP_TOKENS and tok not in _GENERIC_PATH_TOKENS:
                tokens.add(tok)
    return tokens


def _token_jaccard(a: Set[str], b: Set[str]) -> float:
    """Jaccard similarity of two sets, after verb-group normalisation."""
    if not a or not b:
        return 0.0
    na = {_normalize_token(t) for t in a}
    nb = {_normalize_token(t) for t in b}
    inter = na & nb
    union = na | nb
    # Remove stop tokens from union denominator to avoid dilution
    union -= _STOP_TOKENS
    if not union:
        return 0.0
    return len(inter - _STOP_TOKENS) / len(union)


# ─────────────────────────────────────────────────────────────────────────────
# Enriched data models
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ApiCallEnriched:
    """ApiCall node enriched with caller Function metadata."""
    symbol_id: str
    url_pattern: str                      # normalised: /api/users/:id
    http_method: str                      # GET | POST | …
    project_id: str
    file_path: str = ""                   # src/api/userApi.ts
    function_name: str = ""              # fetchUser
    function_intent: str = ""            # "retrieve user data by id"
    react_role: str = ""                 # "middleware" | "hook" | …


@dataclass
class ApiEndpointEnriched:
    """ApiEndpoint node enriched with handler/controller metadata."""
    symbol_id: str
    path: str                             # /api/users/:id
    http_method: str                      # GET | POST | …
    project_id: str
    file_path: str = ""                   # src/controllers/user.controller.ts
    handler_names: List[str] = field(default_factory=list)   # ["getUser"]
    controller_class: str = ""            # "UserController"
    framework: str = ""                   # "nestjs" | "express"


@dataclass
class SignalScores:
    """Per-signal breakdown for a single (ApiCall, ApiEndpoint) pair."""
    path: float = 0.0       # 0–1  path structure
    method: float = 0.0     # 0–0.10  method exact/any/mismatch
    name: float = 0.0       # 0–1  token overlap
    module: float = 0.0     # 0–1  file/module context
    pathvar: float = 0.0    # 0–1  path variable semantics
    raw: float = 0.0        # weighted sum
    confidence: float = 0.0 # final calibrated confidence

    def to_dict(self) -> Dict[str, float]:
        return {
            "path":       round(self.path, 3),
            "method":     round(self.method, 3),
            "name":       round(self.name, 3),
            "module":     round(self.module, 3),
            "pathvar":    round(self.pathvar, 3),
            "raw":        round(self.raw, 3),
            "confidence": round(self.confidence, 3),
        }


@dataclass
class MatchResult:
    """Result of matching one ApiCall to one ApiEndpoint."""
    ac_id: str
    ep_id: str
    confidence: float
    match_type: str                        # "exact"|"strong"|"structural"|"weak"
    fe_project: str
    be_project: str
    signals: SignalScores = field(default_factory=SignalScores)


# ─────────────────────────────────────────────────────────────────────────────
# Signal 5 — Path variable semantic compatibility
# ─────────────────────────────────────────────────────────────────────────────

def _pathvar_compat_score(fe_url: str, be_path: str) -> float:
    """Score the semantic compatibility of dynamic path variable names.

    Returns a value in [0.0, 1.0]:
      1.0  — same variable stem  (:id ↔ :id,  :userId ↔ :userId)
      0.8  — one stem contains the other (:orderId ↔ :id)
      0.5  — both dynamic but unrelated stems (:slug ↔ :id)
      0.0  — one static, one dynamic (structural mismatch at this segment)

    Returns 0.5 (neutral) when there are no dynamic segments.
    """
    fe_segs = [s for s in fe_url.strip('/').split('/') if s]
    be_segs = [s for s in be_path.strip('/').split('/') if s]

    if len(fe_segs) != len(be_segs):
        return 0.5  # defer to path_match_score for length differences

    var_count = 0
    compat_sum = 0.0

    for fs, bs in zip(fe_segs, be_segs):
        fe_dyn = fs.startswith(':') or ('{' in fs and '}' in fs)
        be_dyn = bs.startswith(':') or bs == '*'

        if fe_dyn and be_dyn:
            var_count += 1
            # Extract variable name, strip leading colon/brace
            fv = re.sub(r'^[:${]+|[}]+$', '', fs).lower().strip()
            bv = re.sub(r'^[:${]+|[}]+$', '', bs).lower().strip()

            # Stem: strip trailing "Id" / "ID" suffix
            fv_stem = re.sub(r'ids?$', '', fv) or fv
            bv_stem = re.sub(r'ids?$', '', bv) or bv

            if fv == bv:
                compat_sum += 1.0
            elif fv_stem == bv_stem:
                compat_sum += 0.95
            elif fv_stem in bv_stem or bv_stem in fv_stem:
                compat_sum += 0.80
            else:
                compat_sum += 0.40  # both dynamic but unrelated domain
        elif fe_dyn != be_dyn:
            # One is a real path segment, the other is a parameter: structural mismatch
            var_count += 1
            compat_sum += 0.0

    if var_count == 0:
        return 0.5  # no dynamic segments → neutral
    return compat_sum / var_count


# ─────────────────────────────────────────────────────────────────────────────
# PathIndex — O(k) candidate retrieval
# ─────────────────────────────────────────────────────────────────────────────

class PathIndex:
    """Inverted static-segment index for fast candidate endpoint retrieval.

    Intuition
    ─────────
    A FE call to  /api/users/:id  can only match a BE endpoint that
    also contains the static segments  "api"  and  "users".

    We build a dict:  static_segment → {endpoint_symbol_ids},
    then intersect sets: endpoints_with("api") ∩ endpoints_with("users").

    This prunes 80-95% of candidates before the expensive signal scoring.
    Fallback: if intersection is empty (rare: e.g. all-dynamic paths), use
    segment-count-based candidates instead.
    """

    def __init__(self, endpoints: List[ApiEndpointEnriched]) -> None:
        # symbol_id → endpoint (fast lookup)
        self._ep_map: Dict[str, ApiEndpointEnriched] = {ep.symbol_id: ep for ep in endpoints}
        # static segment token → set of endpoint symbol_ids
        self._seg_idx: Dict[str, Set[str]] = defaultdict(set)
        # segment count → set of endpoint symbol_ids
        self._count_idx: Dict[int, Set[str]] = defaultdict(set)

        for ep in endpoints:
            segs = [s for s in ep.path.lstrip('/').split('/') if s]
            n = len(segs)
            self._count_idx[n].add(ep.symbol_id)
            for s in segs:
                if not s.startswith(':') and s != '*':
                    self._seg_idx[s].add(ep.symbol_id)

    def candidates(self, call: ApiCallEnriched) -> List[ApiEndpointEnriched]:
        """Return a short list of plausible endpoint candidates for *call*.

        Uses set-intersection on static path segments → typically ≤ 5 results.
        """
        segs = [s for s in call.url_pattern.lstrip('/').split('/') if s]
        static_segs = [s for s in segs if not s.startswith(':') and s != '*']
        n = len(segs)

        if static_segs:
            # Intersect: only endpoints that contain ALL static segments
            candidate_ids: Set[str] = self._seg_idx.get(static_segs[0], set()).copy()
            for seg in static_segs[1:]:
                candidate_ids &= self._seg_idx.get(seg, set())

            if candidate_ids:
                return [self._ep_map[sid] for sid in candidate_ids if sid in self._ep_map]

        # Fallback: same or ±1 segment count (handles prefix/suffix mismatches)
        ids: Set[str] = set()
        for delta in (0, -1, 1):
            ids |= self._count_idx.get(n + delta, set())
        return [self._ep_map[sid] for sid in ids if sid in self._ep_map]

    def __len__(self) -> int:
        return len(self._ep_map)


# ─────────────────────────────────────────────────────────────────────────────
# Multi-Signal Scorer
# ─────────────────────────────────────────────────────────────────────────────

# Signal weights (must sum to ≤ 1.0)
_W_PATH    = 0.45
_W_METHOD  = 0.10
_W_NAME    = 0.20
_W_MODULE  = 0.15
_W_PATHVAR = 0.10


def _confidence_tier(raw: float) -> str:
    """Classify raw score into a human-readable match quality tier."""
    if raw >= 0.90:
        return "exact"
    if raw >= 0.72:
        return "strong"
    if raw >= 0.52:
        return "structural"
    return "weak"


def score_pair(
    call: ApiCallEnriched,
    ep: ApiEndpointEnriched,
) -> Optional[SignalScores]:
    """Compute full multi-signal score for one (call, endpoint) pair.

    Returns ``None`` when the method is incompatible (hard gate).
    The returned ``SignalScores.confidence`` is the final calibrated value.
    """
    cm = call.http_method.upper()
    em = ep.http_method.upper()

    # ── Signal 2: HTTP method (hard gate) ─────────────────────────────────────
    if cm not in ("", "ALL") and em not in ("", "ALL") and cm != em:
        return None  # incompatible methods → hard reject
    method_exact = (cm == em) and cm not in ("", "ALL")
    s_method = _W_METHOD if method_exact else (_W_METHOD * 0.5)

    # ── Signal 1: Path structure ───────────────────────────────────────────────
    s_path_raw = path_match_score(call.url_pattern, ep.path)
    if s_path_raw <= 0.0:
        return None  # no path overlap → hard reject
    s_path = s_path_raw * _W_PATH

    # ── Signal 3: Name token overlap ──────────────────────────────────────────
    call_tokens = set(_tokenize(call.function_name))
    if call.function_intent:
        call_tokens |= set(_tokenize(call.function_intent))

    ep_tokens: Set[str] = set()
    for h in ep.handler_names:
        ep_tokens |= set(_tokenize(h))
    if ep.controller_class:
        ep_tokens |= set(_tokenize(ep.controller_class))

    # Remove stop tokens before Jaccard
    call_tokens -= _STOP_TOKENS
    ep_tokens -= _STOP_TOKENS

    name_j = _token_jaccard(call_tokens, ep_tokens) if (call_tokens and ep_tokens) else 0.0
    s_name = name_j * _W_NAME

    # ── Signal 4: Module / file context ───────────────────────────────────────
    fe_domain = _domain_tokens(call.file_path, call.function_name)
    be_domain = _domain_tokens(ep.file_path, ep.controller_class)
    module_j = _token_jaccard(fe_domain, be_domain) if (fe_domain and be_domain) else 0.0
    s_module = module_j * _W_MODULE

    # ── Signal 5: Path variable semantics ─────────────────────────────────────
    pv_raw = _pathvar_compat_score(call.url_pattern, ep.path)
    s_pathvar = pv_raw * _W_PATHVAR

    # ── Ensemble raw score ─────────────────────────────────────────────────────
    raw = s_path + s_method + s_name + s_module + s_pathvar

    # Certainty floor: when path is an exact structural match (1.0) AND method
    # is exact, the core deterministic signals already identify the endpoint
    # unambiguously.  Absent soft-signal metadata (no handler names, no module
    # overlap) must not push a clearly correct match below the "exact" tier.
    if s_path_raw == 1.0 and method_exact:
        raw = max(raw, 0.90)

    scores = SignalScores(
        path=s_path_raw,
        method=s_method / _W_METHOD,       # normalise back to 0–1 for readability
        name=name_j,
        module=module_j,
        pathvar=pv_raw,
        raw=round(raw, 4),
        confidence=round(raw, 4),          # already in [0, 1]
    )
    return scores


# ─────────────────────────────────────────────────────────────────────────────
# Main matcher
# ─────────────────────────────────────────────────────────────────────────────

class MultiSignalMatcher:
    """Orchestrates PathIndex + SignalEngine to match FE calls → BE endpoints.

    Usage
    ─────
        matcher = MultiSignalMatcher(api_endpoints)
        results = matcher.match_all(api_calls, fe_project_id, be_project_id)

    For each call, it:
    1. Retrieves O(k) candidates from PathIndex (fast, static-segment intersection)
    2. Scores each candidate with all 5 signals
    3. Keeps the best match per call (max confidence)
    4. Filters by min_confidence threshold
    """

    def __init__(
        self,
        endpoints: List[ApiEndpointEnriched],
        min_confidence: float = 0.50,
    ) -> None:
        self._endpoints = endpoints
        self._index = PathIndex(endpoints)
        self._min_confidence = min_confidence

    def match_all(
        self,
        calls: List[ApiCallEnriched],
        fe_project_id: str,
        be_project_id: str,
    ) -> List[MatchResult]:
        """Return the best MatchResult for every qualifying ApiCall."""
        results: List[MatchResult] = []

        for call in calls:
            candidates = self._index.candidates(call)
            best: Optional[Tuple[float, SignalScores, ApiEndpointEnriched]] = None

            for ep in candidates:
                scores = score_pair(call, ep)
                if scores is None:
                    continue
                if scores.confidence < self._min_confidence:
                    continue
                if best is None or scores.confidence > best[0]:
                    best = (scores.confidence, scores, ep)

            if best is not None:
                conf, scores, ep = best
                results.append(MatchResult(
                    ac_id=call.symbol_id,
                    ep_id=ep.symbol_id,
                    confidence=conf,
                    match_type=_confidence_tier(conf),
                    fe_project=fe_project_id,
                    be_project=be_project_id,
                    signals=scores,
                ))

        return results
