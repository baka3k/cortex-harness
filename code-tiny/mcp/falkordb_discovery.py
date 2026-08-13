"""Shared FalkorDB instance discovery helpers used by every MCP backend.

The MCP backends (cplus, android, java) each instantiate ONE
``GraphDriver`` against the storage instance derived from the current
working directory and ``CORTEX_STORAGE_INSTANCE``. That single driver
sees only the graphs stored in its own ``data.rdb``; sibling instances
under ``<data_home>/v1/instances/*/falkordb/code/data.rdb`` are invisible
to introspection tools like :func:`list_databases`.

The helper here discovers physical stores only. The shared FalkorDB driver
opens them under the normal owner lease and routes each logical graph to the
client that owns its file.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List


def discover_falkordb_data_files() -> List[Path]:
    """Return every ``data.rdb`` under ``<data_home>/v1/instances/*/falkordb/code``.

    Honours ``CORTEX_DATA_HOME`` so a relocated account home is still
    scanned. The FalkorDB driver acquires the normal owner lease before it
    opens any returned sibling path.
    """
    data_home_raw = os.environ.get("CORTEX_DATA_HOME")
    if data_home_raw:
        data_root = Path(data_home_raw).expanduser()
    else:
        data_root = Path.home() / ".cortext-harness"
    instances_root = data_root / "v1" / "instances"
    if not instances_root.is_dir():
        return []
    files: List[Path] = []
    for instance_dir in sorted(instances_root.iterdir()):
        if not instance_dir.is_dir():
            continue
        candidate = instance_dir / "falkordb" / "code" / "data.rdb"
        if not candidate.is_file():
            continue
        files.append(candidate)
    return files
