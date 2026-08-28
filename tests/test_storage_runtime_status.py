from __future__ import annotations

import asyncio
from pathlib import Path
import threading

import pytest

from cortex_harness.storage import (
    GatewayErrorCode,
    PhysicalTargetKey,
    StoreGateway,
    StoreGatewayError,
    begin_gateway_drain,
    close_active_gateways,
    storage_runtime_status,
)
from cortex_harness.storage.runtime import _reset_runtime_state_for_tests


@pytest.fixture(autouse=True)
def _isolated_runtime_registry():
    _reset_runtime_state_for_tests()
    yield
    _reset_runtime_state_for_tests()


def _target(root: Path) -> PhysicalTargetKey:
    return PhysicalTargetKey.from_paths(
        instance_id="runtime",
        owner_id="code",
        graph_path=root / "graph.rdb",
        vector_path=root / "vectors",
    )


def test_disabled_gateway_reports_ready_pause_restart_mode() -> None:
    status = storage_runtime_status({})

    assert status["mode"] == "pause_restart"
    assert status["state"] == "rollback_ready"
    assert status["liveness"] is True
    assert status["readiness"] is True


def test_requested_gateway_fails_readiness_without_an_owner() -> None:
    status = storage_runtime_status({"CORTEX_STORE_GATEWAY_ENABLED": "1"})

    assert status["mode"] == "generation_gateway"
    assert status["state"] == "owner_missing"
    assert status["readiness"] is False


@pytest.mark.asyncio
async def test_active_gateway_status_is_sanitized_and_drains(tmp_path: Path) -> None:
    gateway = StoreGateway(
        _target(tmp_path),
        str(tmp_path / "owner"),
        graph_probe=lambda _manifest: None,
        vector_probe=lambda _manifest: None,
    )
    await gateway.start()
    await gateway.publish(
        gateway.generations.allocate("revision-1", generation_id="generation-1"),
        lambda _manifest: None,
    )

    status = storage_runtime_status({})
    assert status["mode"] == "generation_gateway"
    assert status["state"] == "ready"
    assert status["readiness"] is True
    assert status["gateway_count"] == 1
    snapshot = status["gateways"][0]
    assert snapshot["active_generation"] == "generation-1"
    assert "graph_path" not in snapshot
    assert "vector_path" not in snapshot
    assert len(snapshot["target_fingerprint"]) == 16

    assert begin_gateway_drain() == 1
    drained = storage_runtime_status({})
    assert drained["state"] == "draining"
    assert drained["readiness"] is False

    await gateway.close()
    stopped = storage_runtime_status({})
    assert stopped["gateway_count"] == 0
    assert stopped["state"] == "owner_missing"
    assert stopped["readiness"] is False


@pytest.mark.asyncio
async def test_configured_readiness_probes_fail_closed_and_recover(
    tmp_path: Path,
) -> None:
    fail_vector = False
    seen: list[tuple[str, str]] = []

    def graph_probe(manifest) -> None:
        seen.append(("graph", manifest.generation_id))

    def vector_probe(manifest) -> None:
        seen.append(("vector", manifest.generation_id))
        if fail_vector:
            raise RuntimeError("temporary probe failure with a sensitive path")

    gateway = StoreGateway(
        _target(tmp_path),
        str(tmp_path / "owner"),
        graph_probe=graph_probe,
        vector_probe=vector_probe,
    )
    await gateway.start()
    await gateway.publish(
        gateway.generations.allocate("revision-1", generation_id="generation-1"),
        lambda _manifest: None,
    )

    assert gateway.health().ready is True
    fail_vector = True
    failed = await gateway.refresh_readiness(graph_probe, vector_probe)
    assert failed.ready is False
    assert failed.probe_error == "RuntimeError"
    assert "sensitive" not in str(storage_runtime_status({}))

    fail_vector = False
    recovered = await gateway.refresh_readiness(graph_probe, vector_probe)
    assert recovered.ready is True
    assert recovered.probe_generation == "generation-1"
    assert recovered.probe_error is None
    assert seen == [
        ("graph", "generation-1"),
        ("vector", "generation-1"),
        ("graph", "generation-1"),
        ("vector", "generation-1"),
        ("graph", "generation-1"),
        ("vector", "generation-1"),
    ]
    await gateway.close()


@pytest.mark.asyncio
async def test_publication_probe_failure_keeps_previous_generation(
    tmp_path: Path,
) -> None:
    should_fail = False

    def graph_probe(_manifest) -> None:
        return None

    def vector_probe(_manifest) -> None:
        if should_fail:
            raise RuntimeError("candidate is unreadable")

    gateway = StoreGateway(
        _target(tmp_path),
        str(tmp_path / "owner"),
        graph_probe=graph_probe,
        vector_probe=vector_probe,
    )
    await gateway.start()
    first = await gateway.publish(
        gateway.generations.allocate("revision-1", generation_id="generation-1"),
        lambda _manifest: None,
    )
    should_fail = True

    with pytest.raises(RuntimeError, match="candidate is unreadable"):
        await gateway.publish(
            gateway.generations.allocate("revision-2", generation_id="generation-2"),
            lambda _manifest: None,
        )

    assert gateway.generations.load_active() == first
    assert gateway.health().active_generation == "generation-1"
    assert gateway.health().ready is True
    await gateway.close()


@pytest.mark.asyncio
async def test_gateway_owns_adapter_resource_until_lane_drain(
    tmp_path: Path,
) -> None:
    events: list[tuple[str, str]] = []

    class Resource:
        def __init__(self) -> None:
            events.append(("open", threading.current_thread().name))

        def close(self) -> None:
            events.append(("close", threading.current_thread().name))

    gateway = StoreGateway(_target(tmp_path), str(tmp_path / "owner"))
    await gateway.start()
    await gateway.publish(
        gateway.generations.allocate("revision-1", generation_id="generation-1"),
        lambda _manifest: None,
    )

    with pytest.raises(RuntimeError, match="named executor lane"):
        gateway.get_or_create_resource("invalid", Resource, lane="graph")

    def use_resource(_manifest):
        first = gateway.get_or_create_resource("graph:generation-1", Resource, lane="graph")
        second = gateway.get_or_create_resource("graph:generation-1", Resource, lane="graph")
        return first is second

    same_resource, _freshness = await gateway.query(use_resource)
    assert same_resource is True
    assert events == [("open", "cortex-graph-read_0")]

    await gateway.close()
    assert events == [
        ("open", "cortex-graph-read_0"),
        ("close", "cortex-graph-read_0"),
    ]


@pytest.mark.asyncio
async def test_start_cancellation_releases_executors_and_leases(
    tmp_path: Path,
) -> None:
    gateway = StoreGateway(_target(tmp_path), str(tmp_path / "owner"))
    entered = asyncio.Event()

    async def blocked_load() -> None:
        entered.set()
        await asyncio.Event().wait()

    gateway._load_jobs = blocked_load  # type: ignore[method-assign]
    start = asyncio.create_task(gateway.start())
    await entered.wait()
    start.cancel()
    with pytest.raises(asyncio.CancelledError):
        await start

    assert gateway.lifecycle.value == "STOPPED"
    assert gateway._executors == {}
    assert gateway._leases == []

    replacement = StoreGateway(_target(tmp_path), str(tmp_path / "replacement"))
    await replacement.start()
    await replacement.close()


@pytest.mark.asyncio
async def test_cancelled_publication_reconciles_gateway_health(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = StoreGateway(
        _target(tmp_path),
        str(tmp_path / "owner"),
        graph_probe=lambda _manifest: None,
        vector_probe=lambda _manifest: None,
    )
    await gateway.start()
    await gateway.publish(
        gateway.generations.allocate("revision-1", generation_id="generation-1"),
        lambda _manifest: None,
    )
    candidate = gateway.generations.allocate(
        "revision-2", generation_id="generation-2"
    )
    entered = threading.Event()
    release = threading.Event()
    original_publish = gateway.generations.publish

    def blocked_publish(manifest, validate):
        result = original_publish(manifest, validate)
        entered.set()
        release.wait(timeout=5)
        return result

    monkeypatch.setattr(gateway.generations, "publish", blocked_publish)
    publication = asyncio.create_task(
        gateway.publish(candidate, lambda _manifest: None)
    )
    assert await asyncio.get_running_loop().run_in_executor(None, entered.wait, 5)
    publication.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await publication

    assert gateway.generations.load_active().generation_id == "generation-2"
    assert gateway.health().active_generation == "generation-2"
    assert gateway.health().ready is True
    await gateway.close()


@pytest.mark.asyncio
async def test_close_persistence_failure_still_releases_owned_resources(
    tmp_path: Path,
) -> None:
    closed: list[bool] = []

    class Resource:
        def close(self) -> None:
            closed.append(True)

    gateway = StoreGateway(_target(tmp_path), str(tmp_path / "owner"))
    await gateway.start()
    await gateway.publish(
        gateway.generations.allocate("revision-1", generation_id="generation-1"),
        lambda _manifest: None,
    )
    await gateway.query(
        lambda _manifest: gateway.get_or_create_resource(
            "graph:generation-1", Resource, lane="graph"
        )
    )

    async def fail_persist() -> None:
        raise OSError("simulated job-store failure")

    gateway._persist_jobs = fail_persist  # type: ignore[method-assign]
    with pytest.raises(OSError, match="job-store failure"):
        await gateway.close()

    assert closed == [True]
    assert gateway.lifecycle.value == "STOPPED"
    assert gateway._executors == {}
    assert gateway._leases == []


@pytest.mark.asyncio
async def test_start_cancellation_after_resource_open_closes_before_lease_release(
    tmp_path: Path,
) -> None:
    entered = threading.Event()
    release = threading.Event()
    closed: list[bool] = []

    class Resource:
        def close(self) -> None:
            closed.append(True)

    gateway = StoreGateway(
        _target(tmp_path),
        str(tmp_path / "owner"),
        graph_probe=lambda manifest: (
            gateway.get_or_create_resource("probe", Resource, lane="control"),
            entered.set(),
            release.wait(timeout=5),
        ),
        vector_probe=lambda _manifest: None,
    )
    gateway.generations.publish(
        gateway.generations.allocate("revision-1", generation_id="generation-1"),
        lambda _manifest: None,
    )
    start = asyncio.create_task(gateway.start())
    assert await asyncio.get_running_loop().run_in_executor(None, entered.wait, 5)
    start.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await start

    assert closed == [True]
    assert gateway._resources == {}
    assert gateway._executors == {}
    assert gateway._leases == []


@pytest.mark.asyncio
async def test_failed_startup_resource_close_retains_owner_but_rejects_ingest(
    tmp_path: Path,
) -> None:
    entered = threading.Event()
    release = threading.Event()
    fail_close = True

    class Resource:
        def close(self) -> None:
            if fail_close:
                raise RuntimeError("probe handle still open")

    gateway = StoreGateway(
        _target(tmp_path),
        str(tmp_path / "owner"),
        graph_probe=lambda manifest: (
            gateway.get_or_create_resource("probe", Resource, lane="control"),
            entered.set(),
            release.wait(timeout=5),
        ),
        vector_probe=lambda _manifest: None,
    )
    gateway.generations.publish(
        gateway.generations.allocate("revision-1", generation_id="generation-1"),
        lambda _manifest: None,
    )
    start = asyncio.create_task(gateway.start())
    assert await asyncio.get_running_loop().run_in_executor(None, entered.wait, 5)
    start.cancel()
    release.set()
    with pytest.raises(RuntimeError, match="probe handle still open"):
        await start

    assert gateway.lifecycle.value == "DRAINING"
    assert gateway._accepting_jobs is False
    assert gateway._resources
    assert gateway._executors
    assert gateway._leases
    with pytest.raises(StoreGatewayError) as rejected:
        await gateway.submit_ingest(
            idempotency_key="must-not-enter", source_revision="revision-2"
        )
    assert rejected.value.code is GatewayErrorCode.STORE_MAINTENANCE

    fail_close = False
    await gateway.close()


@pytest.mark.asyncio
async def test_resource_close_failure_retains_lease_for_retry(tmp_path: Path) -> None:
    fail_close = True

    class Resource:
        def close(self) -> None:
            if fail_close:
                raise RuntimeError("still open")

    gateway = StoreGateway(_target(tmp_path), str(tmp_path / "owner"))
    await gateway.start()
    await gateway.publish(
        gateway.generations.allocate("revision-1", generation_id="generation-1"),
        lambda _manifest: None,
    )
    await gateway.query(
        lambda _manifest: gateway.get_or_create_resource("graph", Resource, lane="graph")
    )

    with pytest.raises(RuntimeError, match="still open"):
        await gateway.close()
    assert gateway.lifecycle.value == "DRAINING"
    assert gateway._resources
    assert gateway._executors
    assert gateway._leases

    fail_close = False
    await gateway.close()
    assert gateway.lifecycle.value == "STOPPED"
    assert gateway._leases == []


@pytest.mark.asyncio
async def test_non_cancelled_post_pointer_failure_reconciles_and_fails_readiness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = StoreGateway(
        _target(tmp_path),
        str(tmp_path / "owner"),
        graph_probe=lambda _manifest: None,
        vector_probe=lambda _manifest: None,
    )
    await gateway.start()
    await gateway.publish(
        gateway.generations.allocate("revision-1", generation_id="generation-1"),
        lambda _manifest: None,
    )
    candidate = gateway.generations.allocate(
        "revision-2", generation_id="generation-2"
    )
    original_write = gateway.generations._write_active_manifest_unlocked

    def committed_then_failed(manifest) -> None:
        original_write(manifest)
        raise OSError("directory fsync failed after replace")

    monkeypatch.setattr(
        gateway.generations, "_write_active_manifest_unlocked", committed_then_failed
    )
    with pytest.raises(OSError, match="after replace"):
        await gateway.publish(candidate, lambda _manifest: None)

    assert gateway.generations.load_active().generation_id == "generation-2"
    health = gateway.health()
    assert health.active_generation == "generation-2"
    assert health.ready is False
    assert health.probe_error == "OSError"
    await gateway.close()


@pytest.mark.asyncio
async def test_close_active_gateways_closes_resource_before_releasing_lease(
    tmp_path: Path,
) -> None:
    observations: list[tuple[bool, bool]] = []
    gateway = StoreGateway(_target(tmp_path), str(tmp_path / "owner"))
    await gateway.start()
    await gateway.publish(
        gateway.generations.allocate("revision-1", generation_id="generation-1"),
        lambda _manifest: None,
    )

    class Resource:
        def close(self) -> None:
            observations.append((bool(gateway._executors), bool(gateway._leases)))

    await gateway.query(
        lambda _manifest: gateway.get_or_create_resource("graph", Resource, lane="graph")
    )
    assert await close_active_gateways() == 1

    assert observations == [(True, True)]
    assert gateway._executors == {}
    assert gateway._leases == []
