"""Shared ``repo`` scope response/param support for the MCP backends.

The Cypher predicate fragment and parameter preparation live in
``tools.common.project_scope`` (single source of query semantics); this
module adds the backend-facing response shaping that every MCP backend
needs around it: normalized scope state, the additive ``scope`` block for
``data``, the empty-result warning (with the DISTINCT-repo suggestion), and
the anchor post-filters for driver-based tools.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from tools.common.project_scope import matches_repo_scope

CypherRunner = Callable[[str, Dict[str, Any], List[str]], Awaitable[Tuple[str, List[Dict[str, Any]]]]]

REPO_VALUES_QUERY = (
    "MATCH (n) WHERE n.repo IS NOT NULL AND n.repo <> '' "
    "RETURN DISTINCT n.repo AS repo ORDER BY repo LIMIT 5"
)

# RR5 over-fetch factor: Qdrant `match` cannot express suffix matching, so
# scoped vector searches retrieve limit × factor and post-filter in Python.
REPO_POSTFETCH_FACTOR = 4


def repo_scope_state(repo: Optional[str]) -> Tuple[Optional[str], bool]:
    """Normalized repo value plus whether the scope filter is active (RR4)."""
    repo_value = str(repo).strip() if repo is not None else ""
    return (repo_value or None), bool(repo_value)


def repo_scope_block(
    repo_value: Optional[str],
    applied: bool,
    warnings: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Additive ``scope`` block inside ``data`` (envelope v2.0 untouched)."""
    return {
        "repo": repo_value,
        "filter_applied": bool(applied),
        "warnings": list(warnings or []),
    }


async def repo_scope_empty_warnings(
    runner: CypherRunner,
    repo_value: Optional[str],
    applied: bool,
    has_results: bool,
    db_candidates: List[str],
) -> List[str]:
    """Warning for a scoped query that matched nothing (RR2 follow-up).

    The auxiliary DISTINCT-repo query only runs on the empty branch so an
    unmatched repo value comes back with actionable alternatives instead of
    a silent zero.
    """
    if not applied or repo_value is None or has_results:
        return []
    try:
        _, records = await runner(REPO_VALUES_QUERY, {}, db_candidates)
    except Exception:  # noqa: BLE001 - warnings must never fail the query
        records = []
    known = [str(record.get("repo")) for record in records if record.get("repo")]
    if known:
        return [
            f"repo '{repo_value}' matched 0 nodes. Known repo values: {', '.join(known)}"
        ]
    return [f"repo '{repo_value}' matched 0 nodes"]


def raw_node_matches_repo(node: Any, repo_value: Optional[str]) -> bool:
    """Anchor post-filter for driver-returned node dicts (RR2/RR3)."""
    if repo_value is None:
        return True
    repo_prop = node.get("repo") if isinstance(node, dict) else None
    return matches_repo_scope(repo_prop, repo_value)


def filter_vector_hits_by_repo(
    items: List[Dict[str, Any]],
    repo_value: Optional[str],
    applied: bool,
) -> Tuple[List[Dict[str, Any]], int]:
    """RR5 post-filter over merged vector hits. Returns (kept, pre_hits).

    Each hit carries a Qdrant ``payload`` dict whose ``repo`` field holds the
    stored flavor (``primary_vector_sync``); the same exact-or-suffix rule as
    the graph predicate decides what stays. Unscoped queries pass through
    untouched (no behavior change).
    """
    source = list(items or [])
    if not applied:
        return source, len(source)
    kept = [
        item
        for item in source
        if matches_repo_scope(
            (item.get("payload") or {}).get("repo") if isinstance(item, dict) else None,
            repo_value,
        )
    ]
    return kept, len(source)


def vector_repo_scope_result(
    repo_value: Optional[str],
    applied: bool,
    pre_filter_hits: int,
    post_filter_hits: int,
    fetch_limit: int,
) -> Tuple[Dict[str, Any], List[str]]:
    """Vector-flavored ``scope`` block + warnings for one search pass (RR5)."""
    scope = {
        "repo": repo_value,
        "filter_applied": bool(applied),
        "pre_filter_hits": int(pre_filter_hits),
        "post_filter_hits": int(post_filter_hits),
    }
    warnings: List[str] = []
    # scope.warnings mirrors the top-level list (same rule as graph tools)
    scope["warnings"] = warnings
    if not applied:
        return scope, warnings
    if post_filter_hits == 0:
        warnings.append(
            f"repo '{repo_value}' matched 0 of {pre_filter_hits} vector hits"
        )
    elif pre_filter_hits >= fetch_limit and post_filter_hits < pre_filter_hits:
        warnings.append(
            f"repo '{repo_value}' kept {post_filter_hits} of {pre_filter_hits} hits "
            "and the fetch window was exhausted; raise top_k or narrow with "
            "project_id + repo"
        )
    return scope, warnings


def graph_anchors_in_repo(
    graph: Dict[str, Any],
    anchor_ids: List[str],
    repo_value: Optional[str],
) -> bool:
    """Check user-addressed anchor nodes of a rendered path/subgraph.

    Traversal-discovered nodes are never checked (RR2): only the ids the
    caller explicitly asked about gate the result. An anchor missing from
    the rendered graph cannot be verified and therefore passes.
    """
    if repo_value is None:
        return True
    nodes = graph.get("nodes") or []
    for anchor_id in anchor_ids:
        rec = next(
            (
                node
                for node in nodes
                if isinstance(node, dict) and node.get("id") == anchor_id
            ),
            None,
        )
        if rec is None:
            continue
        props = rec.get("properties") or {}
        if not matches_repo_scope(props.get("repo"), repo_value):
            return False
    return True
