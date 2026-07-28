"""ProjectRegistry — single source of truth for project_id -> storage targets.

The registry maps a ``project_id`` (e.g. ``"cortext"``) to every storage target
the harness touches: the code graph name, the code Qdrant collection, the doc
graph name, and the doc Qdrant collection. Both ``graph_mcp`` and ``mind_mcp``
call ``resolve_project_targets(project_id)`` on every project-scoped operation
instead of deriving names from env vars or module-level defaults.

Naming contract (applied when config omits a field):

* ``code_graph == project_id``
* ``code_qdrant_collection == project_id``
* ``doc_graph == f"{project_id}_doc"`` (separate graph; disjoint labels)
* ``doc_qdrant_collection == f"{project_id}_doc"``

Lookup is case-insensitive via ``project_id_lookup_key`` from
:mod:`project_scope`. The registry reads ``.cortext-harness/config/*.json`` on
every call (no cache) — accepted trade-off, see
``plans/260728-0000-unified-ingest-query-contract/plan.md``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from tools.common.project_scope import (
    normalize_project_id,
    project_id_lookup_key,
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ProjectRegistryError(KeyError):
    """Base class for project-registry errors."""


class ProjectNotRegisteredError(ProjectRegistryError):
    """Raised when no config describes ``project_id`` and no override is given."""

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

    def __str__(self) -> str:  # KeyError.__str__ shows repr; use the message.
        return self.args[0] if self.args else ""


# ---------------------------------------------------------------------------
# Defaults and env layout
# ---------------------------------------------------------------------------


# Field on a project config entry that names the project (mirrors dev.json).
_PROJECT_NAME_KEY = "name"
# Field that holds the canonical project_id (used as the code graph shard
# name and as the registry key). dev.json uses ``project.code`` for this.
_PROJECT_CODE_KEY = "code"

# Default location for the harness config dir. Overridable for tests.
DEFAULT_CONFIG_DIRNAME = ".cortext-harness/config"

# Env vars consulted as ad-hoc overrides when a project has no config entry.
_ENV_CODE_GRAPH = ("FALKORDB_GRAPH", "NEO4J_DB")
_ENV_CODE_COLLECTION = "QDRANT_COLLECTION"
_ENV_DOC_GRAPH = "FALKORDB_GRAPH_DOC"
_ENV_DOC_COLLECTION = "QDRANT_COLLECTION_DOC"
_ENV_PROVIDER = "GRAPH_PROVIDER"


# ---------------------------------------------------------------------------
# ProjectTargets — the resolved contract returned to callers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProjectTargets:
    """All storage targets for one project, resolved through the registry.

    Fields are the canonical names to use when talking to graph and Qdrant
    backends. ``project_id`` is the raw identifier (preserved for display
    and identity); ``project_id_normalized`` is the casefold() comparison
    key reused from ``project_scope``.
    """

    project_id: str
    project_id_normalized: str
    code_graph: str
    code_qdrant_collection: str
    doc_graph: str
    doc_qdrant_collection: str
    parser_type: Optional[str] = None
    provider: str = "falkordb"
    # Source of the resolution — useful for diagnostics and tests. Not part of
    # the public contract; callers should not branch on it.
    source: str = field(default="registry", compare=False)


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def _default_config_dir() -> Path:
    """Locate ``.cortext-harness/config`` relative to the repo root.

    Walks up from CWD until the directory exists or the filesystem root is
    reached. Falls back to the CWD-relative path so tests can monkeypatch the
    surrounding directory.
    """
    cwd = Path.cwd()
    for candidate in (cwd, *cwd.parents):
        path = candidate / DEFAULT_CONFIG_DIRNAME
        if path.is_dir():
            return path
    return cwd / DEFAULT_CONFIG_DIRNAME


def _read_config_files(config_dir: Path) -> List[Dict[str, Any]]:
    """Return the list of project descriptors parsed from ``config_dir``."""
    if not config_dir.is_dir():
        return []
    documents: List[Dict[str, Any]] = []
    for entry in sorted(config_dir.glob("*.json")):
        try:
            with entry.open(encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, Mapping):
            documents.append(dict(payload))
    return documents


def _project_entries(documents: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Flatten the ``[project]`` / ``[code, doc]`` shape of dev.json into
    one dict per registered project.

    Each entry carries at minimum ``project_id`` (the registry key). ``code``
    and ``doc`` env sub-dicts are preserved so callers can read overrides.
    Empty / malformed entries are skipped.
    """
    entries: List[Dict[str, Any]] = []
    for document in documents:
        project_section = document.get("project") or {}
        project_id = (
            project_section.get(_PROJECT_CODE_KEY)
            or project_section.get(_PROJECT_NAME_KEY)
        )
        if not project_id:
            continue
        entry: Dict[str, Any] = {
            "project_id": str(project_id),
            "project_name": project_section.get(_PROJECT_NAME_KEY),
            "code_env": dict(document.get("code", {}).get("env") or {}),
            "doc_env": dict(document.get("doc", {}).get("env") or {}),
            "active": bool(document.get("active", False)),
        }
        entries.append(entry)
    return entries


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def _first_env(names: Iterable[str]) -> Optional[str]:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def _resolve_targets(
    project_id: str,
    entries: List[Dict[str, Any]],
    *,
    provider_override: Optional[str] = None,
    code_graph_override: Optional[str] = None,
    code_qdrant_override: Optional[str] = None,
    doc_graph_override: Optional[str] = None,
    doc_qdrant_override: Optional[str] = None,
    parser_type_override: Optional[str] = None,
) -> ProjectTargets:
    """Build ``ProjectTargets`` for ``project_id``.

    Resolution precedence (lowest to highest): the naming contract, env vars,
    config-file values, per-call overrides. Env vars only contribute when
    no config file describes any project — this stops a stray ``NEO4J_DB``
    on the host from silently shadowing an explicitly registered project.
    """
    lookup = project_id_lookup_key(project_id)
    if lookup is None:
        raise ProjectNotRegisteredError(project_id, [e["project_id"] for e in entries])

    match: Optional[Dict[str, Any]] = None
    for entry in entries:
        if project_id_lookup_key(entry.get("project_id")) == lookup:
            match = entry
            break

    # If a config registry exists and this project isn't in it, raise.
    # When no registry exists at all, env vars seed an ad-hoc project —
    # but only when the env actually has values to seed. With no config
    # AND no env, the project is genuinely unknown and we raise.
    if match is None:
        env_seeds = (
            _first_env(_ENV_CODE_GRAPH)
            or os.environ.get(_ENV_CODE_COLLECTION)
            or os.environ.get(_ENV_DOC_COLLECTION)
            or _first_env((_ENV_DOC_GRAPH,))
            or os.environ.get(_ENV_PROVIDER)
        )
        if entries:
            # A registry exists but this project isn't in it.
            raise ProjectNotRegisteredError(
                project_id, [e["project_id"] for e in entries]
            )
        if not env_seeds:
            # Nothing to seed an ad-hoc project from.
            raise ProjectNotRegisteredError(project_id, [])
        env_allowed = True
    else:
        env_allowed = False

    # Canonical raw project_id: prefer the registered value so case variants
    # of the same logical project produce identical ProjectTargets.
    canonical_project_id = (
        str(match["project_id"]) if match is not None else project_id
    )

    code_env: Mapping[str, Any] = (match or {}).get("code_env") or {}
    doc_env: Mapping[str, Any] = (match or {}).get("doc_env") or {}
    env_allowed = match is None  # only ad-hoc projects may use env vars

    def _code_graph() -> str:
        if code_graph_override:
            return code_graph_override
        if code_env.get("FALKORDB_GRAPH"):
            return str(code_env["FALKORDB_GRAPH"])
        if code_env.get("NEO4J_DB"):
            return str(code_env["NEO4J_DB"])
        if env_allowed:
            env_value = _first_env(_ENV_CODE_GRAPH)
            if env_value:
                return env_value
        return canonical_project_id

    def _code_qdrant() -> str:
        if code_qdrant_override:
            return code_qdrant_override
        if code_env.get("QDRANT_COLLECTION"):
            return str(code_env["QDRANT_COLLECTION"])
        if env_allowed:
            env_value = os.environ.get(_ENV_CODE_COLLECTION)
            if env_value:
                return env_value
        return canonical_project_id

    def _doc_graph() -> str:
        if doc_graph_override:
            return doc_graph_override
        if doc_env.get("FALKORDB_GRAPH"):
            return str(doc_env["FALKORDB_GRAPH"])
        if doc_env.get("NEO4J_DB"):
            return str(doc_env["NEO4J_DB"])
        if env_allowed:
            env_value = _first_env((_ENV_DOC_GRAPH,))
            if env_value:
                return env_value
        return f"{canonical_project_id}_doc"

    def _doc_qdrant() -> str:
        if doc_qdrant_override:
            return doc_qdrant_override
        if doc_env.get("QDRANT_COLLECTION"):
            return str(doc_env["QDRANT_COLLECTION"])
        if env_allowed:
            env_value = os.environ.get(_ENV_DOC_COLLECTION)
            if env_value:
                return env_value
        return f"{canonical_project_id}_doc"

    parser_type = parser_type_override  # None for now; populated later phases.
    provider = (
        provider_override
        or code_env.get("GRAPH_PROVIDER")
        or (os.environ.get(_ENV_PROVIDER) if env_allowed else None)
        or "falkordb"
    )

    return ProjectTargets(
        project_id=canonical_project_id,
        project_id_normalized=lookup,
        code_graph=_code_graph(),
        code_qdrant_collection=_code_qdrant(),
        doc_graph=_doc_graph(),
        doc_qdrant_collection=_doc_qdrant(),
        parser_type=parser_type,
        provider=str(provider),
        source="registry" if match is not None else "env+defaults",
    )


def resolve_project_targets(
    project_id: Any,
    *,
    config_dir: Optional[Path] = None,
    provider: Optional[str] = None,
    code_graph: Optional[str] = None,
    code_qdrant_collection: Optional[str] = None,
    doc_graph: Optional[str] = None,
    doc_qdrant_collection: Optional[str] = None,
    parser_type: Optional[str] = None,
) -> ProjectTargets:
    """Resolve every storage target for ``project_id``.

    Parameters
    ----------
    project_id:
        Raw project identifier. ``None``, empty strings, and whitespace-only
        strings raise :class:`ProjectNotRegisteredError` with an empty known
        list.
    config_dir:
        Override the registry input directory. Defaults to
        ``.cortext-harness/config`` discovered from CWD. Primarily for tests.
    provider, code_graph, code_qdrant_collection, doc_graph, doc_qdrant_collection, parser_type:
        Per-call overrides. They win over both config and env defaults.
        Production code rarely needs these; they exist for ad-hoc tooling
        and tests.
    """
    normalized = normalize_project_id(project_id)
    if normalized is None:
        raise ProjectNotRegisteredError(project_id, [])

    directory = Path(config_dir) if config_dir is not None else _default_config_dir()
    entries = _project_entries(_read_config_files(directory))

    return _resolve_targets(
        normalized,
        entries,
        provider_override=provider,
        code_graph_override=code_graph,
        code_qdrant_override=code_qdrant_collection,
        doc_graph_override=doc_graph,
        doc_qdrant_override=doc_qdrant_collection,
        parser_type_override=parser_type,
    )


def list_registered_projects(
    *, config_dir: Optional[Path] = None
) -> List[str]:
    """Return the raw ``project_id`` of every registered project.

    The order matches the alphabetical order of the config files on disk,
    which keeps the result deterministic for tests and error messages.
    """
    directory = Path(config_dir) if config_dir is not None else _default_config_dir()
    entries = _project_entries(_read_config_files(directory))
    return [entry["project_id"] for entry in entries]


# ---------------------------------------------------------------------------
# Convenience: produce a copy of a ProjectTargets with one field overridden.
# Exposed so callers (Phases 02/05) can compose per-call overrides without
# re-running the entire resolution pipeline.
# ---------------------------------------------------------------------------


def with_overrides(
    targets: ProjectTargets, **overrides: Optional[str]
) -> ProjectTargets:
    """Return a new ``ProjectTargets`` with the given fields replaced.

    Unknown field names raise ``ValueError`` to surface typos early.
    """
    valid_fields = {
        f.name
        for f in ProjectTargets.__dataclass_fields__.values()  # type: ignore[attr-defined]
        if f.name != "source"
    }
    for key in overrides:
        if key not in valid_fields:
            raise ValueError(
                f"unknown ProjectTargets field '{key}'. "
                f"Valid fields: {sorted(valid_fields)}"
            )
    return replace(targets, **overrides)
