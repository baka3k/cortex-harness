"""
workflow_service.py — MCP service wrapper for find_screen_workflows.

Provides the tool-level entrypoint that unified_mcp registers. Reuses the
shared Neo4j driver from whichever backend the caller is using (cplus/android)
by delegating driver acquisition to a small callable passed at init time.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


DriverProvider = Callable[[], Awaitable[Any]]


async def run_find_screen_workflows(
    driver_provider: DriverProvider,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Dispatch entrypoint invoked by unified_mcp.

    Imports the core tool lazily to keep module import cheap (the tool pulls
    in logging/typing only, but we preserve the pattern used elsewhere).
    """

    from tools.ts.workflow_finder import find_screen_workflows  # lazy import

    project_id = (payload.get("project_id") or "").strip()
    node_a = (payload.get("node_a") or payload.get("source") or "").strip()
    node_b_raw = payload.get("node_b") or payload.get("target")
    node_b = node_b_raw.strip() if isinstance(node_b_raw, str) and node_b_raw.strip() else None
    direction = (payload.get("direction") or "bidirectional").strip().lower()
    database = (payload.get("db") or payload.get("database") or "neo4j").strip() or "neo4j"

    def _int(key: str, default: int) -> int:
        val = payload.get(key)
        if val in (None, ""):
            return default
        try:
            return int(val)
        except (TypeError, ValueError):
            return default

    def _bool(key: str) -> bool:
        val = payload.get(key)
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.strip().lower() in {"1", "true", "yes", "y"}
        return False

    max_hops = _int("max_hops", 8)
    max_paths = _int("max_paths", 100)
    include_entry_function = _bool("include_entry_function")
    include_api_calls = _bool("include_api_calls")

    if not project_id:
        raise ValueError("project_id is required")
    if not node_a:
        raise ValueError("node_a is required (bare name or symbol_id)")

    driver = await driver_provider()
    return await find_screen_workflows(
        driver,
        database,
        project_id=project_id,
        node_a=node_a,
        node_b=node_b,
        direction=direction,
        max_hops=max_hops,
        max_paths=max_paths,
        include_entry_function=include_entry_function,
        include_api_calls=include_api_calls,
    )


__all__ = ["run_find_screen_workflows"]
