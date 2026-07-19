"""Shared project-scope helpers for vector and graph retrieval."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional


def normalize_project_id(value: Any) -> Optional[str]:
    """Return a non-empty project id, or ``None`` for an unscoped query."""
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def qdrant_project_filter(project_id: Any) -> Optional[Dict[str, Any]]:
    """Build the canonical Qdrant payload filter for a project scope."""
    normalized = normalize_project_id(project_id)
    if normalized is None:
        return None
    return {
        "must": [
            {"key": "project_id", "match": {"value": normalized}},
        ],
    }


def matches_project_scope(candidate: Mapping[str, Any], project_id: Any) -> bool:
    """Return whether a retrieval candidate belongs to the requested project."""
    normalized = normalize_project_id(project_id)
    if normalized is None:
        return True
    return normalize_project_id(candidate.get("project_id")) == normalized
