"""Provider-neutral reliability outcomes shared by analyzers and orchestrators.

The types in this module are deliberately JSON-only so a child process can
persist a result before exiting and a parent can make retry/rendering decisions
without interpreting log text.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


RELIABILITY_SCHEMA_VERSION = "1.0"
MAX_DETAIL_ITEMS = 100
MAX_DETAIL_STRING_CHARS = 2_048
MAX_RESULT_BYTES = 2 * 1024 * 1024

_SENSITIVE_KEY_RE = re.compile(
    r"(?:authorization|cookie|password|passwd|secret|token|api[_-]?key|private[_-]?key)",
    re.IGNORECASE,
)


class RunPhase(str, Enum):
    CREATED = "created"
    DISCOVERING = "discovering"
    PARSING = "parsing"
    VALIDATING = "validating"
    PREPARING = "preparing"
    WRITING_NODES = "writing_nodes"
    VERIFYING_NODES = "verifying_nodes"
    WRITING_RELATIONS = "writing_relations"
    VERIFYING_GENERATION = "verifying_generation"
    PUBLISHING = "publishing"
    RECONCILING = "reconciling"
    FINISHED = "finished"


class RunOutcome(str, Enum):
    SUCCESS = "success"
    SUCCESS_WITH_QUARANTINE = "success_with_quarantine"
    NO_CHANGES = "no_changes"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_TERMINAL = "failed_terminal"
    CANCELLED = "cancelled"
    AMBIGUOUS = "ambiguous"


class FailureClass(str, Enum):
    INPUT_VALIDATION = "input_validation"
    PARSER_ISOLATION = "parser_isolation"
    SOURCE_CHANGED = "source_changed"
    LOCK = "lock"
    CAPACITY = "capacity"
    STORAGE_UNAVAILABLE = "storage_unavailable"
    TIMEOUT = "timeout"
    AMBIGUOUS_MUTATION = "ambiguous_mutation"
    INTEGRITY = "integrity"
    JOURNAL_RECOVERY = "journal_recovery"
    CONFIGURATION = "configuration"
    INTERNAL_DEFECT = "internal_defect"


class ReliabilityExitCode(IntEnum):
    SUCCESS = 0
    LEGACY_FAILURE = 1
    LEGACY_LOCK_BUSY = 2
    TERMINAL_FAILURE = 10
    RETRYABLE_FAILURE = 11
    AMBIGUOUS = 12
    CANCELLED = 13
    INTERNAL_DEFECT = 14


_ALLOWED_TRANSITIONS: Mapping[RunPhase, frozenset[RunPhase]] = {
    RunPhase.CREATED: frozenset({RunPhase.DISCOVERING}),
    RunPhase.DISCOVERING: frozenset({RunPhase.PARSING, RunPhase.VALIDATING, RunPhase.FINISHED}),
    RunPhase.PARSING: frozenset({RunPhase.VALIDATING, RunPhase.FINISHED}),
    RunPhase.VALIDATING: frozenset({RunPhase.PREPARING, RunPhase.FINISHED}),
    RunPhase.PREPARING: frozenset({RunPhase.WRITING_NODES, RunPhase.FINISHED}),
    RunPhase.WRITING_NODES: frozenset({RunPhase.VERIFYING_NODES, RunPhase.FINISHED}),
    RunPhase.VERIFYING_NODES: frozenset({RunPhase.WRITING_RELATIONS, RunPhase.FINISHED}),
    RunPhase.WRITING_RELATIONS: frozenset({RunPhase.VERIFYING_GENERATION, RunPhase.FINISHED}),
    RunPhase.VERIFYING_GENERATION: frozenset({RunPhase.PUBLISHING, RunPhase.FINISHED}),
    RunPhase.PUBLISHING: frozenset({RunPhase.FINISHED}),
    RunPhase.RECONCILING: frozenset(
        {
            RunPhase.WRITING_NODES,
            RunPhase.VERIFYING_NODES,
            RunPhase.WRITING_RELATIONS,
            RunPhase.VERIFYING_GENERATION,
            RunPhase.PUBLISHING,
            RunPhase.FINISHED,
        }
    ),
    RunPhase.FINISHED: frozenset(),
}


def validate_transition(current: RunPhase, target: RunPhase) -> None:
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise ValueError(f"invalid reliability phase transition: {current.value} -> {target.value}")


def _bounded_json_value(value: Any, *, key: str = "", depth: int = 0) -> Any:
    if _SENSITIVE_KEY_RE.search(key):
        return "<redacted>"
    if depth > 8:
        return "<depth-limit>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, str):
        return value[:MAX_DETAIL_STRING_CHARS]
    if isinstance(value, Mapping):
        bounded: dict[str, Any] = {}
        for index, (item_key, item_value) in enumerate(sorted(value.items(), key=lambda item: str(item[0]))):
            if index >= MAX_DETAIL_ITEMS:
                bounded["_truncated"] = True
                break
            text_key = str(item_key)[:128]
            bounded[text_key] = _bounded_json_value(item_value, key=text_key, depth=depth + 1)
        return bounded
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        items = list(value[:MAX_DETAIL_ITEMS])
        bounded_items = [_bounded_json_value(item, depth=depth + 1) for item in items]
        if len(value) > MAX_DETAIL_ITEMS:
            bounded_items.append("<truncated>")
        return bounded_items
    return str(value)[:MAX_DETAIL_STRING_CHARS]


@dataclass(frozen=True)
class ArtifactReference:
    kind: str
    path: str
    sha256: str = ""
    byte_count: int = 0
    item_count: int = 0

    def __post_init__(self) -> None:
        if not self.kind.strip() or not self.path.strip():
            raise ValueError("artifact kind and path must not be empty")
        if self.byte_count < 0 or self.item_count < 0:
            raise ValueError("artifact counts must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "path": self.path,
            "sha256": self.sha256,
            "byte_count": self.byte_count,
            "item_count": self.item_count,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ArtifactReference":
        return cls(
            kind=str(payload["kind"]),
            path=str(payload["path"]),
            sha256=str(payload.get("sha256") or ""),
            byte_count=int(payload.get("byte_count") or 0),
            item_count=int(payload.get("item_count") or 0),
        )


@dataclass(frozen=True)
class FailureRecord:
    code: str
    failure_class: FailureClass
    phase: RunPhase
    component: str
    retryable: bool
    run_id: str
    correlation_id: str
    summary: str
    safe_action: str
    artifact_references: tuple[ArtifactReference, ...] = ()
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        required = (self.code, self.component, self.run_id, self.correlation_id, self.summary, self.safe_action)
        if any(not str(value).strip() for value in required):
            raise ValueError("failure records require stable identifiers, summary, and safe action")
        object.__setattr__(self, "details", _bounded_json_value(dict(self.details)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "class": self.failure_class.value,
            "phase": self.phase.value,
            "component": self.component,
            "retryable": self.retryable,
            "run_id": self.run_id,
            "correlation_id": self.correlation_id,
            "summary": self.summary[:MAX_DETAIL_STRING_CHARS],
            "safe_action": self.safe_action[:MAX_DETAIL_STRING_CHARS],
            "artifacts": [artifact.to_dict() for artifact in self.artifact_references],
            "details": _bounded_json_value(self.details),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "FailureRecord":
        return cls(
            code=str(payload["code"]),
            failure_class=FailureClass(str(payload["class"])),
            phase=RunPhase(str(payload["phase"])),
            component=str(payload["component"]),
            retryable=bool(payload.get("retryable", False)),
            run_id=str(payload["run_id"]),
            correlation_id=str(payload["correlation_id"]),
            summary=str(payload["summary"]),
            safe_action=str(payload["safe_action"]),
            artifact_references=tuple(
                ArtifactReference.from_dict(item) for item in payload.get("artifacts", [])
            ),
            details=dict(payload.get("details") or {}),
        )


@dataclass(frozen=True)
class PhaseResult:
    phase: RunPhase
    expected: int = 0
    accepted: int = 0
    quarantined: int = 0
    rejected: int = 0
    attempted: int = 0
    persisted: int = 0
    unresolved: int = 0
    ambiguous: int = 0
    elapsed_ms: float = 0.0
    fingerprint: str = ""

    def __post_init__(self) -> None:
        counts = (
            self.expected,
            self.accepted,
            self.quarantined,
            self.rejected,
            self.attempted,
            self.persisted,
            self.unresolved,
            self.ambiguous,
        )
        if any(value < 0 for value in counts):
            raise ValueError("phase accounting counts must be non-negative")
        if self.expected != self.accepted + self.quarantined + self.rejected:
            raise ValueError("phase discovery accounting is not balanced")
        if self.attempted != self.persisted + self.unresolved + self.ambiguous:
            raise ValueError("phase persistence accounting is not balanced")

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase.value,
            "expected": self.expected,
            "accepted": self.accepted,
            "quarantined": self.quarantined,
            "rejected": self.rejected,
            "attempted": self.attempted,
            "persisted": self.persisted,
            "unresolved": self.unresolved,
            "ambiguous": self.ambiguous,
            "elapsed_ms": round(self.elapsed_ms, 3),
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PhaseResult":
        return cls(
            phase=RunPhase(str(payload["phase"])),
            expected=int(payload.get("expected") or 0),
            accepted=int(payload.get("accepted") or 0),
            quarantined=int(payload.get("quarantined") or 0),
            rejected=int(payload.get("rejected") or 0),
            attempted=int(payload.get("attempted") or 0),
            persisted=int(payload.get("persisted") or 0),
            unresolved=int(payload.get("unresolved") or 0),
            ambiguous=int(payload.get("ambiguous") or 0),
            elapsed_ms=float(payload.get("elapsed_ms") or 0.0),
            fingerprint=str(payload.get("fingerprint") or ""),
        )


@dataclass(frozen=True)
class RunResult:
    run_id: str
    correlation_id: str
    outcome: RunOutcome
    phase: RunPhase
    component: str
    failure: FailureRecord | None = None
    phase_results: tuple[PhaseResult, ...] = ()
    artifacts: tuple[ArtifactReference, ...] = ()
    current_generation: str = ""
    retained_generation: str = ""
    retry_after_seconds: float | None = None
    started_at: str = ""
    finished_at: str = ""
    schema_version: str = RELIABILITY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.run_id.strip() or not self.correlation_id.strip() or not self.component.strip():
            raise ValueError("run result requires run, correlation, and component identifiers")
        failed = self.outcome in {
            RunOutcome.FAILED_RETRYABLE,
            RunOutcome.FAILED_TERMINAL,
            RunOutcome.AMBIGUOUS,
        }
        if failed != (self.failure is not None):
            raise ValueError("failed outcomes require exactly one failure record")
        if self.outcome is RunOutcome.FAILED_RETRYABLE and not self.failure.retryable:  # type: ignore[union-attr]
            raise ValueError("failed_retryable requires a retryable failure record")
        if self.outcome is RunOutcome.AMBIGUOUS and self.failure.retryable:  # type: ignore[union-attr]
            raise ValueError("ambiguous mutations must reconcile before retry")
        if self.retry_after_seconds is not None and self.retry_after_seconds < 0:
            raise ValueError("retry_after_seconds must be non-negative")

    @property
    def should_retry(self) -> bool:
        return (
            self.outcome is RunOutcome.FAILED_RETRYABLE
            and self.failure is not None
            and self.failure.retryable
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "correlation_id": self.correlation_id,
            "outcome": self.outcome.value,
            "phase": self.phase.value,
            "component": self.component,
            "failure": self.failure.to_dict() if self.failure else None,
            "phase_results": [result.to_dict() for result in self.phase_results],
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "current_generation": self.current_generation,
            "retained_generation": self.retained_generation,
            "retry_after_seconds": self.retry_after_seconds,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RunResult":
        if "run_result" in payload:
            nested = payload["run_result"]
            if not isinstance(nested, Mapping):
                raise ValueError("run_result wrapper must contain an object")
            payload = nested
        version = str(payload.get("schema_version") or "")
        if version.split(".", 1)[0] != RELIABILITY_SCHEMA_VERSION.split(".", 1)[0]:
            raise ValueError(f"unsupported reliability schema version: {version or '<missing>'}")
        failure_payload = payload.get("failure")
        return cls(
            run_id=str(payload["run_id"]),
            correlation_id=str(payload["correlation_id"]),
            outcome=RunOutcome(str(payload["outcome"])),
            phase=RunPhase(str(payload["phase"])),
            component=str(payload["component"]),
            failure=(
                FailureRecord.from_dict(failure_payload)
                if isinstance(failure_payload, Mapping)
                else None
            ),
            phase_results=tuple(
                PhaseResult.from_dict(item) for item in payload.get("phase_results", [])
            ),
            artifacts=tuple(
                ArtifactReference.from_dict(item) for item in payload.get("artifacts", [])
            ),
            current_generation=str(payload.get("current_generation") or ""),
            retained_generation=str(payload.get("retained_generation") or ""),
            retry_after_seconds=(
                float(payload["retry_after_seconds"])
                if payload.get("retry_after_seconds") is not None
                else None
            ),
            started_at=str(payload.get("started_at") or ""),
            finished_at=str(payload.get("finished_at") or ""),
            schema_version=version,
        )


def load_run_result(path: str | os.PathLike[str]) -> RunResult:
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, Mapping):
        raise ValueError("run result artifact must contain a JSON object")
    return RunResult.from_dict(payload)


def atomic_write_run_result(
    path: str | os.PathLike[str], result: RunResult, *, max_bytes: int = MAX_RESULT_BYTES
) -> ArtifactReference:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(result.to_dict(), ensure_ascii=True, sort_keys=True, indent=2) + "\n").encode("utf-8")
    if len(encoded) > max_bytes:
        raise ValueError(f"run result exceeds byte cap: {len(encoded)} > {max_bytes}")
    temporary = target.with_name(f".{target.name}.{os.getpid()}.{time.time_ns()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            descriptor = -1
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        os.chmod(target, 0o600)
        if os.name != "nt":
            # Windows cannot open a directory with os.open(O_RDONLY); the
            # durability sync is POSIX-only.
            directory_fd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
    return ArtifactReference(
        kind="run_result",
        path=str(target),
        sha256=hashlib.sha256(encoded).hexdigest(),
        byte_count=len(encoded),
        item_count=1,
    )


def exit_code_for(result: RunResult, *, observe_only: bool = True) -> int:
    if result.outcome in {
        RunOutcome.SUCCESS,
        RunOutcome.SUCCESS_WITH_QUARANTINE,
        RunOutcome.NO_CHANGES,
    }:
        return int(ReliabilityExitCode.SUCCESS)
    if observe_only:
        if result.failure and result.failure.failure_class is FailureClass.LOCK:
            return int(ReliabilityExitCode.LEGACY_LOCK_BUSY)
        return int(ReliabilityExitCode.LEGACY_FAILURE)
    if result.outcome is RunOutcome.FAILED_RETRYABLE:
        return int(ReliabilityExitCode.RETRYABLE_FAILURE)
    if result.outcome is RunOutcome.AMBIGUOUS:
        return int(ReliabilityExitCode.AMBIGUOUS)
    if result.outcome is RunOutcome.CANCELLED:
        return int(ReliabilityExitCode.CANCELLED)
    if result.failure and result.failure.failure_class is FailureClass.INTERNAL_DEFECT:
        return int(ReliabilityExitCode.INTERNAL_DEFECT)
    return int(ReliabilityExitCode.TERMINAL_FAILURE)


def fingerprint_rows(rows: Iterable[Mapping[str, Any]]) -> str:
    hasher = hashlib.sha256()
    for row in sorted(
        (json.dumps(_bounded_json_value(dict(item)), sort_keys=True, separators=(",", ":")) for item in rows)
    ):
        hasher.update(row.encode("utf-8"))
        hasher.update(b"\n")
    return hasher.hexdigest()

