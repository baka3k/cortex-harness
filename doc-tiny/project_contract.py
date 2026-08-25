"""Project contract helpers for doc-tiny.

Mirrors the subset of ``code-tiny/tools/common/project_registry.py`` and
``code-tiny/tools/common/project_scope.py`` that doc-tiny needs. The two
modules cannot import from each other because doc-tiny is laid out as flat
scripts rather than a Python package.

Per Phase 04 / Phase 05 of the unified ingest/query contract plan, every
project-scoped tool in doc-tiny accepts ``project_id`` per call and resolves
its graph + collection through ``resolve_doc_targets``. A missing
``project_id`` falls through to the implicit full-search path (omit it to
query across every project).

The lookup keys are case-insensitive ``casefold()`` values. The naming
contract is:

* ``doc_graph == "{project_id}_doc"`` — separate from the code graph so
  disjoint labels stay queryable.
* ``doc_qdrant_collection == "{project_id}_doc"`` — same convention as
  the code side; ``project_id`` is reused as the suffix.

If a project is registered in ``.cortext-harness/config/*.json``, the
config file's ``doc.env.FALKORDB_GRAPH`` and ``doc.env.QDRANT_COLLECTION``
override the defaults. If no config file exists, the naming rule is
applied.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional


PROJECT_ID_NORMALIZED_FIELD = "project_id_normalized"


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


@dataclass(frozen=True)
class ProjectTargets:
    """Doc-side storage targets resolved through the registry."""

    project_id: str
    project_id_normalized: str
    doc_graph: str
    doc_qdrant_collection: str
    source: str = field(default="registry", compare=False)


class ProjectContractError(KeyError):
    """Base class for project-contract errors."""


class ProjectNotRegisteredError(ProjectContractError):
    """Raised when no config describes ``project_id`` and no env seeds it."""

    def __init__(self, project_id: Any, known: Iterable[str]):
        known_list = sorted(known)
        rendered = ", ".join(known_list) if known_list else "<none registered>"
        message = (
            f"project_id '{project_id}' is not registered. "
            f"Known projects: {rendered}. "
            "Add a project section to .cortext-harness/config/*.json or pass "
            "an explicit override."
        )
        super().__init__(message)
        self.project_id = project_id
        self.known = known_list

    def __str__(self) -> str:
        return self.args[0] if self.args else ""


class DuplicateProjectRegistrationError(ProjectContractError):
    """Raised when two descriptors casefold to the same project id."""

    def __init__(self, collisions: Mapping[str, Iterable[str]]):
        self.collisions = {
            key: sorted(str(value) for value in values)
            for key, values in sorted(collisions.items())
        }
        details = "; ".join(
            f"{key}: {', '.join(values)}"
            for key, values in self.collisions.items()
        )
        super().__init__(
            "Duplicate project registrations after casefold normalization: "
            f"{details}."
        )

    def __str__(self) -> str:
        return self.args[0] if self.args else ""


def _default_config_dir() -> Path:
    """Locate ``.cortext-harness/config`` from CWD, walking up."""
    explicit = str(os.environ.get("CORTEX_HARNESS_CONFIG_PATH") or "").strip()
    if explicit:
        path = Path(explicit).expanduser()
        return path if path.is_dir() else path.parent
    cwd = Path.cwd()
    for candidate in (cwd, *cwd.parents):
        path = candidate / ".cortext-harness" / "config"
        if path.is_dir():
            return path
    return cwd / ".cortext-harness" / "config"


def _read_project_entries(config_dir: Path) -> List[Dict[str, Any]]:
    if not config_dir.is_dir():
        return []
    entries: List[Dict[str, Any]] = []
    for file_path in sorted(config_dir.glob("*.json")):
        try:
            with file_path.open(encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, Mapping):
            continue
        project_section = payload.get("project") or {}
        project_id = (
            project_section.get("code")
            or project_section.get("name")
        )
        if not project_id:
            continue
        entries.append(
            {
                "project_id": str(project_id),
                "doc_env": dict(payload.get("doc", {}).get("env") or {}),
            }
        )
    variants: Dict[str, List[str]] = {}
    for entry in entries:
        lookup = project_id_lookup_key(entry.get("project_id"))
        if lookup is not None:
            variants.setdefault(lookup, []).append(str(entry["project_id"]))
    collisions = {key: values for key, values in variants.items() if len(values) > 1}
    if collisions:
        raise DuplicateProjectRegistrationError(collisions)
    return entries


def _resolve_doc_targets(
    project_id: str,
    entries: List[Dict[str, Any]],
) -> ProjectTargets:
    lookup = project_id_lookup_key(project_id)
    if lookup is None:
        raise ProjectNotRegisteredError(project_id, [e["project_id"] for e in entries])
    match: Optional[Dict[str, Any]] = None
    for entry in entries:
        if project_id_lookup_key(entry.get("project_id")) == lookup:
            match = entry
            break
    if match is None:
        raise ProjectNotRegisteredError(
            project_id, [e["project_id"] for e in entries]
        )
    # Canonicalize the raw project_id to the registered form so case
    # variants of the same project collapse to identical ProjectTargets.
    canonical_project_id = str(match["project_id"]) if match is not None else project_id
    doc_env: Mapping[str, Any] = (match or {}).get("doc_env") or {}
    doc_graph = (
        doc_env.get("FALKORDB_GRAPH")
        or doc_env.get("NEO4J_DB")
        or f"{canonical_project_id}_doc"
    )
    doc_qdrant = (
        doc_env.get("QDRANT_COLLECTION")
        or f"{canonical_project_id}_doc"
    )
    return ProjectTargets(
        project_id=canonical_project_id,
        project_id_normalized=lookup,
        doc_graph=doc_graph,
        doc_qdrant_collection=doc_qdrant,
        source="registry",
    )


def resolve_project_targets(
    project_id: Any,
    *,
    config_dir: Optional[Path] = None,
) -> ProjectTargets:
    """Resolve the doc-side storage targets for ``project_id``.

    Raises ``ProjectNotRegisteredError`` if the project is not in any
    config file. Config files must live under
    ``.cortext-harness/config/*.json``.
    """
    normalized = normalize_project_id(project_id)
    if normalized is None:
        raise ProjectNotRegisteredError(project_id, [])
    directory = Path(config_dir) if config_dir is not None else _default_config_dir()
    entries = _read_project_entries(directory)
    return _resolve_doc_targets(normalized, entries)


def list_registered_projects(config_dir: Optional[Path] = None) -> List[str]:
    directory = Path(config_dir) if config_dir is not None else _default_config_dir()
    return [entry["project_id"] for entry in _read_project_entries(directory)]


def with_overrides(targets: ProjectTargets, **overrides: Optional[str]) -> ProjectTargets:
    """Return a new ``ProjectTargets`` with the given fields replaced."""
    valid_fields = {"project_id", "project_id_normalized", "doc_graph", "doc_qdrant_collection"}
    for key in overrides:
        if key not in valid_fields:
            raise ValueError(
                f"unknown ProjectTargets field '{key}'. "
                f"Valid fields: {sorted(valid_fields)}"
            )
    return replace(targets, **overrides)


def qdrant_project_filter(project_id: Any) -> Optional[Dict[str, Any]]:
    """Build the canonical Qdrant payload filter for a doc project scope.

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
