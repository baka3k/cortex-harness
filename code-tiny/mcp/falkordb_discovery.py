"""Shared FalkorDB instance discovery helpers used by every MCP backend.

The MCP backends (cplus, android, java) each instantiate ONE
``GraphDriver`` against the storage instance derived from the current
working directory and ``CORTEX_STORAGE_INSTANCE``. That single driver
sees only the graphs stored in its own ``data.rdb``; sibling instances
under ``<data_home>/v1/instances/*/falkordb/code/data.rdb`` are invisible
to introspection tools like :func:`list_databases`.

Per the per-instance MCP lease isolation plan
(``plans/260828-1428-instance-isolated-mcp-locks``), the default boot
path leases only the resolved instance. Cross-instance reads are an
opt-in via :data:`code_tiny.mcp.cross_instance.CROSS_INSTANCE_OPT_IN`.
The shared FalkorDB driver opens sibling paths only when the call site
explicitly passes them, and respects the normal owner lease — sibling
paths already in use by another process are skipped, never bypassed.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional


# Legacy override: when this env var is set to "0", the discovery helper
# returns every sibling's ``data.rdb`` regardless of the per-instance
# filter. The default rollout keeps the legacy behavior off; flipping the
# default happens after Phase 04 gates pass.
LEGACY_INCLUDE_SIBLINGS_ENV = "CORTEX_MCP_SCOPE_LEASES"


def _legacy_include_siblings() -> bool:
    """Return True when the legacy "lease every sibling" mode is forced on.

    Honours ``CORTEX_MCP_SCOPE_LEASES=0`` (any other value, including
    unset, leaves the new behavior in place).
    """
    return os.environ.get(LEGACY_INCLUDE_SIBLINGS_ENV, "").strip() == "0"


def _data_root() -> Path:
    data_home_raw = os.environ.get("CORTEX_DATA_HOME")
    if data_home_raw:
        return Path(data_home_raw).expanduser()
    return Path.home() / ".cortext-harness"


def discover_falkordb_data_files(
    *,
    include_siblings: bool = False,
    exclude_self: bool = True,
    current_instance: Optional[str] = None,
    data_home: Optional[Path] = None,
) -> List[Path]:
    """Return ``data.rdb`` files per the requested visibility scope.

    Parameters
    ----------
    include_siblings:
        When ``False`` (default), the result contains only the current
        instance's ``data.rdb`` — MCP boot paths use this to ensure they
        lease exactly one instance. When ``True``, every sibling under
        ``data_home/v1/instances/*/falkordb/code/data.rdb`` is returned
        subject to ``exclude_self``.
    exclude_self:
        When ``True`` (default) and ``include_siblings`` is ``True``,
        drop the current instance from the list. Ignored when
        ``include_siblings`` is ``False``.
    current_instance:
        Override the current ``CORTEX_STORAGE_INSTANCE``. When unset, the
        environment variable is read.
    data_home:
        Override ``CORTEX_DATA_HOME``. When unset, the environment
        variable is read; if still unset, the default
        ``~/.cortext-harness`` is used.

    The ``CORTEX_MCP_SCOPE_LEASES=0`` escape hatch restores the legacy
    behavior (return every sibling, self included) so operators can roll
    back without code changes.
    """
    if _legacy_include_siblings():
        include_siblings = True
        exclude_self = False

    root = data_home if data_home is not None else _data_root()
    instances_root = root / "v1" / "instances"
    if not instances_root.is_dir():
        return []

    self_id = current_instance if current_instance is not None else os.environ.get(
        "CORTEX_STORAGE_INSTANCE"
    )

    primary: Optional[Path] = None
    if self_id:
        candidate = instances_root / self_id / "falkordb" / "code" / "data.rdb"
        if candidate.is_file():
            primary = candidate

    if not include_siblings:
        return [primary] if primary is not None else []

    files: List[Path] = []
    for instance_dir in sorted(instances_root.iterdir()):
        if not instance_dir.is_dir():
            continue
        candidate = instance_dir / "falkordb" / "code" / "data.rdb"
        if not candidate.is_file():
            continue
        if exclude_self and primary is not None and candidate == primary:
            continue
        files.append(candidate)
    return files
