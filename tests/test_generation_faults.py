"""Fault and recovery coverage for the embedded generation owner."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest

from cortex_harness.storage import (
    GatewayErrorCode,
    GenerationManager,
    IngestionJobState,
    PhysicalTargetKey,
    StoreGateway,
    StoreGatewayError,
)


def _target(root: Path) -> PhysicalTargetKey:
    return PhysicalTargetKey.from_paths(
        instance_id="faults",
        owner_id="code",
        graph_path=root / "owner-graph.rdb",
        vector_path=root / "owner-vectors",
    )


@pytest.mark.asyncio
async def test_cancelled_query_keeps_pin_until_sync_call_really_returns(
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

    def blocked(_manifest) -> str:
        entered.set()
        release.wait(timeout=5)
        return "finished"

    task = asyncio.create_task(gateway.query(blocked))
    for _ in range(100):
        if entered.is_set():
            break
        await asyncio.sleep(0.01)
    assert entered.is_set()
    task.cancel()
    await asyncio.sleep(0.02)

    await gateway.publish(
        manager.allocate("revision-2", generation_id="generation-2"),
        lambda _: None,
    )
    assert manager.reference_count(first.generation_id) == 1
    assert await gateway.retire(first) is False

    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert manager.reference_count(first.generation_id) == 0
    assert await gateway.retire(first) is True
    await gateway.close()


@pytest.mark.asyncio
async def test_running_job_is_recovered_as_ambiguous_and_deduplicated(
    tmp_path: Path,
) -> None:
    root = tmp_path / "generations"
    target = _target(tmp_path)
    first = StoreGateway(target, str(root))
    await first.start()
    job = await first.submit_ingest(
        idempotency_key="request-1",
        source_revision="revision-1",
        estimated_bytes=12,
    )
    await first.update_job(job.job_id, IngestionJobState.WRITING)
    await first.close()

    second = StoreGateway(target, str(root))
    await second.start()
    recovered = await second.get_ingestion_status(job.job_id)
    assert recovered is not None
    assert recovered.state is IngestionJobState.AMBIGUOUS
    assert recovered.detail["recovery"] == "owner_restarted_during_store_operation"
    duplicate = await second.submit_ingest(
        idempotency_key="request-1",
        source_revision="revision-1",
    )
    assert duplicate.job_id == job.job_id
    await second.close()


@pytest.mark.asyncio
async def test_queued_job_cancel_and_bounded_wait_are_truthful(tmp_path: Path) -> None:
    gateway = StoreGateway(_target(tmp_path), str(tmp_path / "generations"))
    await gateway.start()
    job = await gateway.submit_ingest(
        idempotency_key="request-1", source_revision="revision-1"
    )

    pending = await gateway.wait_for_ingestion(job.job_id, timeout_seconds=0.01)
    assert pending is not None
    assert pending.state is IngestionJobState.QUEUED

    cancelled = await gateway.cancel_ingest(job.job_id)
    assert cancelled is not None
    assert cancelled.state is IngestionJobState.CANCELLED
    assert cancelled.cancel_requested_at is not None
    assert await gateway.wait_for_ingestion(job.job_id, timeout_seconds=0.1) == cancelled
    await gateway.close()


@pytest.mark.asyncio
async def test_job_state_machine_rejects_an_invalid_publish_jump(tmp_path: Path) -> None:
    gateway = StoreGateway(_target(tmp_path), str(tmp_path / "generations"))
    await gateway.start()
    job = await gateway.submit_ingest(
        idempotency_key="request-1", source_revision="revision-1"
    )

    with pytest.raises(ValueError, match="QUEUED -> PUBLISHING"):
        await gateway.update_job(job.job_id, IngestionJobState.PUBLISHING)

    status = await gateway.get_ingestion_status(job.job_id)
    assert status is not None
    assert status.state is IngestionJobState.QUEUED
    await gateway.close()


@pytest.mark.asyncio
async def test_drain_timeout_keeps_owner_resources_until_sync_call_returns(
    tmp_path: Path,
) -> None:
    gateway = StoreGateway(_target(tmp_path), str(tmp_path / "generations"))
    entered = threading.Event()
    release = threading.Event()
    await gateway.start()
    await gateway.publish(
        gateway.generations.allocate("revision-1", generation_id="generation-1"),
        lambda _: None,
    )

    def blocked(_manifest) -> None:
        entered.set()
        release.wait(timeout=5)

    query = asyncio.create_task(gateway.query(blocked))
    for _ in range(100):
        if entered.is_set():
            break
        await asyncio.sleep(0.01)
    with pytest.raises(StoreGatewayError) as raised:
        await gateway.close(timeout_seconds=0.01)
    assert raised.value.code is GatewayErrorCode.DEADLINE_EXCEEDED
    assert gateway.lifecycle.value == "DRAINING"
    assert gateway._leases

    release.set()
    await query
    await gateway.close(timeout_seconds=1)
    assert not gateway._leases


@pytest.mark.asyncio
async def test_read_deadline_covers_sync_execution_and_keeps_correlation(
    tmp_path: Path,
) -> None:
    gateway = StoreGateway(_target(tmp_path), str(tmp_path / "generations"))
    await gateway.start()
    active = await gateway.publish(
        gateway.generations.allocate("revision-1", generation_id="generation-1"),
        lambda _: None,
    )

    with pytest.raises(StoreGatewayError) as raised:
        await gateway.query(
            lambda _manifest: __import__("time").sleep(0.02),
            deadline_seconds=0.001,
            request_id="request-1",
        )

    assert raised.value.code is GatewayErrorCode.DEADLINE_EXCEEDED
    assert raised.value.details["correlation_id"] == "request-1"
    assert raised.value.details["active_generation"] == active.generation_id
    assert gateway.generations.reference_count(active.generation_id) == 0
    await gateway.close()


def test_recovery_discards_abandoned_temporary_manifest(tmp_path: Path) -> None:
    manager = GenerationManager(tmp_path / "generations", _target(tmp_path))
    active = manager.publish(
        manager.allocate("revision-1", generation_id="generation-1"),
        lambda _: None,
    )
    temporary = manager.manifest_path.with_suffix(".tmp")
    temporary.write_text("{incomplete", encoding="utf-8")

    recovered = GenerationManager(manager.root, manager.target).recover()

    assert recovered == active
    assert not temporary.exists()


def test_retirement_rejects_a_generation_root_symlink(tmp_path: Path) -> None:
    manager = GenerationManager(tmp_path / "generations", _target(tmp_path))
    old = manager.allocate("revision-1", generation_id="generation-1")
    manager.publish(old, lambda _: None)
    manager.publish(
        manager.allocate("revision-2", generation_id="generation-2"),
        lambda _: None,
    )
    generation_root = manager.generations_root / old.generation_id
    outside = tmp_path / "must-survive"
    outside.mkdir()
    (outside / "sentinel").write_text("keep", encoding="utf-8")
    for child in generation_root.iterdir():
        child.unlink()
    generation_root.rmdir()
    generation_root.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symbolic link"):
        manager.retire(old)
    assert (outside / "sentinel").read_text(encoding="utf-8") == "keep"


def test_incompatibility_fence_wins_race_with_publication(tmp_path: Path) -> None:
    manager = GenerationManager(tmp_path / "generations", _target(tmp_path))
    candidate = manager.allocate("revision-1", generation_id="generation-1")
    validation_started = threading.Event()
    release_validation = threading.Event()
    outcome: list[BaseException] = []

    def validate(_manifest) -> None:
        validation_started.set()
        release_validation.wait(timeout=5)

    def publish() -> None:
        try:
            manager.publish(candidate, validate)
        except BaseException as exc:  # captured for assertion in the main thread
            outcome.append(exc)

    worker = threading.Thread(target=publish)
    worker.start()
    assert validation_started.wait(timeout=5)
    manager.mark_incompatible(candidate.generation_id, reason="failed structural gate")
    release_validation.set()
    worker.join(timeout=5)

    assert len(outcome) == 1
    assert isinstance(outcome[0], ValueError)
    assert "structurally incompatible" in str(outcome[0])
    assert manager.load_active() is None


def test_retirement_fence_prevents_same_generation_republication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = GenerationManager(tmp_path / "generations", _target(tmp_path))
    old = manager.publish(
        manager.allocate("revision-1", generation_id="generation-1"),
        lambda _: None,
    )
    manager.publish(
        manager.allocate("revision-2", generation_id="generation-2"),
        lambda _: None,
    )
    delete_started = threading.Event()
    release_delete = threading.Event()
    real_rmtree = __import__("shutil").rmtree

    def blocked_rmtree(path: Path) -> None:
        delete_started.set()
        release_delete.wait(timeout=5)
        real_rmtree(path)

    monkeypatch.setattr("cortex_harness.storage.generation.shutil.rmtree", blocked_rmtree)
    result: list[bool] = []
    worker = threading.Thread(target=lambda: result.append(manager.retire(old)))
    worker.start()
    assert delete_started.wait(timeout=5)

    with pytest.raises(ValueError, match="retir"):
        manager.publish(old, lambda _: None)

    release_delete.set()
    worker.join(timeout=5)
    assert result == [True]
    assert manager.load_active().generation_id == "generation-2"
    with pytest.raises(ValueError, match="retired or missing"):
        manager.publish(old, lambda _: None)


def test_generation_record_failure_cannot_change_active_pointer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = GenerationManager(tmp_path / "generations", _target(tmp_path))
    first = manager.publish(
        manager.allocate("revision-1", generation_id="generation-1"),
        lambda _: None,
    )
    candidate = manager.allocate("revision-2", generation_id="generation-2")

    def fail_record(_manifest) -> None:
        raise OSError("simulated generation record fsync failure")

    monkeypatch.setattr(manager, "_write_generation_record", fail_record)
    with pytest.raises(OSError, match="fsync failure"):
        manager.publish(candidate, lambda _: None)

    assert manager.load_active() == first
