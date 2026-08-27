"""Connectivity probe for remote storage backends.

Shared by ``infra-up``, ``infra-down``, and ``doctor`` to validate
remote Qdrant and FalkorDB server reachability without duplicating
connection logic. Provisioning helpers also live here so ``infra-up
--provision`` reuses the same client construction as health checks.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import RemoteStorageConfig
from .targets import environment_flag_enabled


ENV_FORCE_LOCAL = "CORTEX_STORAGE_BACKEND_FORCE_LOCAL"


def force_local_active() -> bool:
    """Return True when the operator override forces local backends."""
    return environment_flag_enabled(os.getenv(ENV_FORCE_LOCAL))


@dataclass(frozen=True)
class ProbeResult:
    """Outcome of a single remote backend probe."""

    backend: str          # "qdrant" or "falkordb"
    url: str
    reachable: bool
    message: str
    cause: Optional[BaseException] = None


@dataclass(frozen=True)
class ProvisionResult:
    """Outcome of provisioning a single resource."""

    resource: str       # e.g. "qdrant:my_project_code"
    action: str         # "created", "exists", "skipped", "failed"
    message: str
    cause: Optional[BaseException] = None


def probe_qdrant(config: RemoteStorageConfig) -> ProbeResult:
    """Check Qdrant server reachability from a RemoteStorageConfig."""
    if not config.qdrant_url:
        return ProbeResult("qdrant", "(not configured)", True, "skipped — no qdrant_url")
    try:
        from .qdrant_remote import get_remote_client

        client = get_remote_client(config.qdrant_url, api_key=config.qdrant_api_key)
        client.get_collections()
        return ProbeResult("qdrant", config.qdrant_url, True, "reachable")
    except Exception as exc:
        return ProbeResult("qdrant", config.qdrant_url, False, str(exc), cause=exc)


def probe_falkordb(config: RemoteStorageConfig) -> ProbeResult:
    """Check FalkorDB server reachability from a RemoteStorageConfig."""
    if not config.falkordb_uri:
        return ProbeResult("falkordb", "(not configured)", True, "skipped — no falkordb_uri")
    try:
        from tools.graph.driver.falkordb_driver import FalkorDBDriver

        driver = FalkorDBDriver(
            uri=config.falkordb_uri,
            password=config.falkordb_password,
            ssl=config.falkordb_ssl,
            graph="__probe__",
            _suppress_deprecation=True,
        )
        # Lifecycle probes are synchronous. Calling the async execute_query()
        # without awaiting it reports a false positive and emits an un-awaited
        # coroutine warning; use the driver's explicit synchronous boundary.
        driver.execute_query_sync("RETURN 1 AS ok")
        return ProbeResult("falkordb", config.falkordb_uri, True, "reachable")
    except Exception as exc:
        return ProbeResult("falkordb", config.falkordb_uri, False, str(exc), cause=exc)


def probe_all(config: RemoteStorageConfig) -> list[ProbeResult]:
    """Probe both backends and return combined results."""
    return [probe_qdrant(config), probe_falkordb(config)]


def provision_qdrant_collection(
    config: RemoteStorageConfig,
    collection_name: str,
    *,
    vector_size: int = 384,
    distance: str = "COSINE",
) -> ProvisionResult:
    """Create a Qdrant collection on the remote server if it doesn't exist."""
    if not config.qdrant_url:
        return ProvisionResult(
            f"qdrant:{collection_name}", "skipped", "no qdrant_url configured"
        )
    try:
        from qdrant_client.http import models as qmodels

        from .qdrant_remote import get_remote_client

        client = get_remote_client(config.qdrant_url, api_key=config.qdrant_api_key)
        distance_enum = getattr(qmodels.Distance, distance.upper())

        if client.collection_exists(collection_name=collection_name):
            return ProvisionResult(
                f"qdrant:{collection_name}", "exists",
                f"collection '{collection_name}' already exists",
            )

        client.create_collection(
            collection_name=collection_name,
            vectors_config=qmodels.VectorParams(
                size=vector_size, distance=distance_enum
            ),
        )
        return ProvisionResult(
            f"qdrant:{collection_name}", "created",
            f"collection '{collection_name}' created (dim={vector_size})",
        )
    except Exception as exc:
        return ProvisionResult(
            f"qdrant:{collection_name}", "failed", str(exc), cause=exc
        )


def provision_falkordb_graph(
    config: RemoteStorageConfig,
    graph_name: str,
) -> ProvisionResult:
    """Ensure a FalkorDB graph exists and is queryable on the remote server."""
    if not config.falkordb_uri:
        return ProvisionResult(
            f"falkordb:{graph_name}", "skipped", "no falkordb_uri configured"
        )
    try:
        from tools.graph.driver.falkordb_driver import FalkorDBDriver

        driver = FalkorDBDriver(
            uri=config.falkordb_uri,
            password=config.falkordb_password,
            ssl=config.falkordb_ssl,
            graph=graph_name,
            _suppress_deprecation=True,
        )
        # FalkorDB auto-creates graphs on first query.
        driver.execute_query_sync("RETURN 1 AS ok")
        return ProvisionResult(
            f"falkordb:{graph_name}", "exists",
            f"graph '{graph_name}' is accessible",
        )
    except Exception as exc:
        return ProvisionResult(
            f"falkordb:{graph_name}", "failed", str(exc), cause=exc
        )


def setup_remote_falkordb_schema(
    config: RemoteStorageConfig,
    graph_name: str,
    *,
    python: Optional[str] = None,
    setup_script: Optional[Path] = None,
    timeout: float = 120.0,
) -> ProvisionResult:
    """Run schema setup against remote FalkorDB (idempotent).

    Delegates to ``code-tiny/scripts/setup_constraints.py`` via subprocess to
    keep import coupling off the lifecycle path. ``python`` defaults to
    ``sys.executable``; ``setup_script`` defaults to the in-repo path.
    """
    if not config.falkordb_uri:
        return ProvisionResult(
            f"falkordb:{graph_name}:schema", "skipped", "no falkordb_uri"
        )
    interpreter = python or _default_python()
    script_path = setup_script or _default_setup_script()
    if script_path is None or not Path(script_path).is_file():
        return ProvisionResult(
            f"falkordb:{graph_name}:schema", "skipped",
            "setup_constraints.py not found",
        )
    arguments: list[str] = [
        interpreter,
        str(script_path),
        "--graph-provider", "falkordb",
        "--falkordb-uri", config.falkordb_uri,
        "--falkordb-graph", graph_name,
    ]
    if config.falkordb_password:
        arguments += ["--falkordb-password", config.falkordb_password]
    try:
        result = subprocess.run(
            arguments,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return ProvisionResult(
            f"falkordb:{graph_name}:schema", "failed",
            f"setup_constraints timed out after {timeout}s",
            cause=exc,
        )
    except Exception as exc:
        return ProvisionResult(
            f"falkordb:{graph_name}:schema", "failed", str(exc), cause=exc
        )
    if result.returncode == 0:
        return ProvisionResult(
            f"falkordb:{graph_name}:schema", "created",
            "constraints and indexes ensured",
        )
    message = (result.stderr or result.stdout or "").strip() or f"exit {result.returncode}"
    return ProvisionResult(
        f"falkordb:{graph_name}:schema", "failed", message,
    )


def _default_python() -> str:
    import sys

    return sys.executable


def _default_setup_script() -> Optional[Path]:
    candidate = Path(__file__).resolve().parents[2] / "code-tiny" / "scripts" / "setup_constraints.py"
    return candidate if candidate.is_file() else None


# Tag map shared by lifecycle callers when rendering provision output.
PROVISION_TAGS: dict[str, str] = {
    "created": "[new]",
    "exists": "[ok]",
    "skipped": "[skip]",
    "failed": "[fail]",
}


def render_provision_line(result: ProvisionResult) -> str:
    """Format a :class:`ProvisionResult` for CLI output."""
    tag = PROVISION_TAGS.get(result.action, "[?]")
    return f"{tag} {result.resource}: {result.message}"


__all__ = [
    "ENV_FORCE_LOCAL",
    "ProbeResult",
    "ProvisionResult",
    "PROVISION_TAGS",
    "force_local_active",
    "probe_all",
    "probe_falkordb",
    "probe_qdrant",
    "provision_falkordb_graph",
    "provision_qdrant_collection",
    "render_provision_line",
    "setup_remote_falkordb_schema",
]
