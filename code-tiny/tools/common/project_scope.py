"""Shared project-scope helpers for vector and graph retrieval."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional


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


def qdrant_project_filter(project_id: Any) -> Optional[Dict[str, Any]]:
    """Build the canonical Qdrant payload filter for a project scope.

    When ``project_id`` is empty (``None``/blank), the filter is suppressed
    (``None`` returned) so the query crosses project boundaries. This is the
    implicit default for the unified contract.
    """
    normalized = project_id_lookup_key(project_id)
    if normalized is None:
        return None
    return {
        "must": [
            {"key": PROJECT_ID_NORMALIZED_FIELD, "match": {"value": normalized}},
        ],
    }


def matches_project_scope(
    candidate: Mapping[str, Any],
    project_id: Any,
) -> bool:
    """Return whether a retrieval candidate belongs to the requested project.

    When ``project_id`` is ``None`` (or blank), every candidate matches —
    the filter is intentionally a no-op so the caller can paginate across
    projects.
    """
    normalized = project_id_lookup_key(project_id)
    if normalized is None:
        return True
    candidate_key = project_id_lookup_key(
        candidate.get(PROJECT_ID_NORMALIZED_FIELD, candidate.get("project_id"))
    )
    return candidate_key == normalized