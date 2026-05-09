"""URL normalization utilities for API contract matching.

Converts raw URL expressions found in frontend source code into normalised
path patterns that can be matched against backend ApiEndpoint paths.

Canonical form: ``/api/users/:id`` — forward-slash-prefixed, colon-style
path parameters, no query string, no trailing slash (except root "/").

Examples
--------
>>> normalize_url_pattern("/api/users/${id}")
'/api/users/:id'
>>> normalize_url_pattern("`/posts/${postId}/comments/${commentId}`")
'/posts/:postId/comments/:commentId'
>>> normalize_url_pattern('"/api/orders/" + orderId')
'/api/orders/:orderId'
>>> normalize_url_pattern("/api/data?page=1&limit=20")
'/api/data'
>>> merge_base_url("/api", "/users/:id")
'/api/users/:id'
"""

from __future__ import annotations

import re
from typing import Optional


# ─── Regex patterns ───────────────────────────────────────────────────────────

# Template literal interpolation: ${varName}  or  ${expr.field}
_RE_TEMPLATE_EXPR = re.compile(r'\$\{[^}]+\}')

# String concatenation with a variable: "/path/" + varName
_RE_CONCAT_VAR = re.compile(r'"\s*\+\s*[A-Za-z_$][A-Za-z0-9_$.]*')
_RE_CONCAT_VAR_LEAD = re.compile(r'[A-Za-z_$][A-Za-z0-9_$.]*\s*\+\s*"')

# Backtick or quote stripping
_RE_QUOTES = re.compile(r'^[`\'"\s]+|[`\'"\s]+$')

# Query string
_RE_QUERY_STRING = re.compile(r'\?.*$')

# Multiple consecutive slashes → single slash
_RE_MULTI_SLASH = re.compile(r'/+')

# Express-style params: :paramName  (leave as-is)
_RE_COLON_PARAM = re.compile(r':[A-Za-z_][A-Za-z0-9_]*')

# Dynamic segments already in curly-brace form: {id}  (OpenAPI / .NET style)
_RE_CURLY_PARAM = re.compile(r'\{[^}]+\}')

# Variable name captured from `${varName}` or `someVar`
_RE_IDENTIFIER = re.compile(r'^[A-Za-z_$][A-Za-z0-9_$.]*$')


def _strip_quotes(raw: str) -> str:
    """Remove wrapping backticks, single-quotes, double-quotes and whitespace."""
    return _RE_QUOTES.sub('', raw)


def _extract_var_name(expr: str) -> str:
    """Extract a short readable name from a template expression.

    ``${user.id}``   → ``userId``
    ``${orderId}``   → ``orderId``
    ``${index + 1}`` → ``id``  (fallback generic)
    """
    inner = expr.strip('${}').strip()
    # Simple identifier or dotted access
    # e.g. "user.id" → "userId",  "orderId" → "orderId"
    parts = [p for p in re.split(r'[^a-zA-Z0-9_$]', inner) if p and p[0].isalpha()]
    if not parts:
        return 'id'
    # Take last meaningful segment, e.g. "params.userId" → "userId"
    last = parts[-1]
    # Drop common generic suffixes that duplicate the param name
    return last


def normalize_url_pattern(raw: str) -> Optional[str]:
    """Normalise a raw URL expression from source code to a canonical path pattern.

    Returns ``None`` if the expression does not look like an HTTP path (e.g. it
    is a full external URL like ``https://…``).

    Normalisation steps:
    1. Strip wrapping quotes / backticks.
    2. Replace template-literal interpolations ``${expr}`` with ``:name``.
    3. Replace string-concat variable fragments with ``:name``.
    4. Strip query string.
    5. Normalise ``{param}`` (OpenAPI) → ``:param`` (Express).
    6. Collapse duplicate slashes.
    7. Ensure leading slash; remove trailing slash (except root "/").
    """
    if not raw:
        return None

    s = raw.strip()

    # Drop full external URLs — we only care about relative paths
    if re.match(r'https?://', s, re.IGNORECASE):
        return None

    # Strip wrapping quotes/backticks
    s = _strip_quotes(s)

    if not s:
        return None

    # Strip backtick-wrapped (in case _strip_quotes missed them)
    s = s.strip('`').strip()

    # Replace ${expr} template interpolations
    def _replace_template(m: re.Match) -> str:
        name = _extract_var_name(m.group(0))
        return f':{name}'

    s = _RE_TEMPLATE_EXPR.sub(_replace_template, s)

    # Handle string concat: "/api/users/" + someId  →  /api/users/:someId
    # Pattern: ends with  " + varName  or  " + varName + "
    # We look for  "  +  identifier  after  a path segment
    def _replace_concat(m: re.Match) -> str:
        # Extract the variable name from  `" + varName`
        var_part = m.group(0).split('+')[-1].strip().strip('"')
        name = var_part.strip() if _RE_IDENTIFIER.match(var_part.strip()) else 'id'
        return f':{name}'

    s = re.sub(r'"\s*\+\s*([A-Za-z_$][A-Za-z0-9_$.]*)', lambda m: f':{m.group(1)}', s)
    # Leading variable + "/…"
    s = re.sub(r'([A-Za-z_$][A-Za-z0-9_$.]*)\s*\+\s*"', '', s)

    # Strip query string
    s = _RE_QUERY_STRING.sub('', s)

    # {param} → :param  (OpenAPI / .NET style)
    s = _RE_CURLY_PARAM.sub(lambda m: f':{m.group(0)[1:-1]}', s)

    # Collapse duplicate slashes
    s = _RE_MULTI_SLASH.sub('/', s)

    # Ensure leading slash
    if s and not s.startswith('/'):
        s = '/' + s

    # Remove trailing slash (except root "/")
    if len(s) > 1 and s.endswith('/'):
        s = s.rstrip('/')

    # If nothing meaningful remains
    if not s or s in ('/', ''):
        return None

    return s


def merge_base_url(base: Optional[str], path: Optional[str]) -> Optional[str]:
    """Merge a baseURL and a relative path into a single canonical pattern.

    Both inputs are normalised before merging.

    Examples
    --------
    >>> merge_base_url("/api", "/users/:id")
    '/api/users/:id'
    >>> merge_base_url("http://localhost:3000/api", "/orders")
    '/api/orders'
    >>> merge_base_url(None, "/users")
    '/users'
    """
    if not path:
        return None

    norm_path = normalize_url_pattern(path)
    if base is None:
        return norm_path

    # Strip scheme+host from base if present (keep only path)
    base_clean = re.sub(r'^https?://[^/]+', '', base)
    norm_base = normalize_url_pattern(base_clean)

    if not norm_base or norm_base == '/':
        return norm_path

    if norm_path is None:
        return norm_base

    # Avoid double segments
    merged = norm_base.rstrip('/') + '/' + norm_path.lstrip('/')
    return _RE_MULTI_SLASH.sub('/', merged)


def path_match_score(fe_pattern: str, be_pattern: str) -> float:
    """Score how well a frontend URL pattern matches a backend endpoint path.

    Returns a float in [0.0, 1.0].

    Scoring tiers
    -------------
    1.0  — Exact string match after normalisation
    0.85 — Segment-count match + all static segments identical (dynamic differ)
    0.6  — One of the patterns is a prefix of the other (versioned paths, etc.)
    0.0  — No meaningful overlap
    """
    if not fe_pattern or not be_pattern:
        return 0.0

    # Normalise both sides (strip trailing slashes, lowercase)
    a = fe_pattern.lower().rstrip('/')
    b = be_pattern.lower().rstrip('/')

    if a == b:
        return 1.0

    a_segs = [s for s in a.split('/') if s]
    b_segs = [s for s in b.split('/') if s]

    if len(a_segs) != len(b_segs):
        # Allow prefix match
        min_len = min(len(a_segs), len(b_segs))
        if a_segs[:min_len] == b_segs[:min_len]:
            return 0.6
        return 0.0

    # Same segment count — compare each segment
    matches = 0
    for sa, sb in zip(a_segs, b_segs):
        # Both dynamic: counts as match
        is_a_dyn = sa.startswith(':') or sa == '*'
        is_b_dyn = sb.startswith(':') or sb == '*'
        if sa == sb or is_a_dyn or is_b_dyn:
            matches += 1

    score = matches / len(a_segs)
    if score == 1.0:
        return 0.85  # structural match, not literal
    return round(score * 0.7, 2)


def normalize_http_method(method: Optional[str]) -> str:
    """Normalise an HTTP method string to uppercase, defaulting to 'GET'."""
    if not method:
        return 'GET'
    m = method.strip().upper()
    return m if m in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS', 'ALL') else 'GET'
