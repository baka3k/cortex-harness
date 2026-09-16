"""Shared LadybugDB instance discovery helpers used by every MCP backend.

Mirrors :mod:`falkordb_discovery` for the embedded LadybugDB provider: the
backends open one catalog per ``.lbdb`` file, and the driver opens sibling
instance files read-only so unscoped queries can fan out across every
registered project instance under ``CORTEX_DATA_HOME``.
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


def discover_ladybug_databases(
    *,
    include_siblings: bool = True,
    exclude_self: bool = False,
    current_instance: Optional[str] = None,
    data_home: Optional[Path] = None,
    owner: str = "code",
) -> List[Path]:
    """Return every ``data.lbdb`` under the configured ``CORTEX_DATA_HOME``.

    See :func:`falkordb_discovery.discover_falkordb_data_files` for the
    argument contract; the only differences are the ``data.lbdb`` file name,
    the ``ladybug`` directory, and the ``owner`` selector (``code``/``doc``).
    """

    root = data_home if data_home is not None else _data_root()
    instances_root = root / "v1" / "instances"
    if not instances_root.is_dir():
        return []

    self_id = current_instance if current_instance is not None else os.environ.get(
        "CORTEX_STORAGE_INSTANCE"
    )

    primary: Optional[Path] = None
    if self_id:
        candidate = instances_root / self_id / "ladybug" / owner / "data.lbdb"
        if candidate.is_file():
            primary = candidate

    if not include_siblings:
        return [primary] if primary is not None else []

    files: List[Path] = []
    for instance_dir in sorted(instances_root.iterdir()):
        if not instance_dir.is_dir():
            continue
        candidate = instance_dir / "ladybug" / owner / "data.lbdb"
        if not candidate.is_file():
            continue
        if exclude_self and primary is not None and candidate == primary:
            continue
        files.append(candidate)
    return files


__all__ = ["discover_ladybug_databases"]
