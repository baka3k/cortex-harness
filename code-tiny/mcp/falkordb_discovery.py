"""Shared FalkorDB instance discovery helpers used by every MCP backend.

The MCP backends (cplus, android, java, fastmcp) each instantiate one
``GraphDriver`` against the storage instance derived from the current
working directory and ``CORTEX_STORAGE_INSTANCE``. By default that driver
sees every ``data.rdb`` under ``<data_home>/v1/instances/*/falkordb/code``,
so unscoped queries (e.g. ``_list_databases`` followed by fan-out via
``_run_cypher_first``) cover every registered project across instances.

The driver's primary lease protects only the path passed as ``path``;
siblings are opened read-only without acquiring an application lease, so
concurrent ingests on those instances are not blocked by the read fan-out.
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


def discover_falkordb_data_files(
    *,
    include_siblings: bool = True,
    exclude_self: bool = False,
    current_instance: Optional[str] = None,
    data_home: Optional[Path] = None,
) -> List[Path]:
    """Return every ``data.rdb`` under the configured ``CORTEX_DATA_HOME``.

    Parameters
    ----------
    include_siblings:
        When ``True`` (default), every instance's ``data.rdb`` under
        ``data_home/v1/instances/*/falkordb/code/data.rdb`` is returned
        subject to ``exclude_self``. When ``False``, only the current
        instance's file is returned (if it exists).
    exclude_self:
        When ``True`` and ``include_siblings`` is ``True``, drop the
        current instance from the list. Ignored when ``include_siblings``
        is ``False``. Defaults to ``False`` so the result is "every
        instance, including self".
    current_instance:
        Override the current ``CORTEX_STORAGE_INSTANCE``. When unset, the
        environment variable is read.
    data_home:
        Override ``CORTEX_DATA_HOME``. When unset, the environment
        variable is read; if still unset, the default
        ``~/.cortext-harness`` is used.

    Examples
    --------
    Boot-path callers use the default args and receive every ``data.rdb``
    (self + siblings)::

        paths = discover_falkordb_data_files()
        # [Path("~/.cortext-harness/v1/instances/alpha/.../data.rdb"),
        #  Path("~/.cortext-harness/v1/instances/beta/.../data.rdb"), ...]

    To get only siblings (drop self)::

        discover_falkordb_data_files(exclude_self=True)
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
