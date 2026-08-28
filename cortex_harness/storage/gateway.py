"""Single-process owner for bounded generation-pinned store access."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Any, Callable, TypeVar

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


_JOB_TRANSITIONS: dict[IngestionJobState, frozenset[IngestionJobState]] = {
    IngestionJobState.QUEUED: frozenset(
        {
            IngestionJobState.PREPARING,
            IngestionJobState.WRITING,
            IngestionJobState.CANCELLED,
            IngestionJobState.FAILED,
            IngestionJobState.SUPERSEDED,
        }
    ),
    IngestionJobState.PREPARING: frozenset(
        {
            IngestionJobState.WRITING,
            IngestionJobState.CANCELLED,
            IngestionJobState.FAILED,
            IngestionJobState.SUPERSEDED,
        }
    ),
    IngestionJobState.WRITING: frozenset(
        {
            IngestionJobState.VALIDATING,
            IngestionJobState.CANCELLED,
            IngestionJobState.FAILED,
            IngestionJobState.AMBIGUOUS,
        }
    ),
    IngestionJobState.VALIDATING: frozenset(
        {
            IngestionJobState.PUBLISHING,
            IngestionJobState.CANCELLED,
            IngestionJobState.FAILED,
            IngestionJobState.AMBIGUOUS,
        }
    ),
    IngestionJobState.PUBLISHING: frozenset(
        {
            IngestionJobState.COMPLETED,
            IngestionJobState.FAILED,
            IngestionJobState.AMBIGUOUS,
        }
    ),
}


@dataclass(frozen=True)
class GatewayLimits:
    graph_read: LaneLimits = LaneLimits()
    vector_read: LaneLimits = LaneLimits()
    write: LaneLimits = LaneLimits(concurrency=1, max_queue_items=8)
    control: LaneLimits = LaneLimits(concurrency=1, max_queue_items=4)
    drain_timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if self.drain_timeout_seconds <= 0:
            raise ValueError("gateway drain timeout must be positive")

    @classmethod
    def from_profile(cls, profile: PerformanceProfile) -> "GatewayLimits":
        """Translate the sole validated performance object into lane budgets."""
        return cls(
            graph_read=LaneLimits(profile.graph_readers, profile.max_queue_items, profile.max_queue_bytes),
            vector_read=LaneLimits(profile.vector_readers, profile.max_queue_items, profile.max_queue_bytes),
            write=LaneLimits(
                profile.writer_slots,
                max(1, profile.max_queue_items // 4),
                profile.max_queue_bytes,
            ),
            control=LaneLimits(
                profile.control_slots,
                max(1, profile.max_queue_items // 8),
                profile.max_queue_bytes,
            ),
            drain_timeout_seconds=max(1.0, profile.request_timeout_seconds * 2),
        )


class StoreGateway:
    """Coordinates one physical graph/vector target without opening clients.

    Backend adapters pass an operation that uses a manifest's explicit paths.
    This makes the gateway usable before all legacy adapters are migrated and
    prevents it from silently acquiring a second client for an active target.
    """

    def __init__(
        self,
        target: PhysicalTargetKey,
        generation_root: str,
        *,
        limits: GatewayLimits | None = None,
        generation_manager: GenerationManager | None = None,
        graph_probe: Callable[[GenerationManifest], Any] | None = None,
        vector_probe: Callable[[GenerationManifest], Any] | None = None,
    ) -> None:
        self.target = target
        self.limits = limits or GatewayLimits()
        if generation_manager is not None and generation_manager.target != target:
            raise ValueError("generation manager target does not match gateway target")
        self.generations = generation_manager or GenerationManager(generation_root, target)
        self.lifecycle = OwnerLifecycleState.STARTING
        self._leases: list[StorageLease] = []
        self._jobs: dict[str, IngestionJob] = {}
        self._job_keys: dict[tuple[str, str], str] = {}
        self._job_lock = asyncio.Lock()
        self._job_changed = asyncio.Condition(self._job_lock)
        self._job_persist_lock = asyncio.Lock()
        self._jobs_path = Path(generation_root).resolve() / "ingestion-jobs.json"
        self._active_generation: GenerationManifest | None = None
        if (graph_probe is None) != (vector_probe is None):
            raise ValueError("graph and vector readiness probes must be configured together")
        self._graph_probe = graph_probe
        self._vector_probe = vector_probe
        self._probe_generation: str | None = None
        self._last_probe_at: str | None = None
        self._probe_error: str | None = None
        self._accepting_jobs = False
        self._lanes = {
            "graph": BoundedLane("graph-read", self.limits.graph_read),
            "vector": BoundedLane("vector-read", self.limits.vector_read),
            "write": BoundedLane("storage-write", self.limits.write),
            "control": BoundedLane("control-health", self.limits.control),
        }
        # The process lease is intentionally acquired before any executor or
        # backend resource is created. This also makes partial startup unwind
        # deterministic when the second physical target is already owned.
        self._executors: dict[str, ThreadPoolExecutor] = {}
        self._resource_lock = threading.Lock()
        self._resources: dict[str, tuple[str, Any]] = {}
        self._execution_context = threading.local()

    @classmethod
    def from_storage_factory(
        cls,
        factory: object,
        generation_root: str,
        *,
        graph_name: str,
        collection_name: str,
        role: object = "code",
        project_scope: str | None = None,
        limits: GatewayLimits | None = None,
        graph_probe: Callable[[GenerationManifest], Any] | None = None,
        vector_probe: Callable[[GenerationManifest], Any] | None = None,
    ) -> "StoreGateway":
        """Create a lease-safe gateway with topology-fenced publication."""

        manager = GenerationManager.from_storage_factory(
            Path(generation_root),
            factory,
            graph_name=graph_name,
            collection_name=collection_name,
            role=role,
            project_scope=project_scope,
        )
        return cls(
            manager.target,
            generation_root,
            limits=limits,
            generation_manager=manager,
            graph_probe=graph_probe,
            vector_probe=vector_probe,
        )

    async def start(self) -> None:
        if self.lifecycle is not OwnerLifecycleState.STARTING:
            return
        acquired: list[StorageLease] = []
        try:
            graph_path = Path(self.target.graph_path)
            for path in self.target.canonical_paths:
                backend = "falkordb" if path == graph_path else "qdrant"
                lease = StorageLease(
                    path,
                    instance_id=self.target.instance_id,
                    owner_id=self.target.owner_id,
                    backend=backend,
                ).acquire()
                acquired.append(lease)
        except BaseException:
            for lease in reversed(acquired):
                lease.release()
            raise
        self._leases = acquired
        try:
            self._executors = {
                "graph": ThreadPoolExecutor(
                    max_workers=self.limits.graph_read.concurrency,
                    thread_name_prefix="cortex-graph-read",
                ),
                "vector": ThreadPoolExecutor(
                    max_workers=self.limits.vector_read.concurrency,
                    thread_name_prefix="cortex-vector-read",
                ),
                "write": ThreadPoolExecutor(
                    max_workers=1, thread_name_prefix="cortex-storage-write"
                ),
                "control": ThreadPoolExecutor(
                    max_workers=self.limits.control.concurrency,
                    thread_name_prefix="cortex-control",
                ),
            }
            self.lifecycle = OwnerLifecycleState.RECOVERING
            self._active_generation = await self._run_sync(
                "control", self.generations.recover
            )
            await self._load_jobs()
            self.lifecycle = OwnerLifecycleState.WARMING
            # Backend-specific deep probes are supplied by the adapter layer.
            # Until an active pair exists the owner can accept a staging write,
            # but readiness remains false in ``health``.
            self.lifecycle = OwnerLifecycleState.READY
            async with self._job_changed:
                self._accepting_jobs = True
                self._job_changed.notify_all()
            if self._active_generation is not None and self._graph_probe is not None:
                await self.refresh_readiness(self._graph_probe, self._vector_probe)
            from .runtime import register_gateway

            register_gateway(self)
        except BaseException as startup_error:
            from .runtime import unregister_gateway

            self.begin_drain()
            unregister_gateway(self)
            resource_error = await self._close_owned_resources()
            if resource_error is not None:
                # A possibly-live backend handle must remain fenced. Keep its
                # lane executors and process leases so close() can be retried.
                self.lifecycle = OwnerLifecycleState.DRAINING
                from .runtime import register_gateway

                register_gateway(self)
                raise resource_error from startup_error
            for executor in self._executors.values():
                executor.shutdown(wait=True, cancel_futures=True)
            self._executors.clear()
            for lease in reversed(self._leases):
                lease.release()
            self._leases.clear()
            self.lifecycle = OwnerLifecycleState.STOPPED
            raise

    def begin_drain(self) -> None:
        """Synchronously stop admission for first-signal shutdown handling."""

        if self.lifecycle is OwnerLifecycleState.STOPPED:
            return
        self.lifecycle = OwnerLifecycleState.DRAINING
        self._accepting_jobs = False

    async def close(self, *, timeout_seconds: float | None = None) -> None:
        if self.lifecycle is OwnerLifecycleState.STOPPED:
            return
        self.begin_drain()
        async with self._job_changed:
            self._accepting_jobs = False
            self._job_changed.notify_all()
        timeout = (
            self.limits.drain_timeout_seconds
            if timeout_seconds is None
            else timeout_seconds
        )
        if timeout <= 0:
            raise ValueError("gateway close timeout must be positive")
        try:
            await asyncio.wait_for(
                asyncio.gather(*(lane.wait_idle() for lane in self._lanes.values())),
                timeout=timeout,
            )
        except TimeoutError as exc:
            # Retain leases and executors: a non-preemptive store call may
            # still be using them. The lifecycle remains DRAINING so a caller
            # can retry close or follow the documented force-exit path.
            raise StoreGatewayError(
                GatewayErrorCode.DEADLINE_EXCEEDED,
                "store gateway drain deadline elapsed",
                retryable=False,
                details={"lifecycle": self.lifecycle.value},
            ) from exc
        close_error: BaseException | None = None
        try:
            await self._persist_jobs()
        except BaseException as exc:
            # Persistence failure must not turn graceful teardown into a
            # lease/resource leak. Recovery will reconcile from the last
            # durable job snapshot on the next owner start.
            close_error = exc
        resource_error = await self._close_owned_resources()
        if resource_error is not None:
            # Do not release process ownership while a backend handle may
            # still be open. The operator can retry close or force-exit.
            self.lifecycle = OwnerLifecycleState.DRAINING
            if close_error is not None:
                raise resource_error from close_error
            raise resource_error
        for executor in self._executors.values():
            executor.shutdown(wait=True, cancel_futures=True)
        self._executors.clear()
        for lease in reversed(self._leases):
            lease.release()
        self._leases.clear()
        self.lifecycle = OwnerLifecycleState.STOPPED
        from .runtime import unregister_gateway

        unregister_gateway(self)
        if close_error is not None:
            raise close_error

    async def _close_owned_resources(self) -> BaseException | None:
        """Close resources on their lanes, retaining failed handles for retry."""

        with self._resource_lock:
            resources = list(self._resources.items())
        first_error: BaseException | None = None
        for key, (lane, resource) in reversed(resources):
            close_resource = getattr(resource, "close", None)
            if callable(close_resource):
                try:
                    await self._run_sync(lane, close_resource)
                except BaseException as exc:
                    if first_error is None:
                        first_error = exc
                    continue
            with self._resource_lock:
                if self._resources.get(key) == (lane, resource):
                    self._resources.pop(key, None)
        return first_error

    async def _run_sync(self, lane: str, operation: Callable[..., T], *args: Any) -> T:
        """Run a non-preemptive store call without releasing ownership early."""

        executor = self._executors.get(lane)
        if executor is None:
            raise RuntimeError("store gateway executor is not running")

        def invoke() -> T:
            previous = getattr(self._execution_context, "lane", None)
            self._execution_context.lane = lane
            try:
                return operation(*args)
            finally:
                self._execution_context.lane = previous

        future = asyncio.get_running_loop().run_in_executor(executor, invoke)
        try:
            result = await asyncio.shield(future)
        except asyncio.CancelledError:
            # A Python thread cannot be stopped safely. Keep the lane and any
            # generation pin until the real operation returns, then report the
            # caller cancellation truthfully.
            try:
                await asyncio.shield(future)
            except Exception:
                pass
            raise
        if inspect.isawaitable(result):
            close = getattr(result, "close", None)
            if callable(close):
                close()
            raise TypeError("gateway store operations must be synchronous callables")
        return result

    def get_or_create_resource(
        self,
        key: str,
        factory: Callable[[], T],
        *,
        lane: str,
    ) -> T:
        """Return one gateway-owned adapter handle and close it during drain.

        The factory runs outside the registry lock so opening a backend never
        violates the leaf-lock rule. Callers invoke this from the matching
        serialized gateway lane; a defensive duplicate check closes a raced
        handle instead of leaking it.
        """

        if not str(key or "").strip():
            raise ValueError("gateway resource key is required")
        if lane not in self._executors:
            raise ValueError(f"gateway resource lane is not running: {lane}")
        if getattr(self._execution_context, "lane", None) != lane:
            raise RuntimeError(
                "gateway resources must be opened from their named executor lane"
            )
        with self._resource_lock:
            existing = self._resources.get(key)
        if existing is not None:
            existing_lane, resource = existing
            if existing_lane != lane:
                raise ValueError("gateway resource key is already bound to another lane")
            return resource

        created = factory()
        with self._resource_lock:
            existing = self._resources.setdefault(key, (lane, created))
        existing_lane, resource = existing
        if resource is not created:
            close_created = getattr(created, "close", None)
            if callable(close_created):
                close_created()
        if existing_lane != lane:
            if resource is created:
                with self._resource_lock:
                    self._resources.pop(key, None)
                close_created = getattr(created, "close", None)
                if callable(close_created):
                    close_created()
            raise ValueError("gateway resource key is already bound to another lane")
        return resource

    async def query(
        self,
        operation: Callable[[Any], T],
        *,
        lane: str = "graph",
        estimated_bytes: int = 0,
        deadline_seconds: float | None = None,
        request_id: str | None = None,
    ) -> tuple[T, FreshnessMetadata]:
        request_id = request_id or uuid.uuid4().hex
        if self.lifecycle is not OwnerLifecycleState.READY:
            raise StoreGatewayError(
                GatewayErrorCode.STORE_MAINTENANCE,
                "store owner is not ready",
                retryable=True,
                details={"correlation_id": request_id},
            )
        if lane not in {"graph", "vector", "control"}:
            raise ValueError(f"unsupported query lane: {lane}")
        deadline = None if deadline_seconds is None else monotonic() + deadline_seconds

        async def run() -> tuple[T, FreshnessMetadata]:
            try:
                with self.generations.pin_active() as manifest:
                    result = await self._run_sync(lane, operation, manifest)
                    if deadline is not None and monotonic() > deadline:
                        raise StoreGatewayError(
                            GatewayErrorCode.DEADLINE_EXCEEDED,
                            f"{lane} query deadline elapsed during execution",
                            retryable=True,
                        )
                    async with self._job_lock:
                        active_job = next(
                            (
                                job
                                for job in self._jobs.values()
                                if not job.state.terminal
                            ),
                            None,
                        )
                    metadata = FreshnessMetadata(
                        served_generation=manifest.generation_id,
                        source_revision=manifest.source_revision,
                        last_committed_at=manifest.published_at,
                        ingestion_state=active_job.state if active_job else None,
                    )
                    return result, metadata
            except RuntimeError as exc:
                if str(exc) != "no active generation is available":
                    raise
                raise StoreGatewayError(
                    GatewayErrorCode.STORE_MAINTENANCE,
                    "store has no committed generation",
                    retryable=True,
                    details={"correlation_id": request_id},
                ) from exc

        try:
            return await self._lanes[lane].run(
                run, estimated_bytes=estimated_bytes, deadline=deadline
            )
        except StoreGatewayError as exc:
            exc.details.setdefault("correlation_id", request_id)
            exc.details.setdefault(
                "active_generation",
                self._active_generation.generation_id
                if self._active_generation
                else None,
            )
            raise

    async def write(
        self,
        operation: Callable[[], T],
        *,
        estimated_bytes: int = 0,
        deadline_seconds: float | None = None,
    ) -> T:
        """Execute one staging write through the target's sole writer lane."""
        if self.lifecycle is not OwnerLifecycleState.READY:
            raise StoreGatewayError(
                GatewayErrorCode.STORE_MAINTENANCE,
                "store owner is not ready",
                retryable=True,
            )
        deadline = None if deadline_seconds is None else monotonic() + deadline_seconds

        async def run() -> T:
            return await self._run_sync("write", operation)

        return await self._lanes["write"].run(run, estimated_bytes=estimated_bytes, deadline=deadline)

    async def publish(
        self, manifest: GenerationManifest, validate: Callable[[GenerationManifest], None]
    ) -> GenerationManifest:
        """Validate and atomically make one staged graph/vector pair active."""

        def validate_candidate(candidate: GenerationManifest) -> None:
            validate(candidate)
            if self._graph_probe is not None and self._vector_probe is not None:
                self._run_readiness_probes_sync(
                    candidate, self._graph_probe, self._vector_probe
                )

        try:
            published = await self.write(
                lambda: self.generations.publish(manifest, validate_candidate)
            )
        except BaseException as publication_error:
            # The native publication is non-preemptive and `_run_sync` waits
            # for it before surfacing interruption. Re-read the authoritative
            # pointer for every failure because os.replace() can succeed before
            # a later directory fsync reports an error.
            try:
                active = await self._run_sync("control", self.generations.recover)
            except BaseException:
                self._active_generation = None
                self._probe_generation = None
                self._probe_error = "PublicationReconciliationError"
                raise publication_error
            self._active_generation = active
            if (
                active is not None
                and active.generation_id == manifest.generation_id
                and self._graph_probe is not None
            ):
                self._probe_generation = (
                    active.generation_id
                    if isinstance(publication_error, asyncio.CancelledError)
                    else None
                )
                self._last_probe_at = self._timestamp()
                self._probe_error = (
                    None
                    if isinstance(publication_error, asyncio.CancelledError)
                    else type(publication_error).__name__
                )
            raise publication_error
        self._active_generation = published
        if self._graph_probe is not None:
            self._probe_generation = published.generation_id
            self._last_probe_at = self._timestamp()
            self._probe_error = None
        return published

    @staticmethod
    def _run_readiness_probes_sync(
        manifest: GenerationManifest,
        graph_probe: Callable[[GenerationManifest], Any],
        vector_probe: Callable[[GenerationManifest], Any],
    ) -> None:
        for probe in (graph_probe, vector_probe):
            result = probe(manifest)
            if inspect.isawaitable(result):
                close = getattr(result, "close", None)
                if callable(close):
                    close()
                raise TypeError("gateway readiness probes must be synchronous")

    async def refresh_readiness(
        self,
        graph_probe: Callable[[GenerationManifest], Any],
        vector_probe: Callable[[GenerationManifest], Any] | None,
    ) -> StoreHealth:
        """Refresh cached representative graph/vector probes on control capacity.

        Probe failures do not roll back an already durable publication. They
        fail readiness closed and preserve the previous generation as the
        manifest authority so operators can choose rollback explicitly.
        """

        if vector_probe is None:
            raise ValueError("a vector readiness probe is required")

        try:
            _, freshness = await self.query(
                lambda manifest: self._run_readiness_probes_sync(
                    manifest, graph_probe, vector_probe
                ),
                lane="control",
            )
        except Exception as exc:
            self._probe_generation = None
            self._last_probe_at = self._timestamp()
            self._probe_error = type(exc).__name__
        else:
            self._probe_generation = freshness.served_generation
            self._last_probe_at = self._timestamp()
            self._probe_error = None
        return self.health()

    async def retire(self, manifest: GenerationManifest) -> bool:
        """Retire only a non-active generation with no pinned readers."""
        async def run() -> bool:
            return await self._run_sync("control", self.generations.retire, manifest)

        return await self._lanes["control"].run(run)

    async def submit_ingest(
        self,
        *,
        idempotency_key: str,
        source_revision: str,
        estimated_bytes: int = 0,
    ) -> IngestionJob:
        if not idempotency_key.strip() or not source_revision.strip():
            raise ValueError("idempotency_key and source_revision are required")
        if estimated_bytes < 0:
            raise ValueError("estimated_bytes cannot be negative")
        key = (self.target.value, idempotency_key)
        async with self._job_changed:
            if not self._accepting_jobs:
                raise StoreGatewayError(
                    GatewayErrorCode.STORE_MAINTENANCE,
                    "store owner is not accepting ingestion",
                    retryable=True,
                )
            existing_id = self._job_keys.get(key)
            if existing_id is not None:
                existing = self._jobs[existing_id]
                if existing.source_revision != source_revision:
                    raise StoreGatewayError(
                        GatewayErrorCode.INGESTION_ALREADY_RUNNING,
                        "idempotency key is already bound to another source revision",
                        retryable=False,
                        details={"job_id": existing.job_id},
                    )
                return existing
            active_jobs = [job for job in self._jobs.values() if not job.state.terminal]
            queued_bytes = sum(
                int(job.detail.get("estimated_bytes", 0)) for job in active_jobs
            )
            if (
                len(active_jobs) >= self.limits.write.max_queue_items
                or queued_bytes + estimated_bytes > self.limits.write.max_queue_bytes
            ):
                raise StoreGatewayError(
                    GatewayErrorCode.OVERLOADED,
                    "ingestion queue is full",
                    retryable=True,
                    retry_after_ms=100,
                    details={
                        "queued_items": len(active_jobs),
                        "queued_bytes": queued_bytes,
                        "capacity": self.limits.write.max_queue_items,
                        "byte_capacity": self.limits.write.max_queue_bytes,
                    },
                )
            job = IngestionJob(
                job_id=uuid.uuid4().hex,
                target=self.target,
                idempotency_key=idempotency_key,
                source_revision=source_revision,
                queue_position=sum(
                    1
                    for value in self._jobs.values()
                    if value.state is IngestionJobState.QUEUED
                ),
                detail={"estimated_bytes": estimated_bytes},
            )
            self._jobs[job.job_id] = job
            self._job_keys[key] = job.job_id
            self._job_changed.notify_all()
        await self._persist_jobs()
        return job

    async def update_job(self, job_id: str, state: IngestionJobState, **changes: Any) -> IngestionJob:
        async with self._job_changed:
            if not self._accepting_jobs:
                raise StoreGatewayError(
                    GatewayErrorCode.STORE_MAINTENANCE,
                    "store owner is draining ingestion state",
                    retryable=True,
                )
            job = self._jobs[job_id]
            if job.state.terminal and state is not job.state:
                raise ValueError("terminal ingestion jobs cannot transition")
            if (
                state is not job.state
                and state not in _JOB_TRANSITIONS.get(job.state, frozenset())
            ):
                raise ValueError(
                    f"invalid ingestion job transition: {job.state.value} -> {state.value}"
                )
            updated = job.with_state(state, **changes)
            try:
                json.dumps(updated.to_dict())
            except (TypeError, ValueError) as exc:
                raise ValueError("ingestion job details must be JSON serializable") from exc
            self._jobs[job_id] = updated
            self._job_changed.notify_all()
        await self._persist_jobs()
        return updated

    async def get_ingestion_status(self, job_id: str) -> IngestionJob | None:
        async with self._job_lock:
            return self._jobs.get(job_id)

    async def cancel_ingest(self, job_id: str) -> IngestionJob | None:
        """Cancel queued work or record cancellation for a running sync call."""

        async with self._job_changed:
            if not self._accepting_jobs:
                raise StoreGatewayError(
                    GatewayErrorCode.STORE_MAINTENANCE,
                    "store owner is draining ingestion state",
                    retryable=True,
                )
            job = self._jobs.get(job_id)
            if job is None or job.state.terminal:
                return job
            now = job.cancel_requested_at or self._timestamp()
            if job.state in {IngestionJobState.QUEUED, IngestionJobState.PREPARING}:
                updated = job.with_state(
                    IngestionJobState.CANCELLED,
                    cancel_requested_at=now,
                    queue_position=None,
                )
            else:
                updated = job.with_state(
                    job.state,
                    cancel_requested_at=now,
                )
            self._jobs[job_id] = updated
            self._job_changed.notify_all()
        await self._persist_jobs()
        return updated

    async def wait_for_ingestion(
        self, job_id: str, *, timeout_seconds: float | None = None
    ) -> IngestionJob | None:
        """Wait for a terminal job state within the caller's bounded timeout."""

        async def wait() -> IngestionJob | None:
            async with self._job_changed:
                while True:
                    job = self._jobs.get(job_id)
                    if job is None or job.state.terminal:
                        return job
                    await self._job_changed.wait()

        if timeout_seconds is None:
            return await wait()
        if timeout_seconds <= 0:
            return await self.get_ingestion_status(job_id)
        try:
            return await asyncio.wait_for(wait(), timeout=timeout_seconds)
        except TimeoutError:
            return await self.get_ingestion_status(job_id)

    @staticmethod
    def _timestamp() -> str:
        from .contracts import utc_now

        return utc_now()

    async def _persist_jobs(self) -> None:
        if not self._executors:
            return
        async with self._job_persist_lock:
            async with self._job_lock:
                payload = {
                    "schema_version": 1,
                    "target": self.target.value,
                    "jobs": [job.to_dict() for job in self._jobs.values()],
                }
            encoded_bytes = len(json.dumps(payload, sort_keys=True).encode("utf-8"))

            async def persist() -> None:
                await self._run_sync("control", self._write_jobs_file, payload)

            await self._lanes["control"].run(
                persist,
                estimated_bytes=encoded_bytes,
            )

    def _write_jobs_file(self, payload: dict[str, Any]) -> None:
        self._jobs_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._jobs_path.with_suffix(".tmp")
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self._jobs_path)
        if os.name != "nt":
            directory_fd = os.open(str(self._jobs_path.parent), os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)

    async def _load_jobs(self) -> None:
        try:
            payload = await self._run_sync(
                "control", self._read_jobs_file
            )
        except FileNotFoundError:
            return
        if payload.get("schema_version") != 1 or payload.get("target") != self.target.value:
            raise ValueError("ingestion job store does not match this physical target")
        recovered: dict[str, IngestionJob] = {}
        keys: dict[tuple[str, str], str] = {}
        for item in payload.get("jobs", []):
            job = IngestionJob.from_dict(item)
            if job.target != self.target:
                raise ValueError("ingestion job targets a different physical store")
            if job.state is IngestionJobState.PREPARING:
                job = job.with_state(
                    IngestionJobState.QUEUED,
                    detail={**job.detail, "recovery": "preparation_requeued_after_restart"},
                )
            elif job.state is IngestionJobState.PUBLISHING:
                active = self._active_generation
                if (
                    active is not None
                    and job.generation_id == active.generation_id
                    and job.source_revision == active.source_revision
                ):
                    job = job.with_state(
                        IngestionJobState.COMPLETED,
                        queue_position=None,
                        detail={**job.detail, "recovery": "publication_reconciled"},
                    )
                else:
                    job = job.with_state(
                        IngestionJobState.AMBIGUOUS,
                        detail={
                            **job.detail,
                            "recovery": "publication_not_selected_by_active_manifest",
                        },
                    )
            elif job.state in {
                IngestionJobState.WRITING,
                IngestionJobState.VALIDATING,
            }:
                job = job.with_state(
                    IngestionJobState.AMBIGUOUS,
                    detail={**job.detail, "recovery": "owner_restarted_during_store_operation"},
                )
            recovered[job.job_id] = job
            keys[(self.target.value, job.idempotency_key)] = job.job_id
        async with self._job_changed:
            self._jobs = recovered
            self._job_keys = keys
            self._job_changed.notify_all()
        await self._persist_jobs()

    def _read_jobs_file(self) -> dict[str, Any]:
        payload = json.loads(self._jobs_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            raise ValueError("invalid ingestion job store")
        return payload

    def health(self) -> StoreHealth:
        active = self._active_generation
        probes_ready = (
            self._graph_probe is not None
            and self._vector_probe is not None
            and active is not None
            and self._probe_generation == active.generation_id
            and self._probe_error is None
        )
        return StoreHealth(
            target=self.target,
            lifecycle=self.lifecycle,
            active_generation=active.generation_id if active else None,
            active_readers=int(
                sum(self._lanes[name].snapshot["active"] for name in ("graph", "vector"))
            ),
            queued_reads=int(
                sum(
                    self._lanes[name].snapshot["queued_items"]
                    for name in ("graph", "vector")
                )
            ),
            queued_writes=int(self._lanes["write"].snapshot["queued_items"]),
            ready=(
                self.lifecycle is OwnerLifecycleState.READY
                and active is not None
                and probes_ready
            ),
            probe_generation=self._probe_generation,
            last_probe_at=self._last_probe_at,
            probe_error=self._probe_error,
        )

    def metrics(self) -> dict[str, Any]:
        """Return a bounded in-memory projection without probing storage."""

        return {
            "lifecycle": self.lifecycle.value,
            "active_generation": (
                self._active_generation.generation_id
                if self._active_generation
                else None
            ),
            "ready": self.health().ready,
            "probe_generation": self._probe_generation,
            "last_probe_at": self._last_probe_at,
            "probe_error": self._probe_error,
            "lanes": {name: lane.snapshot for name, lane in self._lanes.items()},
            "jobs": {
                state.value: sum(1 for job in self._jobs.values() if job.state is state)
                for state in IngestionJobState
            },
        }
