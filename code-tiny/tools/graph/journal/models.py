"""Versioned contracts for the durable graph-write journal."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Sequence


JOURNAL_SCHEMA_VERSION = 2
CONTRACT_VERSION = 1


class RunStatus(str, Enum):
    OPEN = "open"
    DRAINING = "draining"
    DRAINED = "drained"
    BLOCKED = "blocked"
    DEAD_LETTERED = "dead_lettered"
    QUARANTINED = "quarantined"
    FAILED = "failed"


class BatchStatus(str, Enum):
    PENDING = "pending"
    LEASED = "leased"
    RECONCILING = "reconciling"
    RETRY_WAIT = "retry_wait"
    DONE = "done"
    BLOCKED = "blocked"
    DEAD_LETTER = "dead_letter"


class OperationPhase(str, Enum):
    NODES = "nodes"
    RELATIONSHIPS = "relationships"
    CALLS = "calls"
    CUSTOM = "custom"


class BarrierStatus(str, Enum):
    OPEN = "open"
    PRODUCED = "produced"
    DRAINED = "drained"


class RetryClass(str, Enum):
    TRANSIENT = "transient"
    AMBIGUOUS = "ambiguous"
    INTEGRITY = "integrity"
    INCOMPATIBLE = "incompatible"
    TERMINAL = "terminal"


class TerminalErrorCode(str, Enum):
    INCOMPATIBLE_SCHEMA = "incompatible_schema"
    JOURNAL_CORRUPT = "journal_corrupt"
    DISK_FULL = "disk_full"
    UNSAFE_PLACEMENT = "unsafe_placement"
    PERMISSION_DENIED = "permission_denied"
    ARTIFACT_HASH_MISMATCH = "artifact_hash_mismatch"
    ADMISSION_REJECTED = "admission_rejected"
    INVALID_TRANSITION = "invalid_transition"
    STALE_FENCE = "stale_fence"
    MAX_ATTEMPTS = "max_attempts"
    INVALID_CONTRACT = "invalid_contract"


class JournalError(RuntimeError):
    """Fail-closed journal error with a stable machine-readable code."""

    def __init__(
        self,
        code: TerminalErrorCode,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


class StaleFenceError(JournalError):
    def __init__(self, job_id: str) -> None:
        super().__init__(
            TerminalErrorCode.STALE_FENCE,
            f"batch {job_id} is not owned by the supplied fencing token",
            details={"job_id": job_id},
        )


@dataclass(frozen=True)
class RunMetadata:
    """All compatibility inputs required to resume a journal run."""

    project_id: str
    scope_id: str
    source_revision: str
    source_snapshot: str
    physical_target: str
    generation: str
    parser: str
    parser_version: str
    schema_fingerprint: str
    query_shape_version: str
    operation_versions: Mapping[str, int] = field(default_factory=dict)
    contract_version: int = CONTRACT_VERSION

    def __post_init__(self) -> None:
        required = (
            "project_id",
            "scope_id",
            "source_revision",
            "source_snapshot",
            "physical_target",
            "generation",
            "parser",
            "parser_version",
            "schema_fingerprint",
            "query_shape_version",
        )
        for name in required:
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} must not be empty")
        versions = dict(sorted(self.operation_versions.items()))
        if any(
            not str(name).strip() or not isinstance(version, int) or version < 1
            for name, version in versions.items()
        ):
            raise ValueError(
                "operation versions require non-empty names and positive integer versions"
            )
        if self.contract_version < 1:
            raise ValueError("contract_version must be positive")
        object.__setattr__(self, "operation_versions", MappingProxyType(versions))

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "scope_id": self.scope_id,
            "source_revision": self.source_revision,
            "source_snapshot": self.source_snapshot,
            "physical_target": self.physical_target,
            "generation": self.generation,
            "parser": self.parser,
            "parser_version": self.parser_version,
            "schema_fingerprint": self.schema_fingerprint,
            "query_shape_version": self.query_shape_version,
            "operation_versions": dict(self.operation_versions),
            "contract_version": self.contract_version,
        }


@dataclass(frozen=True)
class ArtifactRef:
    sha256: str
    relative_path: str
    byte_count: int
    row_count: int


@dataclass(frozen=True)
class BatchSpec:
    phase: OperationPhase
    operation_key: str
    sequence: int
    artifact: ArtifactRef
    expected_count: int
    required_barriers: Sequence[str] = ()
    produced_barriers: Sequence[str] = ()
    max_attempts: int = 5
    operation: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.operation_key.strip():
            raise ValueError("operation_key must not be empty")
        if self.sequence < 0:
            raise ValueError("sequence must be non-negative")
        if self.expected_count < 0:
            raise ValueError("expected_count must be non-negative")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        required = tuple(sorted(set(self.required_barriers)))
        produced = tuple(sorted(set(self.produced_barriers)))
        if any(not name.strip() for name in (*required, *produced)):
            raise ValueError("barrier names must not be empty")
        overlap = set(required).intersection(produced)
        if overlap:
            raise ValueError(
                f"a batch cannot require and produce the same barrier: {sorted(overlap)}"
            )
        object.__setattr__(self, "required_barriers", required)
        object.__setattr__(self, "produced_barriers", produced)
        object.__setattr__(self, "operation", MappingProxyType(dict(self.operation)))


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    fingerprint: str
    metadata: RunMetadata
    status: RunStatus
    created_at: str
    updated_at: str
    retention_until: str
    error_code: TerminalErrorCode | None = None


@dataclass(frozen=True)
class BatchRecord:
    job_id: str
    run_id: str
    phase: OperationPhase
    operation_key: str
    sequence: int
    artifact: ArtifactRef
    expected_count: int
    status: BatchStatus
    attempt: int
    max_attempts: int
    fencing_token: str | None
    lease_until: str | None
    next_attempt_at: str | None
    required_barriers: tuple[str, ...]
    produced_barriers: tuple[str, ...]
    retry_class: RetryClass | None = None
    error_code: TerminalErrorCode | None = None
    operation: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BarrierRecord:
    run_id: str
    name: str
    status: BarrierStatus
    produced_count: int
    drained_count: int
    closed_at: str | None


@dataclass(frozen=True)
class JournalLimits:
    max_batches_per_run: int = 100_000
    max_payload_bytes_per_run: int = 4 * 1024 * 1024 * 1024
    max_artifact_bytes: int = 256 * 1024 * 1024
    max_journal_bytes: int = 512 * 1024 * 1024
    min_free_bytes: int = 512 * 1024 * 1024
    retention_seconds: int = 7 * 24 * 60 * 60
    busy_timeout_ms: int = 5_000
    wal_autocheckpoint_pages: int = 1_000

    def __post_init__(self) -> None:
        for name, value in vars(self).items():
            if not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
