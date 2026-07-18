#!/usr/bin/env python3
"""Resolve the active Cortex Harness environment for an MCP server.

Precedence is intentionally handled by the lifecycle launchers:
defaults < service-local ``.env`` < active harness environment.
This module only returns the final active-project overlay.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
from pathlib import Path
from typing import Dict, Optional, Tuple


CONFIG_DIR = Path(".cortext-harness") / "config"
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def load_active_config(root: Path) -> Tuple[dict, Optional[Path]]:
    """Return the active harness config, falling back to the first config."""
    config_dir = Path(root).resolve() / CONFIG_DIR
    configs = sorted(config_dir.glob("*.json")) if config_dir.is_dir() else []
    if not configs:
        return {}, None

    first: Optional[Tuple[dict, Path]] = None
    for path in configs:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
        if not isinstance(payload, dict):
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


def _qdrant_url(env: Dict[str, str]) -> Optional[str]:
    configured = env.get("QDRANT_URL", "").strip()
    if configured:
        return configured
    host = env.get("QDRANT_HOST", "").strip()
    if not host:
        return None
    port = env.get("QDRANT_PORT", "6333").strip() or "6333"
    if host.startswith(("http://", "https://")):
        return f"{host.rstrip('/')}:{port}"
    return f"http://{host}:{port}"


def runtime_environment(root: Path, server_name: str) -> Dict[str, str]:
    """Build the active project overlay for ``code-tiny`` or ``doc-tiny``."""
    config, config_path = load_active_config(root)
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
    if project_name:
        env["PROJECT_NAME"] = project_name

    provider = (
        env.get(scoped_provider)
        or env.get("GRAPH_PROVIDER")
        or ("falkordb" if env.get("NEO4J_URI", "").startswith(("redis://", "rediss://")) else "neo4j")
    ).strip().lower()
    provider = "falkordb" if provider in {"falkor", "falkordb"} else "neo4j"
    env["GRAPH_PROVIDER"] = provider
    env[scoped_provider] = provider

    if provider == "falkordb":
        host = env.get("FALKORDB_HOST", "localhost").strip() or "localhost"
        port = env.get("FALKORDB_PORT", "6379").strip() or "6379"
        uri = env.get("FALKORDB_URI", "").strip() or f"redis://{host}:{port}"
        graph = (
            env.get("FALKORDB_GRAPH")
            or env.get("NEO4J_DB")
            or project_id
            or "neo4j"
        ).strip()
        env.update(
            {
                "FALKORDB_HOST": host,
                "FALKORDB_PORT": port,
                "FALKORDB_URI": uri,
                "FALKORDB_GRAPH": graph,
                # Compatibility aliases are normalized so legacy code cannot
                # silently route to a different graph/database.
                "NEO4J_URI": uri,
                "NEO4J_DB": graph,
            }
        )

    qdrant_url = _qdrant_url(env)
    if qdrant_url:
        env["QDRANT_URL"] = qdrant_url
    env["CORTEX_HARNESS_CONFIG_PATH"] = str(config_path)
    return env


def format_bash_exports(env: Dict[str, str]) -> str:
    return "\n".join(
        f"export {key}={shlex.quote(value)}"
        for key, value in sorted(env.items())
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--server", choices=("code-tiny", "doc-tiny"), required=True)
    parser.add_argument("--format", choices=("json", "bash"), default="json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    env = runtime_environment(args.root, args.server)
    if args.format == "bash":
        print(format_bash_exports(env))
    else:
        print(json.dumps(env, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
