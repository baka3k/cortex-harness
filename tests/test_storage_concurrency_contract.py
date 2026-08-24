"""Focused contracts for the embedded single-owner concurrency boundary."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest

from cortex_harness.storage import (
    GatewayErrorCode,
    GatewayLimits,
    GenerationManager,
    PerformanceProfile,
    PhysicalTargetKey,
    StoreGateway,
    StoreGatewayError,
    resolve_performance_profile,
)
from cortex_harness.storage.admission import BoundedLane, LaneLimits


def _target(tmp_path: Path) -> PhysicalTargetKey:
    return PhysicalTargetKey.from_paths(
        instance_id="default",
        owner_id="code",
        graph_path=tmp_path / "graph" / "data.rdb",
        vector_path=tmp_path / "vectors",
    )


def test_physical_target_key_uses_canonical_paths_not_logical_project(tmp_path: Path):
    target = _target(tmp_path)

    assert target.graph_path == str((tmp_path / "graph" / "data.rdb").resolve())
    assert target.vector_path == str((tmp_path / "vectors").resolve())
    assert target.canonical_paths == tuple(sorted(target.canonical_paths))


def test_performance_profile_keeps_one_embedded_writer():
    assert PerformanceProfile().writer_slots == 1
    with pytest.raises(ValueError, match="exactly one writer"):
        PerformanceProfile(name="custom", writer_slots=2)


def test_custom_profile_validation_comes_from_one_resolver():
    profile = resolve_performance_profile(
        config={
            "CORTEX_STORAGE_PROFILE": "custom",
            "CORTEX_STORAGE_MAX_QUEUE_ITEMS": "7",
            "CORTEX_STORAGE_WRITER_SLOTS": "1",
        }
    )

    assert profile.name == "custom"
    assert profile.max_queue_items == 7
    assert GatewayLimits.from_profile(profile).graph_read.max_queue_items == 7


def test_publish_is_atomic_selection_boundary_and_reader_pin_blocks_retirement(tmp_path: Path):
    manager = GenerationManager(tmp_path / "owner", _target(tmp_path))
    first = manager.allocate("rev-1", generation_id="generation-1")
    first = manager.publish(first, lambda manifest: None)

    assert manager.load_active() == first
    with manager.pin_active() as pinned:
        assert pinned.generation_id == "generation-1"
        assert manager.reference_count("generation-1") == 1
        assert manager.retire(pinned) is False
    assert manager.reference_count("generation-1") == 0

    second = manager.publish(manager.allocate("rev-2", generation_id="generation-2"), lambda manifest: None)
    assert manager.load_active() == second
    assert manager.retire(first) is True


def test_legacy_clang_structural_generation_marker_fails_closed(tmp_path: Path):
    manager = GenerationManager(tmp_path / "owner", _target(tmp_path))
    active = manager.publish(
        manager.allocate("rev-legacy", generation_id="generation-legacy"),
        lambda manifest: None,
    )

    marker = manager.mark_incompatible(
        active.generation_id,
        reason="legacy LIBCLANG structural provenance cannot be proven compatible",
    )

    assert marker.is_file()
    with pytest.raises(ValueError, match="structurally incompatible"):
        manager.load_active()
    with pytest.raises(ValueError, match="structurally incompatible"):
        manager.publish(active, lambda manifest: None)


def test_bounded_lane_returns_structured_overload_without_unbounded_queue():
    async def exercise() -> None:
        lane = BoundedLane("graph", LaneLimits(concurrency=1, max_queue_items=1, max_queue_bytes=10))
        release = asyncio.Event()

        async def blocked() -> str:
            await release.wait()
            return "done"

        first = asyncio.create_task(lane.run(blocked))
        await asyncio.sleep(0)
        second = asyncio.create_task(lane.run(blocked))
        await asyncio.sleep(0)
        with pytest.raises(StoreGatewayError) as error:
            await lane.run(blocked)
        assert error.value.code is GatewayErrorCode.OVERLOADED
        release.set()
        assert await first == "done"
        assert await second == "done"

    asyncio.run(exercise())


def test_gateway_serves_a_pinned_manifest_and_deduplicates_jobs(tmp_path: Path):
    async def exercise() -> None:
        target = _target(tmp_path)
        gateway = StoreGateway(target, str(tmp_path / "owner"))
        await gateway.start()
        try:
            active = await gateway.publish(
                gateway.generations.allocate("rev-1", generation_id="generation-1"), lambda manifest: None
            )
            value, freshness = await gateway.query(
                lambda manifest: (manifest.generation_id, threading.current_thread().name)
            )
            assert value[0] == active.generation_id
            assert value[1].startswith("cortex-graph-read")
            assert freshness.source_revision == "rev-1"

            first = await gateway.submit_ingest(idempotency_key="request-1", source_revision="rev-2")
            duplicate = await gateway.submit_ingest(idempotency_key="request-1", source_revision="rev-2")
            assert duplicate.job_id == first.job_id
        finally:
            await gateway.close()

    asyncio.run(exercise())
