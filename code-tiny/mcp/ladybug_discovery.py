"""Shared LadybugDB instance discovery helpers used by every MCP backend.

Mirrors :mod:`mcp.falkordb_discovery`: the MCP backends instantiate one
``LadybugDriver`` against the storage instance derived from the current
working directory and ``CORTEX_STORAGE_INSTANCE``, and by default that
driver sees every Ladybug store under
``<data_home>/v1/instances/*/ladybug/code/*.lbug/*`` so unscoped queries
cover every registered project across instances.

Difference from the FalkorDB discovery: one Ladybug store holds exactly one
named graph, and graphs of the *current* instance must stay writable, so
this helper returns only stores belonging to OTHER instances — the driver
opens those read-only without an application lease, while the current
instance's own graphs keep lazy read-write routing.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional


def _data_root() -> Path:
    data_home_raw = os.environ.get("CORTEX_DATA_HOME")
    if data_home_raw:
        return Path(data_home_raw).expanduser()
    return Path.home() / ".cortext-harness"


def _instance_stores(instance_dir: Path) -> List[Path]:
    owner_root = instance_dir / "ladybug" / "code"
    if not owner_root.is_dir():
        return []
    return sorted(store for store in owner_root.glob("*.lbug/*") if store.is_file())


def discover_ladybug_store_files(
    *,
    current_instance: Optional[str] = None,
    data_home: Optional[Path] = None,
) -> List[Path]:
    """Return sibling LadybugDB stores under the configured data home.

    Siblings are every store under
    ``<root>/v1/instances/<other>/ladybug/code/*.lbug/<graph>`` for instances
    other than ``current_instance`` (default ``CORTEX_STORAGE_INSTANCE``).
    The current instance's own stores are excluded: the driver reaches them
    read-write through named-graph routing instead.
    """
    root = data_home if data_home is not None else _data_root()
    instances_root = root / "v1" / "instances"
    if not instances_root.is_dir():
        return []

    self_id = current_instance if current_instance is not None else os.environ.get(
        "CORTEX_STORAGE_INSTANCE"
    )

    files: List[Path] = []
    for instance_dir in sorted(instances_root.iterdir()):
        if not instance_dir.is_dir():
            continue
        if self_id and instance_dir.name == self_id:
            continue
        files.extend(_instance_stores(instance_dir))
    return files


def build_ladybug_driver_config(graph: Optional[str] = None) -> dict:
    """Return the shared LadybugDB driver boot config used by MCP backends.

    Mirrors the FalkorDB boot path: local store from ``LADYBUG_PATH`` or the
    resolved storage layout, plus read-only sibling stores from every other
    instance under ``CORTEX_DATA_HOME``.
    """
    from cortex_harness.storage import resolve_storage

    return {
        "path": os.environ.get("LADYBUG_PATH")
        or str(resolve_storage(Path.cwd()).ladybug_code_path),
        "graph": graph or os.environ.get("LADYBUG_GRAPH") or "hyper_graph",
        "query_timeout_ms": os.environ.get("LADYBUG_QUERY_TIMEOUT_MS"),
        "owner_id": os.environ.get("CORTEX_STORAGE_OWNER", "code"),
        "instance_id": os.environ.get("CORTEX_STORAGE_INSTANCE", "default"),
        "additional_paths": discover_ladybug_store_files(),
    }


__all__ = ["build_ladybug_driver_config", "discover_ladybug_store_files"]
