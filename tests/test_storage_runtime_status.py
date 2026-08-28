from __future__ import annotations

from pathlib import Path
import threading

import pytest

from cortex_harness.storage import (
    PhysicalTargetKey,
    StoreGateway,
    begin_gateway_drain,
    storage_runtime_status,
)


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
    gateway = StoreGateway(_target(tmp_path), str(tmp_path / "owner"))
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
    assert storage_runtime_status({})["gateway_count"] == 0


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
