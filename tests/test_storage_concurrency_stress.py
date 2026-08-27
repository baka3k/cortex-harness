"""Small deterministic mixed-load gates for bounded embedded storage."""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest

from cortex_harness.storage import (
    GatewayLimits,
    GenerationManager,
    PhysicalTargetKey,
    StoreGateway,
)
from cortex_harness.storage.admission import LaneLimits


def _target(root: Path) -> PhysicalTargetKey:
    return PhysicalTargetKey.from_paths(
        instance_id="stress",
        owner_id="code",
        graph_path=root / "graph.rdb",
        vector_path=root / "vectors",
    )


@pytest.mark.asyncio
async def test_query_burst_never_exceeds_safe_handle_concurrency(
    tmp_path: Path,
) -> None:
    limits = GatewayLimits(
        graph_read=LaneLimits(concurrency=1, max_queue_items=32),
        vector_read=LaneLimits(concurrency=1, max_queue_items=32),
    )
    gateway = StoreGateway(_target(tmp_path), str(tmp_path / "generations"), limits=limits)
    await gateway.start()
    await gateway.publish(
        gateway.generations.allocate("revision-1", generation_id="generation-1"),
        lambda _: None,
    )
    state_lock = threading.Lock()
    active = 0
    peak = 0

    def query(_manifest) -> int:
        nonlocal active, peak
        with state_lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.005)
        with state_lock:
            active -= 1
        return 1

    results = await asyncio.gather(*(gateway.query(query) for _ in range(32)))

    assert sum(value for (value, _freshness) in results) == 32
    assert peak == 1
    metrics = gateway.metrics()["lanes"]["graph"]
    assert metrics["accepted"] == 32
    assert metrics["completed"] == 32
    assert metrics["max_queue_wait_seconds"] > 0
    await gateway.close()


@pytest.mark.asyncio
async def test_generation_swap_never_changes_an_existing_query_pin(
    tmp_path: Path,
) -> None:
    manager = GenerationManager(tmp_path / "generations", _target(tmp_path))
    gateway = StoreGateway(
        manager.target,
        str(manager.root),
        generation_manager=manager,
    )
    entered = threading.Event()
    release = threading.Event()
    await gateway.start()
    first = await gateway.publish(
        manager.allocate("revision-1", generation_id="generation-1"),
        lambda _: None,
    )

    def pinned(manifest) -> str:
        entered.set()
        release.wait(timeout=5)
        return manifest.generation_id

    old_query = asyncio.create_task(gateway.query(pinned))
    for _ in range(100):
        if entered.is_set():
            break
        await asyncio.sleep(0.01)
    second = await gateway.publish(
        manager.allocate("revision-2", generation_id="generation-2"),
        lambda _: None,
    )
    new_value, new_freshness = await gateway.query(lambda manifest: manifest.generation_id)
    release.set()
    old_value, old_freshness = await old_query

    assert (old_value, old_freshness.served_generation) == (
        first.generation_id,
        first.generation_id,
    )
    assert (new_value, new_freshness.served_generation) == (
        second.generation_id,
        second.generation_id,
    )
    await gateway.close()

