"""Canonical identities for resumable graph-write work."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from .models import OperationPhase, RunMetadata


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def run_fingerprint(metadata: RunMetadata | Mapping[str, Any]) -> str:
    payload = (
        metadata.to_dict() if isinstance(metadata, RunMetadata) else dict(metadata)
    )
    return sha256_hex(canonical_json(payload))


def run_id(metadata: RunMetadata | Mapping[str, Any]) -> str:
    return f"run_{run_fingerprint(metadata)}"


def deterministic_job_id(
    *,
    run_fingerprint_value: str,
    phase: OperationPhase | str,
    operation_key: str,
    sequence: int,
    payload_sha256: str,
) -> str:
    phase_value = phase.value if isinstance(phase, OperationPhase) else str(phase)
    identity = {
        "run_fingerprint": run_fingerprint_value,
        "phase": phase_value,
        "operation_key": operation_key,
        "sequence": sequence,
        "payload_sha256": payload_sha256,
    }
    return f"job_{sha256_hex(canonical_json(identity))}"
