"""
Call graph index and usage-context extraction.

Usage signal is computed by analyzing *how callers use* the return value
of a function:

  Pattern            Intent hint       Signal score
  ─────────────────────────────────────────────────
  assignment         retrieval         0.90
  return value       retrieval         0.85
  condition (if)     predicate         0.90
  ternary condition  predicate         0.85
  await only         io_read           0.75
  void call          side_effect       0.70

Multiple call-sites are aggregated: the dominant pattern wins.
If there are no callers the usage signal is 0.0 (neutral).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Call patterns — compiled once
# ---------------------------------------------------------------------------

# Matches: const/let/var x = [await] ...callee(   OR   return [await] ...callee(
_RE_ASSIGN = re.compile(
    r"""(?x)
    (?:
        (?:const|let|var)\s+\w+\s*(?::\s*\S+)?\s*=\s*|   # const x =
        return\s+                                          # return
    )
    (?:await\s+)?
    (?:\w+\.)*                                            # optional chain
    {name}                                                # function name
    \s*[(<]                                               # call site
    """
)

# Matches: if ([!] ...callee(   or  ternary: ...callee(...) ?
_RE_COND = re.compile(
    r"""(?x)
    (?:
        if\s*\(\s*(?:!?\s*)?     |   # if (  or  if (!
        \|\|\s*(?:!?\s*)?        |   # ||
        &&\s*(?:!?\s*)?          |   # &&
        \?\s*\S+\s*:\s*          |   # ternary rhs
        while\s*\(\s*(?:!?\s*)?      # while (
    )
    (?:\w+\.)*
    {name}
    \s*[(<]
    """
)

# Matches: result = await callee(  or  await callee(
_RE_AWAIT = re.compile(
    r"await\s+(?:\w+\.)*{name}\s*[(<]"
)

# Matches: standalone call — line begins with optional 'await ' then callee(
# (Not preceded by = or return, not inside if/while)
_RE_STANDALONE = re.compile(
    r"(?m)^\s*(?:await\s+)?(?:\w+\.)*{name}\s*[(<]"
)


@dataclass
class CallSiteContext:
    """Context of a single call site."""
    pattern: str   # "assignment" | "condition" | "await" | "standalone"
    caller_id: str


@dataclass
class FunctionUsageIndex:
    """Pre-built index for fast usage-signal lookup."""
    # func_id → list of call site contexts
    by_id:   Dict[str, List[CallSiteContext]] = field(default_factory=dict)
    # func_name → list of call site contexts (fallback when id not resolved)
    by_name: Dict[str, List[CallSiteContext]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Intent + score per dominant pattern
# ---------------------------------------------------------------------------

_PATTERN_INTENT: Dict[str, Tuple[str, float]] = {
    "assignment": ("retrieval",    0.90),
    "condition":  ("predicate",    0.90),
    "await":      ("io_read",      0.75),
    "standalone": ("side_effect",  0.70),
}


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def build_usage_index(
    functions: List[Dict[str, Any]],
    calls: List[Dict[str, Any]],
) -> FunctionUsageIndex:
    """
    Build a reverse call-site index from parsed function + call data.

    Args:
        functions: List of function dicts (from parse payload).
        calls:     List of call-edge dicts (caller_id, callee_name, callee_id).

    Returns:
        FunctionUsageIndex ready for ``get_usage_signal``.
    """
    func_by_id: Dict[str, Dict[str, Any]] = {
        f["symbol_id"]: f for f in functions if f.get("symbol_id")
    }

    index = FunctionUsageIndex()

    for call in calls:
        caller_id  = call.get("caller_id") or ""
        callee_id  = call.get("callee_id") or ""
        callee_name = call.get("callee_name") or ""

        if not caller_id or not callee_name:
            continue

        caller_func = func_by_id.get(caller_id)
        if caller_func is None:
            continue

        caller_code = caller_func.get("code") or ""
        if not caller_code:
            continue

        ctx = _extract_call_context(callee_name, caller_code, caller_id)

        # Index by resolved id (precise) and by name (fallback)
        if callee_id:
            index.by_id.setdefault(callee_id, []).append(ctx)
        index.by_name.setdefault(callee_name, []).append(ctx)

    return index


def get_usage_signal(
    func_id: str,
    func_name: str,
    index: FunctionUsageIndex,
) -> Tuple[Optional[str], float]:
    """
    Compute usage signal for a function.

    Returns:
        (intent_hint, confidence)  — intent_hint may be None if no data.
                                     confidence is 0.0 if no callers found.
    """
    contexts = index.by_id.get(func_id) or index.by_name.get(func_name) or []

    if not contexts:
        return None, 0.0

    # Vote by pattern
    votes: Dict[str, int] = {}
    for ctx in contexts:
        votes[ctx.pattern] = votes.get(ctx.pattern, 0) + 1

    dominant = max(votes, key=lambda p: votes[p])
    total = len(contexts)
    vote_ratio = votes[dominant] / total  # 0.0 to 1.0

    intent_hint, base_score = _PATTERN_INTENT.get(dominant, (None, 0.0))

    # Scale by vote ratio — unanimous → full score
    confidence = round(base_score * vote_ratio, 3)

    # Require at least 2 call sites for medium confidence; 1 site → lower score
    if total == 1:
        confidence = round(confidence * 0.70, 3)

    return intent_hint, confidence


# ---------------------------------------------------------------------------
# Lightweight data-flow: argument type propagation
# ---------------------------------------------------------------------------


def infer_arg_types_from_callers(
    func_id: str,
    func_name: str,
    index: FunctionUsageIndex,
    func_by_id: Dict[str, Dict[str, Any]],
) -> List[str]:
    """
    Infer likely argument types by examining what callers pass to this function.

    Returns a list of type hints (strings) for each argument position.
    Only provides hints when patterns are clear; empty string = unknown.
    """
    contexts = index.by_id.get(func_id) or index.by_name.get(func_name) or []
    if not contexts:
        return []

    # Collect raw argument strings per position
    arg_slots: Dict[int, List[str]] = {}
    for ctx in contexts:
        caller_func = func_by_id.get(ctx.caller_id)
        if not caller_func:
            continue
        caller_code = caller_func.get("code") or ""
        arg_list = _extract_call_args(func_name, caller_code)
        for idx, arg in enumerate(arg_list):
            arg_slots.setdefault(idx, []).append(arg.strip())

    result: List[str] = []
    for idx in range(max(arg_slots.keys(), default=-1) + 1):
        args = arg_slots.get(idx, [])
        if not args:
            result.append("")
            continue
        # Very simple heuristic: look for known type names in arg text
        inferred = _guess_type_from_arg_tokens(args)
        result.append(inferred)

    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_call_context(
    callee_name: str,
    caller_code: str,
    caller_id: str,
) -> CallSiteContext:
    """Classify how `callee_name` is used within `caller_code`."""
    name = re.escape(callee_name)

    assign_re = re.compile(_RE_ASSIGN.pattern.replace("{name}", name), re.VERBOSE)
    cond_re   = re.compile(_RE_COND.pattern.replace("{name}", name), re.VERBOSE)
    await_re  = re.compile(_RE_AWAIT.pattern.replace("{name}", name))

    if cond_re.search(caller_code):
        return CallSiteContext("condition", caller_id)
    if assign_re.search(caller_code):
        return CallSiteContext("assignment", caller_id)
    if await_re.search(caller_code):
        return CallSiteContext("await", caller_id)
    return CallSiteContext("standalone", caller_id)


def _extract_call_args(callee_name: str, code: str) -> List[str]:
    """Extract raw argument strings from a call to `callee_name`."""
    name = re.escape(callee_name)
    pattern = re.compile(rf"(?:\w+\.)*{name}\s*\(([^)]*)\)")
    match = pattern.search(code)
    if not match:
        return []
    raw_args = match.group(1)
    if not raw_args.strip():
        return []
    return raw_args.split(",")


_KNOWN_TYPE_HINTS: Dict[str, str] = {
    "user":     "User",
    "id":       "string | number",
    "email":    "string",
    "password": "string",
    "token":    "string",
    "url":      "string",
    "path":     "string",
    "count":    "number",
    "index":    "number",
    "page":     "number",
    "limit":    "number",
    "offset":   "number",
    "event":    "Event",
    "request":  "Request",
    "response": "Response",
    "error":    "Error",
    "config":   "Config",
    "options":  "Options",
}


def _guess_type_from_arg_tokens(args: List[str]) -> str:
    """Return a rough type hint based on argument names."""
    for arg in args:
        lower = arg.lower().strip()
        for key, hint in _KNOWN_TYPE_HINTS.items():
            if key in lower:
                return hint
    return ""
