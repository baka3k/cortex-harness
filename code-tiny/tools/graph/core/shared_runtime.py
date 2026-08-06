"""Process-local graph-driver cache shared by MCP backend modules.

Embedded FalkorDB owns an exclusive lease for its physical path. A unified
MCP process imports several backend modules, so per-module caches are not
sufficient: they would each try to open the same path. This cache deliberately
ignores the logical graph name in its FalkorDB key because one driver can
select any graph per query.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, Hashable, Tuple

from tools.graph.core.factory import GraphDriverFactory
from tools.graph.core.base import GraphDriver, GraphProvider


_drivers: Dict[Tuple[Hashable, ...], GraphDriver] = {}
_creation_tasks: Dict[Tuple[Hashable, ...], "asyncio.Task[GraphDriver]"] = {}


def _driver_key(
    provider: GraphProvider, config: Dict[str, Any]
) -> Tuple[Hashable, ...]:
    if provider == GraphProvider.FALKORDB:
        raw_path = config.get("path")
        path = str(Path(raw_path).resolve()) if raw_path else ""
        return (
            provider.value,
            path,
            str(config.get("instance_id") or "default"),
            str(config.get("owner_id") or "code"),
        )
    return (
        provider.value,
        str(config.get("uri") or ""),
        str(config.get("user") or ""),
        str(config.get("password") or ""),
    )


async def get_shared_graph_driver(
    provider: GraphProvider, config: Dict[str, Any]
) -> GraphDriver:
    """Return one driver per physical provider target in this process."""
    key = _driver_key(provider, config)
    existing = _drivers.get(key)
    if existing is not None:
        return existing

    task = _creation_tasks.get(key)
    if task is None:
        task = asyncio.create_task(
            GraphDriverFactory.create_driver(provider, config)
        )
        _creation_tasks[key] = task
    try:
        driver = await task
    finally:
        if task.done():
            _creation_tasks.pop(key, None)
    _drivers.setdefault(key, driver)
    return _drivers[key]


def reset_shared_graph_drivers(*, close: bool = False) -> None:
    """Clear the cache; intended for deterministic tests and process teardown."""
    drivers = list(dict.fromkeys(_drivers.values()))
    _drivers.clear()
    _creation_tasks.clear()
    if close:
        for driver in drivers:
            driver.close()


__all__ = ["get_shared_graph_driver", "reset_shared_graph_drivers"]
