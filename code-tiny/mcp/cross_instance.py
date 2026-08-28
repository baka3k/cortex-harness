"""Cross-instance query gate for code MCP backends.

The per-instance MCP lease isolation plan
(``plans/260828-1428-instance-isolated-mcp-locks``) closes the default
lease surface of every MCP backend to a single
``CORTEX_STORAGE_INSTANCE``. A small number of tools legitimately need
to query graphs that live under sibling instances; this module gates
that capability behind two conditions:

1. The operator has set ``CROSS_INSTANCE_QUERY=1`` in the MCP process
   environment (opt-in at the process level — defaults to off so a
   fresh deployment cannot leak reads across instances).
2. The tool name is on :data:`ALLOWLIST`. Adding a tool here is a
   deliberate audit decision; keep the list small.

Backends that want to use sibling paths consult
:func:`sibling_paths_if_allowed` rather than calling
:func:`code_tiny.mcp.falkordb_discovery.discover_falkordb_data_files`
directly. The helper returns ``[]`` (not an error) when the gate is
closed, so callers can pass the result through unchanged and get a
self-only answer instead of a hard failure.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import FrozenSet

from falkordb_discovery import discover_falkordb_data_files


CROSS_INSTANCE_OPT_IN_ENV = "CROSS_INSTANCE_QUERY"

# Tool names that may read sibling instances when ``CROSS_INSTANCE_QUERY=1``
# is set. Each entry must justify why a self-only read is insufficient.
ALLOWLIST: FrozenSet[str] = frozenset(
    {
        # Cross-project impact analysis reads the call graph of a target
        # that may live in a sibling instance — without it the analysis
        # returns incomplete graphs.
        "analyze_workflow_impact",
        # Admin/global search across every registered project regardless
        # of instance — used by ops tooling to enumerate graphs.
        "explore_graph",
    }
)


def enabled() -> bool:
    """Return ``True`` when the process-level cross-instance gate is open."""
    return os.environ.get(CROSS_INSTANCE_OPT_IN_ENV, "").strip() == "1"


def is_allowed(tool_name: str) -> bool:
    """Return ``True`` only when both the gate and the allowlist permit it."""
    return enabled() and tool_name in ALLOWLIST


def sibling_paths_if_allowed(tool_name: str) -> list[Path]:
    """Return sibling paths when allowed, else an empty list.

    The helper never raises: when the gate is closed or the tool is not
    allowlisted, the result is ``[]`` so the caller's ``additional_paths``
    config degrades cleanly to self-only behavior.
    """
    if not is_allowed(tool_name):
        return []
    return discover_falkordb_data_files(
        include_siblings=True,
        exclude_self=True,
    )


def self_and_allowed_siblings_paths(tool_name: str) -> list[Path]:
    """Return the current instance plus any allowed sibling paths.

    Used by service-level tools (impact analyzer, explore service) that
    must always see their own graphs and may see siblings when the gate
    is open. The primary ``path`` in the driver config is the current
    instance; this helper is for the ``additional_paths`` slot.
    """
    siblings = sibling_paths_if_allowed(tool_name)
    self_only = discover_falkordb_data_files()  # self only by default
    return [*self_only, *siblings]
