"""Single-process owner for bounded generation-pinned store access."""

from __future__ import annotations

import asyncio
import inspect
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from time import monotonic
from typing import Any, Awaitable, Callable, TypeVar

from .admission import BoundedLane, LaneLimits
from .contracts import (
    FreshnessMetadata,
    GatewayErrorCode,
    GenerationManifest,
    IngestionJob,
    IngestionJobState,
    OwnerLifecycleState,
    PerformanceProfile,
    PhysicalTargetKey,
    StoreGatewayError,
    StoreHealth,
)
from .generation import GenerationManager
from .lease import StorageLease


T = TypeVar("T")


@dataclass(frozen=True)
class GatewayLimits:
    graph_read: LaneLimits = LaneLimits()
    vector_read: LaneLimits = LaneLimits()
    write: LaneLimits = LaneLimits(concurrency=1, max_queue_items=8)
    control: LaneLimits = LaneLimits(concurrency=1, max_queue_items=4)

    @classmethod
    def from_profile(cls, profile: PerformanceProfile) -> "GatewayLimits":
        """Translate the sole validated performance object into lane budgets."""
        return cls(
            graph_read=LaneLimits(profile.graph_readers, profile.max_queue_items, profile.max_queue_bytes),
            vector_read=LaneLimits(profile.vector_readers, profile.max_queue_items, profile.max_queue_bytes),
            write=LaneLimits(profile.writer_slots, max(1, profile.max_queue_items // 4), profile.max_queue_bytes),
            control=LaneLimits(profile.control_slots, max(1, profile.max_queue_items // 8), profile.max_queue_bytes),
        )


class StoreGateway:
    """Coordinates one physical graph/vector target without opening clients.

    Backend adapters pass an operation that uses a manifest's explicit paths.
    This makes the gateway usable before all legacy adapters are migrated and
    prevents it from silently acquiring a second client for an active target.
    """

    def __init__(self, target: PhysicalTargetKey, generation_root: str, *, limits: GatewayLimits | None = None) -> None:
        self.target = target
        self.limits = limits or GatewayLimits()
        self.generations = GenerationManager(generation_root, target)
        self.lifecycle = OwnerLifecycleState.STARTING
        self._leases: list[StorageLease] = []
        self._jobs: dict[str, IngestionJob] = {}
        self._job_keys: dict[tuple[str, str], str] = {}
        self._job_lock = asyncio.Lock()
        self._lanes = {
            "graph": BoundedLane("graph-read", self.limits.graph_read),
            "vector": BoundedLane("vector-read", self.limits.vector_read),
            "write": BoundedLane("storage-write", self.limits.write),
            "control": BoundedLane("control-health", self.limits.control),
        }
        self._executors = {
            "graph": ThreadPoolExecutor(max_workers=self.limits.graph_read.concurrency, thread_name_prefix="cortex-graph-read"),
            "vector": ThreadPoolExecutor(max_workers=self.limits.vector_read.concurrency, thread_name_prefix="cortex-vector-read"),
            "write": ThreadPoolExecutor(max_workers=1, thread_name_prefix="cortex-storage-write"),
            "control": ThreadPoolExecutor(max_workers=self.limits.control.concurrency, thread_name_prefix="cortex-control"),
        }

    async def start(self) -> None:
        if self.lifecycle is not OwnerLifecycleState.STARTING:
            return
        acquired: list[StorageLease] = []
        try:
            for path, backend in ((path, "falkordb" if path.suffix else "qdrant") for path in self.target.canonical_paths):
                lease = StorageLease(path, instance_id=self.target.instance_id, owner_id=self.target.owner_id, backend=backend).acquire()
                acquired.append(lease)
        except Exception:
            for lease in reversed(acquired):
                lease.release()
            raise
        self._leases = acquired
        self.lifecycle = OwnerLifecycleState.WARMING
        self.lifecycle = OwnerLifecycleState.READY

    async def close(self) -> None:
        if self.lifecycle is OwnerLifecycleState.STOPPED:
            return
        self.lifecycle = OwnerLifecycleState.DRAINING
        await asyncio.gather(*(lane.wait_idle() for lane in self._lanes.values()))
        for executor in self._executors.values():
            executor.shutdown(wait=True, cancel_futures=True)
        for lease in reversed(self._leases):
            lease.release()
        self._leases.clear()
        self.lifecycle = OwnerLifecycleState.STOPPED

    async def query(
        self,
        operation: Callable[[Any], T | Awaitable[T]],
        *,
        lane: str = "graph",
        estimated_bytes: int = 0,
        deadline_seconds: float | None = None,
    ) -> tuple[T, FreshnessMetadata]:
        if self.lifecycle is not OwnerLifecycleState.READY:
            raise StoreGatewayError(GatewayErrorCode.STORE_MAINTENANCE, "store owner is not ready", retryable=True)
        if lane not in {"graph", "vector", "control"}:
            raise ValueError(f"unsupported query lane: {lane}")
        deadline = None if deadline_seconds is None else monotonic() + deadline_seconds

        async def run() -> tuple[T, FreshnessMetadata]:
            try:
                with self.generations.pin_active() as manifest:
                    result = await asyncio.get_running_loop().run_in_executor(
                        self._executors[lane], operation, manifest
                    )
                    if inspect.isawaitable(result):
                        result = await result
                    metadata = FreshnessMetadata(
                        served_generation=manifest.generation_id,
                        source_revision=manifest.source_revision,
                        last_committed_at=manifest.published_at,
                    )
                    return result, metadata
            except RuntimeError as exc:
                if str(exc) != "no active generation is available":
                    raise
                raise StoreGatewayError(
                    GatewayErrorCode.STORE_MAINTENANCE,
                    "store has no committed generation",
                    retryable=True,
                ) from exc

        return await self._lanes[lane].run(run, estimated_bytes=estimated_bytes, deadline=deadline)

    async def write(
        self,
        operation: Callable[[], T | Awaitable[T]],
        *,
        estimated_bytes: int = 0,
        deadline_seconds: float | None = None,
    ) -> T:
        """Execute one staging write through the target's sole writer lane."""
        if self.lifecycle is not OwnerLifecycleState.READY:
            raise StoreGatewayError(GatewayErrorCode.STORE_MAINTENANCE, "store owner is not ready", retryable=True)
        deadline = None if deadline_seconds is None else monotonic() + deadline_seconds

        async def run() -> T:
            result = await asyncio.get_running_loop().run_in_executor(self._executors["write"], operation)
            if inspect.isawaitable(result):
                result = await result
            return result

        return await self._lanes["write"].run(run, estimated_bytes=estimated_bytes, deadline=deadline)

    async def publish(
        self, manifest: GenerationManifest, validate: Callable[[GenerationManifest], None]
    ) -> GenerationManifest:
        """Validate and atomically make one staged graph/vector pair active."""
        return await self.write(lambda: self.generations.publish(manifest, validate))

    async def retire(self, manifest: GenerationManifest) -> bool:
        """Retire only a non-active generation with no pinned readers."""
        async def run() -> bool:
            return await asyncio.get_running_loop().run_in_executor(
                self._executors["control"], self.generations.retire, manifest
            )

        return await self._lanes["control"].run(run)

    async def submit_ingest(self, *, idempotency_key: str, source_revision: str) -> IngestionJob:
        key = (self.target.value, idempotency_key)
        async with self._job_lock:
            existing_id = self._job_keys.get(key)
            if existing_id is not None:
                return self._jobs[existing_id]
            job = IngestionJob(
                job_id=uuid.uuid4().hex,
                target=self.target,
                idempotency_key=idempotency_key,
                source_revision=source_revision,
                queue_position=sum(1 for value in self._jobs.values() if value.state is IngestionJobState.QUEUED),
            )
            self._jobs[job.job_id] = job
            self._job_keys[key] = job.job_id
            return job

    async def update_job(self, job_id: str, state: IngestionJobState, **changes: Any) -> IngestionJob:
        async with self._job_lock:
            job = self._jobs[job_id]
            updated = job.with_state(state, **changes)
            self._jobs[job_id] = updated
            return updated

    async def get_ingestion_status(self, job_id: str) -> IngestionJob | None:
        async with self._job_lock:
            return self._jobs.get(job_id)

    def health(self) -> StoreHealth:
        try:
            active = self.generations.load_active()
        except (OSError, ValueError):
            active = None
        return StoreHealth(
            target=self.target,
            lifecycle=self.lifecycle,
            active_generation=active.generation_id if active else None,
            active_readers=sum(self._lanes[name].snapshot["active"] for name in ("graph", "vector")),
            queued_reads=sum(self._lanes[name].snapshot["queued_items"] for name in ("graph", "vector")),
            queued_writes=self._lanes["write"].snapshot["queued_items"],
            ready=self.lifecycle is OwnerLifecycleState.READY,
        )
