"""Stable contracts for the embedded-store owner boundary.

These types deliberately describe *physical* storage.  A logical project ID
can share an owner with another project and must therefore never be used as a
lock, queue, or idempotency key by itself.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping


MANIFEST_SCHEMA_VERSION = 1


def utc_now() -> str:
    """Return a compact, timezone-aware timestamp suitable for manifests."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class GenerationState(str, Enum):
    BUILDING = "BUILDING"
    VALIDATING = "VALIDATING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    RETIRING = "RETIRING"
    RETIRED = "RETIRED"


class IngestionJobState(str, Enum):
    QUEUED = "QUEUED"
    PREPARING = "PREPARING"
    WRITING = "WRITING"
    VALIDATING = "VALIDATING"
    PUBLISHING = "PUBLISHING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    AMBIGUOUS = "AMBIGUOUS"
    SUPERSEDED = "SUPERSEDED"


class OwnerLifecycleState(str, Enum):
    STARTING = "STARTING"
    RECOVERING = "RECOVERING"
    WARMING = "WARMING"
    READY = "READY"
    DRAINING = "DRAINING"
    STOPPED = "STOPPED"


class GatewayErrorCode(str, Enum):
    OVERLOADED = "OVERLOADED"
    INGESTION_ALREADY_RUNNING = "INGESTION_ALREADY_RUNNING"
    STORE_MAINTENANCE = "STORE_MAINTENANCE"
    REQUEST_TOO_LARGE = "REQUEST_TOO_LARGE"
    STALE_GENERATION = "STALE_GENERATION"
    DEADLINE_EXCEEDED = "DEADLINE_EXCEEDED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class PerformanceProfile:
    """Validated owner limits; ``safe`` is intentionally conservative."""

    name: str = "safe"
    graph_readers: int = 1
    vector_readers: int = 1
    writer_slots: int = 1
    control_slots: int = 1
    max_queue_items: int = 32
    max_queue_bytes: int = 4 * 1024 * 1024
    request_timeout_seconds: float = 5.0
    disk_safety_fraction: float = 0.20

    def __post_init__(self) -> None:
        if self.name not in {"safe", "balanced", "custom"}:
            raise ValueError("performance profile must be safe, balanced, or custom")
        if min(self.graph_readers, self.vector_readers, self.writer_slots, self.control_slots) < 1:
            raise ValueError("all gateway lane capacities must be at least one")
        if self.writer_slots != 1:
            raise ValueError("embedded storage permits exactly one writer per physical target")
        if self.max_queue_items < 1 or self.max_queue_bytes < 1:
            raise ValueError("gateway queue capacities must be positive")
        if self.request_timeout_seconds <= 0:
            raise ValueError("request timeout must be positive")
        if not 0 < self.disk_safety_fraction < 1:
            raise ValueError("disk safety fraction must be between zero and one")


@dataclass(frozen=True, order=True)
class PhysicalTargetKey:
    """Canonical identity for a graph/vector pair owned by one process."""

    instance_id: str
    owner_id: str
    graph_path: str
    vector_path: str

    @classmethod
    def from_paths(
        cls, *, instance_id: str, owner_id: str, graph_path: Path, vector_path: Path
    ) -> "PhysicalTargetKey":
        return cls(
            instance_id=str(instance_id).strip().casefold(),
            owner_id=str(owner_id).strip().casefold(),
            graph_path=str(Path(graph_path).resolve()),
            vector_path=str(Path(vector_path).resolve()),
        )

    @property
    def canonical_paths(self) -> tuple[Path, Path]:
        graph_path, vector_path = sorted((Path(self.graph_path), Path(self.vector_path)))
        return graph_path, vector_path

    @property
    def value(self) -> str:
        return "|".join((self.instance_id, self.owner_id, self.graph_path, self.vector_path))


@dataclass(frozen=True)
class GenerationManifest:
    generation_id: str
    target: PhysicalTargetKey
    source_revision: str
    graph_path: str
    vector_path: str
    state: GenerationState = GenerationState.PUBLISHED
    created_at: str = field(default_factory=utc_now)
    validated_at: str | None = None
    published_at: str | None = None
    retired_at: str | None = None
    validation: Mapping[str, Any] = field(default_factory=dict)
    schema_version: int = MANIFEST_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["target"] = asdict(self.target)
        data["state"] = self.state.value
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "GenerationManifest":
        target_data = data.get("target")
        if not isinstance(target_data, Mapping):
            raise ValueError("generation manifest is missing a physical target")
        version = int(data.get("schema_version", 0))
        if version != MANIFEST_SCHEMA_VERSION:
            raise ValueError(f"unsupported generation manifest schema version: {version}")
        return cls(
            generation_id=str(data["generation_id"]),
            target=PhysicalTargetKey(**dict(target_data)),
            source_revision=str(data["source_revision"]),
            graph_path=str(data["graph_path"]),
            vector_path=str(data["vector_path"]),
            state=GenerationState(str(data.get("state", GenerationState.PUBLISHED.value))),
            created_at=str(data["created_at"]),
            validated_at=data.get("validated_at"),
            published_at=data.get("published_at"),
            retired_at=data.get("retired_at"),
            validation=dict(data.get("validation") or {}),
            schema_version=version,
        )


@dataclass(frozen=True)
class FreshnessMetadata:
    served_generation: str
    source_revision: str
    last_committed_at: str | None
    ingestion_state: IngestionJobState | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["ingestion_state"] = self.ingestion_state.value if self.ingestion_state else None
        return data


@dataclass(frozen=True)
class IngestionJob:
    job_id: str
    target: PhysicalTargetKey
    idempotency_key: str
    source_revision: str
    state: IngestionJobState = IngestionJobState.QUEUED
    submitted_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    queue_position: int | None = None
    generation_id: str | None = None
    detail: Mapping[str, Any] = field(default_factory=dict)

    def with_state(self, state: IngestionJobState, **changes: Any) -> "IngestionJob":
        data = asdict(self)
        data.update(changes, state=state, updated_at=utc_now())
        data["target"] = self.target
        return IngestionJob(**data)


@dataclass(frozen=True)
class StoreHealth:
    target: PhysicalTargetKey
    lifecycle: OwnerLifecycleState
    active_generation: str | None
    active_readers: int
    queued_reads: int
    queued_writes: int
    ready: bool
    updated_at: str = field(default_factory=utc_now)


class StoreGatewayError(RuntimeError):
    """A stable error that can be rendered by CLI and MCP callers."""

    def __init__(
        self,
        code: GatewayErrorCode,
        message: str,
        *,
        retryable: bool = False,
        retry_after_ms: int | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.retry_after_ms = retry_after_ms
        self.details = dict(details or {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "message": str(self),
            "retryable": self.retryable,
            "retry_after_ms": self.retry_after_ms,
            **self.details,
        }
