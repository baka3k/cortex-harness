"""Shared project-scope helpers for vector and graph retrieval.

The ``project_id`` query contract (see ``docs/project_id_query_rules.md``):

* omitted/blank ``project_id`` → unscoped query across every project;
* a given ``project_id`` is matched case-insensitively (``casefold()``);
* matching is prefix/LIKE: a query for ``bank`` matches stored ids
  ``bank_android``, ``bank_Cplus``, ... because the scanner derives
  per-target project ids as ``{name}_{platform}`` suffixes of a shared
  stem. Exact match is the degenerate one-key case.
* read paths only — write/sync/delete paths keep exact equality so one
  project can never mutate another.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional


PROJECT_ID_NORMALIZED_FIELD = "project_id_normalized"
_PROJECT_SCOPE_PARAMETER_KEYS = frozenset(
    {
        "project_id",
        "be_project_id",
        "fe_project_id",
        "be_project",
        "fe_project",
        "pid",
    }
)

REPO_SCOPE_PARAMETER_KEYS = ("repo",)
REPO_SUFFIX_PARAMETER = "repo_suffix"

__all__ = [
    "PROJECT_ID_NORMALIZED_FIELD",
    "REPO_SCOPE_PARAMETER_KEYS",
    "REPO_SUFFIX_PARAMETER",
    "enrich_project_scope",
    "matches_project_scope",
    "matches_repo_scope",
    "normalize_project_id",
    "prepare_project_scope_parameters",
    "prepare_repo_scope_parameters",
    "project_id_lookup_key",
    "project_id_scope_keys",
    "qdrant_project_filter",
    "registered_project_scope_ids",
    "repo_scope_predicate",
]


def normalize_project_id(value: Any) -> Optional[str]:
    """Return a non-empty project id, or ``None`` for an unscoped query."""
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def project_id_lookup_key(value: Any) -> Optional[str]:
    """Return the case-insensitive comparison key for a project id."""
    normalized = normalize_project_id(value)
    return normalized.casefold() if normalized is not None else None


def registered_project_scope_ids() -> List[str]:
    """Best-effort list of registered project ids for scope expansion.

    Reads the ProjectRegistry lazily (it imports this module, so a module
    level import would be circular). Any registry failure degrades to an
    empty list — scope matching then falls back to the exact query key,
    which keeps unregistered/out-of-band shards reachable.
    """
    try:
        from tools.common.project_registry import list_registered_projects

        return list(list_registered_projects())
    except Exception:  # noqa: BLE001 - scope expansion must never break a query
        return []


def project_id_scope_keys(
    value: Any,
    known_ids: Optional[Iterable[Any]] = None,
) -> Optional[List[str]]:
    """Return the case-insensitive match keys for a project-scope query.

    Implements the LIKE/prefix rule of the query contract: the query's own
    casefold() key always matches (so an exact id — or an unregistered
    shard keyed by the raw id — stays reachable), plus every known project
    id whose casefold() key starts with the query key. ``bank`` therefore
    matches ``bank_android`` / ``bank_Cplus`` payloads as well as ``bank``.

    ``None`` means the query is unscoped: callers must suppress the filter
    entirely so it crosses project boundaries.
    """
    query_key = project_id_lookup_key(value)
    if query_key is None:
        return None
    keys = {query_key}
    for known in known_ids if known_ids is not None else registered_project_scope_ids():
        known_key = project_id_lookup_key(known)
        if known_key and known_key.startswith(query_key):
            keys.add(known_key)
    return sorted(keys)


def enrich_project_scope(value: Any) -> Any:
    """Copy nested query/write payloads and add normalized project scope keys."""
    if isinstance(value, Mapping):
        enriched = {key: enrich_project_scope(item) for key, item in value.items()}
        if "project_id" in enriched:
            lookup_key = project_id_lookup_key(enriched.get("project_id"))
            if lookup_key is not None:
                enriched[PROJECT_ID_NORMALIZED_FIELD] = lookup_key
            else:
                enriched.pop(PROJECT_ID_NORMALIZED_FIELD, None)
        return enriched
    if isinstance(value, list):
        return [enrich_project_scope(item) for item in value]
    if isinstance(value, tuple):
        return tuple(enrich_project_scope(item) for item in value)
    return value


def prepare_project_scope_parameters(
    query: str,
    parameters: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Prepare graph parameters for normalized project-scope predicates.

    Raw project identifiers remain untouched for legacy predicates and write
    identities. Each recognized project-scope parameter receives a sibling
    ``*_normalized`` parameter for normalized predicates. Callers omit
    ``project_id`` (or pass an empty/blank value) to query across every
    project — the normalized key is simply not added in that case so
    Cypher queries that filter on ``$project_id_normalized`` automatically
    skip the predicate.

    The ``query`` argument is preserved for backwards compatibility with the
    original signature and may be used by future callers to inspect the Cypher
    text. It is intentionally unused today.
    """
    prepared = enrich_project_scope(dict(parameters or {}))
    for key in _PROJECT_SCOPE_PARAMETER_KEYS:
        if key in prepared:
            prepared[f"{key}_normalized"] = project_id_lookup_key(prepared[key])
    # ``query`` is intentionally unused; it is part of the public signature so
    # future versions can inspect the Cypher text without breaking callers.
    del query
    return prepared


def qdrant_project_filter(
    project_id: Any,
    known_ids: Optional[Iterable[Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Build the canonical Qdrant payload filter for a project scope.

    When ``project_id`` is empty (``None``/blank), the filter is suppressed
    (``None`` returned) so the query crosses project boundaries. This is the
    implicit default for the unified contract.

    A scoped query expands to the prefix/LIKE key set from
    :func:`project_id_scope_keys` (``bank`` → ``bank`` + ``bank_android`` +
    ``bank_Cplus``) and matches any of them, so it works identically on the
    local embedded store and a remote Qdrant server (``match.any`` is a
    plain payload condition in both). Pass ``known_ids`` to pin the
    expansion source; the default reads the ProjectRegistry.
    """
    keys = project_id_scope_keys(project_id, known_ids)
    if keys is None:
        return None
    return {
        "must": [
            {"key": PROJECT_ID_NORMALIZED_FIELD, "match": {"any": keys}},
        ],
    }


def matches_project_scope(
    candidate: Mapping[str, Any],
    project_id: Any,
) -> bool:
    """Return whether a retrieval candidate belongs to the requested scope.

    ``project_id`` ``None``/blank matches every candidate (unscoped query).
    A scoped query matches when the candidate's casefold() project key
    starts with the query key — the same LIKE rule as
    :func:`qdrant_project_filter`, applied post-hoc to payloads that were
    not filtered server-side.
    """
    query_key = project_id_lookup_key(project_id)
    if query_key is None:
        return True
    candidate_key = project_id_lookup_key(
        candidate.get(PROJECT_ID_NORMALIZED_FIELD, candidate.get("project_id"))
    )
    return bool(candidate_key) and candidate_key.startswith(query_key)


def repo_scope_predicate(alias: str) -> str:
    """Return the standard ``repo`` scope predicate for a node alias.

    The fragment is the Cypher twin of :func:`matches_repo_scope` (rule RR3
    of ``docs/REPO_QUERY_RULES.md``): a node matches when its ``repo``
    equals the requested value exactly or ends with ``/`` + value, so both
    stored flavors (absolute path and ``{project}/{basename}``) are covered
    without matching accidental basename prefixes (``s3`` must not match
    ``xs3``). Call sites concatenate it after their ``AND``; the ``$repo`` /
    ``$repo_suffix`` parameters come from :func:`prepare_repo_scope_parameters`.

    The unscoped test is ``$repo = ''`` — NOT ``$repo IS NULL`` — on
    purpose: LadybugDB binds a ``None`` parameter as BOOL, so an
    ``$repo IS NULL OR n.repo = $repo`` fragment types ``$repo`` as BOOL and
    the string comparison then fails at runtime. Binding ``''`` keeps every
    reference STRING on all three backends. (The ``project_id`` predicate
    only survives NULL binding because its IS NULL branch names a different
    parameter than the comparison branch.)

    ``alias`` is a static Cypher identifier interpolated verbatim — user
    values only ever travel through parameters, never through this fragment.
    """
    return (
        f"(${REPO_SCOPE_PARAMETER_KEYS[0]} = '' OR {alias}.repo"
        f" = ${REPO_SCOPE_PARAMETER_KEYS[0]} OR {alias}.repo"
        f" ENDS WITH ${REPO_SUFFIX_PARAMETER})"
    )


def prepare_repo_scope_parameters(
    parameters: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Prepare graph parameters for the optional ``repo`` scope predicate.

    Mirrors the ``*_normalized`` mechanism of
    :func:`prepare_project_scope_parameters`: when the parameters carry a
    ``repo`` key, the value is normalized (blank → ``''``) and the sibling
    ``repo_suffix`` parameter (``"/" + repo``) is injected for the
    ``ENDS WITH`` branch of :func:`repo_scope_predicate` — the suffix is
    precomputed here because Cypher string concatenation inside the
    predicate is not portable across all three backends.

    An absent ``repo`` key injects nothing. An unscoped ``repo`` binds the
    empty string (never ``None`` — see :func:`repo_scope_predicate` for the
    LadybugDB BOOL-binding constraint) and the predicate's ``= ''`` branch
    makes the whole filter a no-op.
    """
    prepared = dict(parameters or {})
    if "repo" not in prepared:
        return prepared
    repo = normalize_project_id(prepared.get("repo")) or ""
    prepared["repo"] = repo
    prepared[REPO_SUFFIX_PARAMETER] = f"/{repo}" if repo else ""
    return prepared


def matches_repo_scope(value: Any, repo: Any) -> bool:
    """Python twin of the Cypher ``repo`` scope predicate (RR3/RR5).

    ``repo`` ``None``/blank is an unscoped query and matches every value.
    An empty/missing node value (``repo=""`` — "belongs to an unknown
    repo") never matches a scoped query. Otherwise the value matches when
    it equals the requested repo exactly or ends with ``/`` + repo, the
    same exact-or-suffix rule :func:`repo_scope_predicate` encodes.
    """
    requested = normalize_project_id(repo)
    if requested is None:
        return True
    node_repo = str(value) if value is not None else ""
    if not node_repo:
        return False
    return node_repo == requested or node_repo.endswith(f"/{requested}")