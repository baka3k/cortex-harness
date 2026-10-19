"""Registry facade for link-tiny.

Delegating boundary over ``code-tiny/tools/common/project_registry.py``:
project naming rules (``code_graph == project_id``,
``doc_graph == f"{project_id}_doc"``) stay owned by the code-side registry,
and link-tiny never derives names from env vars itself. When the code
registry is not importable (standalone link-tiny runtime), a naming-contract
fallback keeps read paths working without re-implementing config parsing.

Also owns the instance ``links/`` directory resolution — the runtime layout
``{instance_root}/links/{project_id}/`` lives beside the storage manifest
but is deliberately invisible to ``layout.py`` owner validation (a links
directory is not a storage owner).
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional


_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for _entry in (_REPO_ROOT, os.path.join(_REPO_ROOT, "code-tiny")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

# Delegation boundary: link-tiny keeps no parse hook of its own, so the
# public resolver is correct here (unlike doc-tiny, which must use the
# private entry-level resolver to preserve its parse hook).
try:
    from tools.common.project_registry import (  # type: ignore
        ProjectNotRegisteredError as _RegistryNotRegisteredError,
        list_registered_projects as _registry_list_projects,
        resolve_project_targets as _registry_resolve_targets,
    )
except Exception:  # standalone link-tiny runtime without the code registry
    _RegistryNotRegisteredError = None  # type: ignore[assignment]
    _registry_resolve_targets = None  # type: ignore[assignment]
    _registry_list_projects = None  # type: ignore[assignment]


class ProjectNotRegisteredError(KeyError):
    """Raised when no config describes ``project_id``.

    Mirrors the code-side registry error so callers can catch one type
    regardless of whether delegation succeeded.
    """

    def __init__(self, project_id: Any, known: List[str]):
        known_list = sorted(str(item) for item in known)
        rendered = ", ".join(known_list) if known_list else "<none registered>"
        message = (
            f"project_id '{project_id}' is not registered. "
            f"Known projects: {rendered}. "
            "Add a project section to .cortext-harness/config/*.json."
        )
        super().__init__(message)
        self.project_id = project_id
        self.known = known_list

    def __str__(self) -> str:
        return self.args[0] if self.args else ""


@dataclass(frozen=True)
class LinkProjectTargets:
    """Storage targets link-tiny needs for one project.

    Field names mirror the code-side ``ProjectTargets`` so delegation can
    return it directly; the fallback resolver builds the same shape from
    the naming contract.
    """

    project_id: str
    project_id_normalized: str
    code_graph: str
    doc_graph: str
    code_qdrant_collection: str
    doc_qdrant_collection: str
    parser_type: Optional[str] = None
    provider: str = "falkordb"


def normalize_project_id(value: Any) -> Optional[str]:
    """Return a non-empty project id, or ``None`` for an unscoped query."""
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


# Same shape the lifecycle enforces for storage identities: the project id
# becomes a filesystem path segment under links/, so separators and
# traversal are rejected at this boundary.
_SAFE_PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def is_safe_project_id(value: str) -> bool:
    """True when ``value`` is safe to interpolate into a storage path."""
    return bool(_SAFE_PROJECT_ID_RE.match(value))


def _from_registry_targets(targets: Any) -> LinkProjectTargets:
    return LinkProjectTargets(
        project_id=targets.project_id,
        project_id_normalized=targets.project_id_normalized,
        code_graph=targets.code_graph,
        doc_graph=targets.doc_graph,
        code_qdrant_collection=targets.code_qdrant_collection,
        doc_qdrant_collection=targets.doc_qdrant_collection,
        parser_type=getattr(targets, "parser_type", None),
        provider=getattr(targets, "provider", "falkordb"),
    )


def _fallback_targets(project_id: str) -> LinkProjectTargets:
    """Naming-contract resolution when the code registry is unavailable.

    Provider/graph overrides from env are honoured only where the naming
    contract leaves a hole; link identity never depends on them because
    the registry is the join key.
    """
    lookup = project_id.casefold()
    provider = (os.environ.get("CODE_GRAPH_PROVIDER") or "falkordb").strip().lower()
    code_graph = os.environ.get("FALKORDB_GRAPH") or project_id
    doc_graph = os.environ.get("DOC_FALKORDB_GRAPH") or f"{project_id}_doc"
    return LinkProjectTargets(
        project_id=project_id,
        project_id_normalized=lookup,
        code_graph=code_graph,
        doc_graph=doc_graph,
        code_qdrant_collection=os.environ.get("QDRANT_COLLECTION") or project_id,
        doc_qdrant_collection=os.environ.get("QDRANT_COLLECTION_DOC") or f"{project_id}_doc",
        parser_type=os.environ.get("PARSER_TYPE") or None,
        provider=provider if provider in {"falkordb", "ladybug", "neo4j"} else "falkordb",
    )


def resolve_project_targets(
    project_id: Any,
    *,
    config_dir: Optional[Path] = None,
) -> LinkProjectTargets:
    """Resolve the link-side storage targets for ``project_id``.

    Raises :class:`ProjectNotRegisteredError` when the project is not
    registered and no env seeds an ad-hoc project.
    """
    normalized = normalize_project_id(project_id)
    if normalized is None:
        raise ProjectNotRegisteredError(project_id, [])
    if _registry_resolve_targets is not None:
        try:
            return _from_registry_targets(
                _registry_resolve_targets(normalized, config_dir=config_dir)
            )
        except _RegistryNotRegisteredError as exc:  # type: ignore[misc]
            raise ProjectNotRegisteredError(
                exc.project_id, list(getattr(exc, "known", []))
            ) from exc
    return _fallback_targets(normalized)


def list_registered_projects(
    *, config_dir: Optional[Path] = None
) -> List[str]:
    """Best-effort list of registered project ids (registry order)."""
    if _registry_list_projects is not None:
        try:
            return list(_registry_list_projects(config_dir=config_dir))
        except Exception:  # noqa: BLE001 - listing must never break a read
            return []
    return []


def project_root_from_config(config_dir: Optional[Path] = None) -> Path:
    """Locate the project root (the directory holding ``.cortext-harness``).

    ``CORTEX_HARNESS_CONFIG_PATH`` wins when set (the lifecycle launcher and
    dev.py both export it); otherwise walk up from CWD like the registry
    does. Storage resolution and the ``links/`` layout both key off this
    root.
    """
    directory = config_dir
    if directory is None:
        explicit = str(os.environ.get("CORTEX_HARNESS_CONFIG_PATH") or "").strip()
        if explicit:
            candidate = Path(explicit).expanduser()
            directory = candidate if candidate.is_dir() else candidate.parent
    if directory is not None:
        directory = Path(directory)
        if directory.name == "config":
            return directory.parent.parent
        if directory.name == ".cortext-harness":
            return directory.parent
        return directory
    cwd = Path.cwd()
    for candidate in (cwd, *cwd.parents):
        if (candidate / ".cortext-harness" / "config").is_dir():
            return candidate
    return cwd


def instance_root(project_dir: Optional[Path] = None) -> Path:
    """Return the storage instance root for ``project_dir``.

    Resolved through ``cortex_harness.storage.resolve_storage`` (lazy import
    — tree code must not import the harness at module import time). The
    ``CORTEX_LINKS_ROOT`` env override exists for standalone runtimes and
    tests; when set it replaces the whole instance root.
    """
    override = str(os.environ.get("CORTEX_LINKS_ROOT") or "").strip()
    if override:
        return Path(override).expanduser()
    root = Path(project_dir) if project_dir else project_root_from_config()
    from cortex_harness.storage import resolve_storage

    resolved = resolve_storage(root)
    return Path(resolved.manifest_path).parent


def links_dir(
    project_id: Any,
    *,
    project_dir: Optional[Path] = None,
) -> Path:
    """Return ``{instance_root}/links/{project_id}/`` for ``project_id``.

    The directory is created lazily by writers; read paths must tolerate it
    being absent (no links built yet).
    """
    normalized = normalize_project_id(project_id)
    if normalized is None or not is_safe_project_id(normalized):
        # The id becomes a path segment; anything outside the lifecycle's
        # storage-identity pattern (traversal, separators) is rejected
        # before it can reach the filesystem.
        raise ProjectNotRegisteredError(project_id, [])
    return instance_root(project_dir) / "links" / normalized


__all__ = [
    "LinkProjectTargets",
    "ProjectNotRegisteredError",
    "instance_root",
    "is_safe_project_id",
    "links_dir",
    "list_registered_projects",
    "normalize_project_id",
    "project_root_from_config",
    "resolve_project_targets",
]
