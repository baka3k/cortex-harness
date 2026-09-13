"""Provider-neutral query rewriting shared by concrete graph drivers.

Both FalkorDB and Ladybug reject the Neo4j 5 importing-variable subquery
form and have no zero-argument ``datetime()`` function, so drivers rewrite
those constructs before execution. The rewriters live here so the rules
cannot drift between backends; dialect-specific parameter substitution
(whether the timestamp needs a wrapper function) stays with each driver.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple


# Neo4j 5.x subquery-with-importing-variable: CALL (var) { ... }.
# FalkorDB only supports the older CALL { WITH var ... } form (variables
# imported via opening WITH clause). Rewrite the parenthesized form to the
# portable form so a single query works against both backends.
#
# Caveat: this regex matches the opening ``CALL (var) {`` and rewriter inserts
# ``WITH var`` right after the brace. Nested CALL subqueries inside the body
# are left alone — if a query has nested ``CALL (other) {`` braces inside the
# outer subquery, callers should write the query in the portable form.
_CALL_IMPORTING_SUBQUERY_RE = re.compile(
    r"CALL\s+\(([A-Za-z_][A-Za-z0-9_]*)\)\s*\{",
)
_MUTATING_CYPHER_RE = re.compile(
    r"\b(CREATE|MERGE|SET|DELETE|DETACH|REMOVE|DROP|ALTER|FOREACH|LOAD\s+CSV)\b",
    re.IGNORECASE,
)


def normalize_call_importing_subqueries(query: str) -> str:
    """Rewrite ``CALL (var) { ... }`` to portable ``CALL { WITH var ... }``.

    Index creation must go through ``driver.create_indexes()`` — never raw
    ``CREATE INDEX`` Cypher — so each backend uses its native API.
    """
    return _CALL_IMPORTING_SUBQUERY_RE.sub(r"CALL { WITH \1", query)


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def rewrite_datetime_call(
    query: str,
    parameters: Optional[Dict[str, Any]],
    *,
    replacement: str = "${param}",
    param_prefix: str = "__now",
) -> Tuple[str, Dict[str, Any]]:
    """Replace zero-argument ``datetime()`` with a bound ISO-8601 timestamp.

    ``replacement`` controls how the parameter is embedded: plain ``$param``
    for FalkorDB, ``timestamp($param)`` for Ladybug (whose ``timestamp``
    function parses ISO-8601 strings; a zero-argument ``datetime()`` does
    not exist there). The parameter name is collided against existing
    parameter names so rewrites never shadow caller data.
    """

    params = dict(parameters or {})
    if "datetime()" not in query:
        return query, params

    param_name = param_prefix
    while param_name in params:
        param_name = f"_{param_name}"

    rendered = replacement.replace("{param}", param_name)
    return query.replace("datetime()", rendered), {**params, param_name: utc_timestamp()}


def is_retryable_read(query: str) -> bool:
    """Retry only queries that are unambiguously read-only.

    A synchronous embedded call can fail after committing a mutation, so a
    blanket retry can duplicate ingestion effects.  Read retries remain useful
    for transient checkpoint contention.
    """
    first_token = query.lstrip().split(None, 1)[0].upper() if query.strip() else ""
    return first_token in {"MATCH", "OPTIONAL", "UNWIND", "WITH", "RETURN", "SHOW", "EXPLAIN", "PROFILE"} and not bool(
        _MUTATING_CYPHER_RE.search(query)
    )


def is_mutating(query: str) -> bool:
    """Return True when the query contains any mutation keyword."""

    return bool(_MUTATING_CYPHER_RE.search(query))


__all__ = [
    "is_mutating",
    "is_retryable_read",
    "normalize_call_importing_subqueries",
    "rewrite_datetime_call",
    "utc_timestamp",
]
