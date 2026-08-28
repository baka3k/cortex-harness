"""Process-local observability and drain coordination for store gateways.

The registry is deliberately not a factory and never opens storage.  It only
tracks gateways that have already acquired their leases, allowing MCP health
and signal handlers to report and drain the real owner state without creating
a second embedded client.
"""

from __future__ import annotations

import hashlib
import os
import threading
import weakref
from typing import TYPE_CHECKING, Any, Mapping


if TYPE_CHECKING:  # pragma: no cover - import-cycle guard
    from .gateway import StoreGateway


ENV_STORE_GATEWAY_ENABLED = "CORTEX_STORE_GATEWAY_ENABLED"

_registry_lock = threading.RLock()
_gateways: weakref.WeakSet[StoreGateway] = weakref.WeakSet()


def _flag_enabled(value: object) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def store_gateway_enabled(env: Mapping[str, object] | None = None) -> bool:
    """Return whether generation-gateway mode was explicitly requested."""

    source: Mapping[str, object] = os.environ if env is None else env
    return _flag_enabled(source.get(ENV_STORE_GATEWAY_ENABLED))


def register_gateway(gateway: StoreGateway) -> None:
    """Publish an already-started gateway to process health consumers."""

    with _registry_lock:
        _gateways.add(gateway)


def unregister_gateway(gateway: StoreGateway) -> None:
    """Remove a stopped or failed gateway from process health consumers."""

    with _registry_lock:
        _gateways.discard(gateway)


def active_gateways() -> tuple[StoreGateway, ...]:
    """Return a stable snapshot without keeping gateways alive indefinitely."""

    with _registry_lock:
        return tuple(_gateways)


def begin_gateway_drain() -> int:
    """Stop new admission on every active gateway without awaiting store I/O."""

    gateways = active_gateways()
    for gateway in gateways:
        gateway.begin_drain()
    return len(gateways)


def _target_fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def storage_runtime_status(
    env: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    """Return a bounded, credential-free owner status projection.

    With the guarded gateway feature disabled, pause/restart remains the
    supported and ready rollback mode.  When gateway mode is requested,
    readiness fails closed until at least one registered gateway has a
    committed active generation.
    """

    gateways = active_gateways()
    requested = store_gateway_enabled(env)
    effective = requested or bool(gateways)
    snapshots: list[dict[str, Any]] = []
    for gateway in sorted(gateways, key=lambda item: item.target.value):
        health = gateway.health()
        metrics = gateway.metrics()
        snapshots.append(
            {
                "instance_id": health.target.instance_id,
                "owner_id": health.target.owner_id,
                "target_fingerprint": _target_fingerprint(health.target.value),
                "lifecycle": health.lifecycle.value,
                "active_generation": health.active_generation,
                "active_readers": health.active_readers,
                "queued_reads": health.queued_reads,
                "queued_writes": health.queued_writes,
                "ready": health.ready,
                "probe_generation": health.probe_generation,
                "last_probe_at": health.last_probe_at,
                "probe_error": health.probe_error,
                "updated_at": health.updated_at,
                "lanes": metrics["lanes"],
                "jobs": metrics["jobs"],
            }
        )

    ready = all(item["ready"] for item in snapshots) and bool(snapshots)
    if not effective:
        state = "rollback_ready"
        ready = True
    elif not snapshots:
        state = "owner_missing"
    elif any(item["lifecycle"] == "DRAINING" for item in snapshots):
        state = "draining"
    elif ready:
        state = "ready"
    else:
        state = "warming"

    return {
        "mode": "generation_gateway" if effective else "pause_restart",
        "feature_requested": requested,
        "liveness": True,
        "readiness": ready,
        "state": state,
        "gateway_count": len(snapshots),
        "gateways": snapshots,
    }
