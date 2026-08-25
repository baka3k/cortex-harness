#!/usr/bin/env python3
"""Resolve the active Cortex Harness environment for an MCP server.

Precedence is intentionally handled by the lifecycle launchers:
defaults < service-local ``.env`` < active harness environment.
This module only returns the final active-project overlay.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Dict, Optional, Tuple


CONFIG_DIR = Path(".cortext-harness") / "config"
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
REPO_ROOT = Path(__file__).absolute().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cortex_harness.storage import StorageRole, resolve_storage, storage_overlay


LOCAL_STORAGE_KEYS = frozenset({
    "CORTEX_DATA_HOME", "CORTEX_STORAGE_INSTANCE", "CORTEX_CODE_STORAGE_OWNER",
    "CORTEX_DOC_STORAGE_OWNER", "QDRANT_PATH", "QDRANT_CODE_PATH",
    "QDRANT_DOC_PATH", "FALKORDB_PATH", "FALKORDB_CODE_PATH", "FALKORDB_DOC_PATH",
})

REMOTE_STORAGE_KEYS = frozenset({
    "QDRANT_URL", "QDRANT_HOST", "QDRANT_PORT", "QDRANT_API_KEY",
    "FALKORDB_URI", "FALKORDB_URL", "FALKORDB_HOST", "FALKORDB_PORT",
    "FALKORDB_USER", "FALKORDB_PASSWORD", "FALKORDB_SSL",
})

_FALKORDB_PROVIDER_VALUES = frozenset({"falkor", "falkordb"})


def normalize_graph_provider(env: Dict[str, str], scoped_provider: str) -> str:
    """Resolve one explicit graph provider and reject ambiguous values."""
    scoped_value = str(env.get(scoped_provider) or "").strip()
    global_value = str(env.get("GRAPH_PROVIDER") or "").strip()
    source = scoped_provider if scoped_value else "GRAPH_PROVIDER"
    value = (scoped_value or global_value or "falkordb").casefold()
    if value in _FALKORDB_PROVIDER_VALUES:
        return "falkordb"
    if value == "neo4j":
        return "neo4j"
    raise ValueError(
        f"Unsupported graph provider for {source}: {value!r}; expected "
        "'falkordb' (alias 'falkor') or 'neo4j'"
    )


def isolate_graph_provider_environment(
    env: Dict[str, str], scoped_provider: str
) -> str:
    """Mutate ``env`` so it contains configuration for only one provider."""
    provider = normalize_graph_provider(env, scoped_provider)
    env["GRAPH_PROVIDER"] = provider
    env[scoped_provider] = provider
    for key in tuple(env):
        if provider == "falkordb" and key.startswith("NEO4J_"):
            env.pop(key, None)
        elif provider == "neo4j" and (
            key.startswith("FALKORDB_") or key == "DOC_FALKORDB_GRAPH"
        ):
            env.pop(key, None)
    return provider


def load_config_file(path: Path) -> Tuple[dict, Optional[Path]]:
    """Load one explicit harness config without applying active-file selection."""
    path = Path(os.path.abspath(path))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}, None
    return (payload, path) if isinstance(payload, dict) else ({}, None)


def load_active_config(root: Path) -> Tuple[dict, Optional[Path]]:
    """Return the active harness config, falling back to the first config."""
    # Keep the caller-visible lexical path. On macOS /var is a symlink to
    # /private/var; resolving it here made diagnostics and tests disagree with
    # the explicitly supplied config path.
    config_dir = Path(os.path.abspath(root)) / CONFIG_DIR
    configs = sorted(config_dir.glob("*.json")) if config_dir.is_dir() else []
    if not configs:
        return {}, None

    first: Optional[Tuple[dict, Path]] = None
    for path in configs:
        payload, loaded_path = load_config_file(path)
        if loaded_path is None:
            continue
        if first is None:
            first = payload, path
        if payload.get("active") is True:
            return payload, path
    return first or ({}, None)


def _string_environment(value: object) -> Dict[str, str]:
    if not isinstance(value, dict):
        return {}
    result: Dict[str, str] = {}
    for key, raw in value.items():
        name = str(key).strip()
        if not _ENV_NAME.fullmatch(name) or raw is None:
            continue
        if isinstance(raw, bool):
            result[name] = "true" if raw else "false"
        elif isinstance(raw, (str, int, float)):
            result[name] = str(raw)
    return result


def active_local_storage_config(
    root: Path,
    config_path: Optional[Path] = None,
) -> Tuple[Dict[str, str], Optional[Path]]:
    """Return one conflict-checked local-storage config for lifecycle and MCP use."""
    config, config_path = (
        load_config_file(config_path) if config_path is not None else load_active_config(root)
    )
    if not config_path:
        return {}, None
    result: Dict[str, str] = {}
    for section_name in ("code", "doc"):
        section = config.get(section_name) if isinstance(config, dict) else {}
        environment = _string_environment(section.get("env") if isinstance(section, dict) else {})
        for key in LOCAL_STORAGE_KEYS:
            value = environment.get(key, "").strip()
            if not value:
                continue
            previous = result.get(key)
            if previous is not None and previous != value:
                raise ValueError(
                    f"Active config {config_path} has conflicting {key} values in code/doc sections"
                )
            result[key] = value
    return result, config_path


def resolve_active_storage(
    root: Path,
    *,
    config_path: Optional[Path] = None,
    **logical_targets: object,
):
    """Resolve the active config, with explicit process-local path overrides winning."""
    config, loaded_path = (
        load_config_file(config_path) if config_path is not None else load_active_config(root)
    )
    if loaded_path is None:
        config = {}
    else:
        config = dict(config)
    local_config, _ = active_local_storage_config(root, config_path)
    config.update(local_config)
    for key in LOCAL_STORAGE_KEYS:
        value = os.environ.get(key, "").strip()
        if value:
            config[key] = value
    return resolve_storage(Path(root), config=config, **logical_targets)


def runtime_environment(
    root: Path,
    server_name: str,
    config_path: Optional[Path] = None,
) -> Dict[str, str]:
    """Build the active project overlay for ``code-tiny`` or ``doc-tiny``."""
    config, config_path = (
        load_config_file(config_path) if config_path is not None else load_active_config(root)
    )
    if not config_path:
        return {}

    section_name = "doc" if server_name == "doc-tiny" else "code"
    scoped_provider = "DOC_GRAPH_PROVIDER" if section_name == "doc" else "CODE_GRAPH_PROVIDER"
    section = config.get(section_name) if isinstance(config, dict) else {}
    env = _string_environment(section.get("env") if isinstance(section, dict) else {})

    project = config.get("project") if isinstance(config, dict) else {}
    project = project if isinstance(project, dict) else {}
    project_id = str(project.get("code") or "").strip()
    project_name = str(project.get("name") or project_id).strip()
    if project_id:
        env["PROJECT_ID"] = project_id
        env["CORTEX_STORAGE_PROJECT_ID"] = project_id
    if project_name:
        env["PROJECT_NAME"] = project_name

    provider = isolate_graph_provider_environment(env, scoped_provider)

    if provider == "falkordb":
        explicit_graph = (env.get("FALKORDB_GRAPH") or "").strip()
        graph = (
            (explicit_graph or project_id or "default")
            if section_name == "code"
            else (project_id or "default")
        ).strip()
        default_doc_graph = f"{project_id}_doc" if project_id else "default_doc"
        doc_graph = (
            (explicit_graph or default_doc_graph)
            if section_name == "doc"
            else (env.get("DOC_FALKORDB_GRAPH") or default_doc_graph)
        ).strip()
        code_collection = (env.get("QDRANT_COLLECTION") or project_id or "default").strip()
        doc_collection = (
            env.get("QDRANT_COLLECTION_DOC")
            or (env.get("QDRANT_COLLECTION") if section_name == "doc" else None)
            or (f"{project_id}_doc" if project_id else "default_doc")
        ).strip()
        resolved = resolve_active_storage(
            Path(root), config_path=config_path, code_graph=graph, doc_graph=doc_graph,
            code_collection=code_collection, doc_collection=doc_collection,
        )
        role = StorageRole.DOCUMENT if section_name == "doc" else StorageRole.CODE
        # Replace both endpoint and path fields with the single resolved
        # storage overlay. This prevents stale shell values from winning while
        # preserving an explicit top-level ``storage_backend: remote`` config.
        for key in LOCAL_STORAGE_KEYS | REMOTE_STORAGE_KEYS:
            env.pop(key, None)
        env.update(storage_overlay(resolved, owner=role))
        env["FALKORDB_GRAPH"] = doc_graph if role == StorageRole.DOCUMENT else graph
    isolate_graph_provider_environment(env, scoped_provider)
    env["CORTEX_HARNESS_CONFIG_PATH"] = str(config_path)
    return env


def format_bash_exports(env: Dict[str, str]) -> str:
    lines = [
        f"export {key}={shlex.quote(value)}"
        for key, value in sorted(env.items())
    ]
    provider = str(
        env.get("CODE_GRAPH_PROVIDER")
        or env.get("DOC_GRAPH_PROVIDER")
        or env.get("GRAPH_PROVIDER")
        or "falkordb"
    ).casefold()
    if provider == "falkordb":
        lines.extend(
            (
                'for _cortex_inactive_key in "${!NEO4J_@}"; do unset "$_cortex_inactive_key"; done',
                "unset _cortex_inactive_key 2>/dev/null || true",
            )
        )
    elif provider == "neo4j":
        lines.extend(
            (
                'for _cortex_inactive_key in "${!FALKORDB_@}"; do unset "$_cortex_inactive_key"; done',
                "unset DOC_FALKORDB_GRAPH _cortex_inactive_key 2>/dev/null || true",
            )
        )
    else:
        raise ValueError(f"Unsupported graph provider: {provider}")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--server", choices=("code-tiny", "doc-tiny"), required=True)
    parser.add_argument("--format", choices=("json", "bash"), default="json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    env = runtime_environment(args.root, args.server, args.config)
    if args.format == "bash":
        print(format_bash_exports(env))
    else:
        print(json.dumps(env, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
