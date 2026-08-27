"""Bounded async admission for a single embedded-store owner."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import monotonic
from typing import Awaitable, Callable, TypeVar

from .contracts import GatewayErrorCode, StoreGatewayError


T = TypeVar("T")


@dataclass(frozen=True)
class LaneLimits:
    concurrency: int = 1
    max_queue_items: int = 32
    max_queue_bytes: int = 4 * 1024 * 1024

    def __post_init__(self) -> None:
        if self.concurrency < 1:
            raise ValueError("lane concurrency must be at least one")
        if self.max_queue_items < 0 or self.max_queue_bytes < 0:
            raise ValueError("lane queue limits cannot be negative")


class BoundedLane:
    """A FIFO lane with explicit count and byte budgets.

    The semaphore controls execution while accounting is held only around state
    mutation. Awaited work is always performed without an internal state lock.
    """

    def __init__(self, name: str, limits: LaneLimits) -> None:
        self.name = name
        self.limits = limits
        self._semaphore = asyncio.Semaphore(limits.concurrency)
        self._lock = asyncio.Lock()
        self._queued_items = 0
        self._queued_bytes = 0
        self._active = 0
        self._accepted = 0
        self._completed = 0
        self._rejected = 0
        self._timed_out = 0
        self._cancelled = 0
        self._queue_wait_seconds = 0.0
        self._max_queue_wait_seconds = 0.0
        self._idle = asyncio.Event()
        self._idle.set()

    @property
    def snapshot(self) -> dict[str, int | float]:
        return {
            "active": self._active,
            "queued_items": self._queued_items,
            "queued_bytes": self._queued_bytes,
            "capacity": self.limits.max_queue_items,
            "byte_capacity": self.limits.max_queue_bytes,
            "accepted": self._accepted,
            "completed": self._completed,
            "rejected": self._rejected,
            "timed_out": self._timed_out,
            "cancelled": self._cancelled,
            "queue_wait_seconds": self._queue_wait_seconds,
            "max_queue_wait_seconds": self._max_queue_wait_seconds,
        }

    async def run(
        self,
        operation: Callable[[], Awaitable[T]],
        *,
        estimated_bytes: int = 0,
        deadline: float | None = None,
    ) -> T:
        if estimated_bytes < 0:
            raise ValueError("estimated_bytes cannot be negative")
        async with self._lock:
            if self._queued_items >= self.limits.max_queue_items or (
                self._queued_bytes + estimated_bytes > self.limits.max_queue_bytes
            ):
                self._rejected += 1
                raise StoreGatewayError(
                    GatewayErrorCode.OVERLOADED,
                    f"{self.name} admission queue is full",
                    retryable=True,
                    retry_after_ms=100,
                    details=self.snapshot,
                )
            self._queued_items += 1
            self._queued_bytes += estimated_bytes
            self._accepted += 1
            self._idle.clear()
        queued_at = monotonic()
        acquired = False
        try:
            timeout = None if deadline is None else max(0.0, deadline - monotonic())
            try:
                if timeout is None:
                    await self._semaphore.acquire()
                else:
                    await asyncio.wait_for(self._semaphore.acquire(), timeout=timeout)
            except TimeoutError as exc:
                async with self._lock:
                    self._timed_out += 1
                raise StoreGatewayError(
                    GatewayErrorCode.DEADLINE_EXCEEDED,
                    f"{self.name} admission deadline elapsed",
                    retryable=True,
                    details=self.snapshot,
                ) from exc
            acquired = True
            async with self._lock:
                waited = monotonic() - queued_at
                self._queued_items -= 1
                self._queued_bytes -= estimated_bytes
                self._active += 1
                self._queue_wait_seconds += waited
                self._max_queue_wait_seconds = max(
                    self._max_queue_wait_seconds, waited
                )
            try:
                result = await operation()
            except asyncio.CancelledError:
                async with self._lock:
                    self._cancelled += 1
                raise
            else:
                async with self._lock:
                    self._completed += 1
                return result
        finally:
            if acquired:
                async with self._lock:
                    self._active -= 1
                    if not self._active and not self._queued_items:
                        self._idle.set()
                self._semaphore.release()
            else:
                async with self._lock:
                    self._queued_items -= 1
                    self._queued_bytes -= estimated_bytes
                    if not self._active and not self._queued_items:
                        self._idle.set()

    async def wait_idle(self) -> None:
        """Wait until all accepted work has finished or left the queue."""
        await self._idle.wait()
