from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable

from .models import ProgramMapping


def _records(payload: Any) -> Iterable[dict[str, Any]]:
    if isinstance(payload, list):
        return (item for item in payload if isinstance(item, dict))
    if isinstance(payload, dict) and isinstance(payload.get("mappings"), list):
        return (item for item in payload["mappings"] if isinstance(item, dict))
    raise ValueError("program mapping ledger must be a list or contain a mappings list")


def _canonical_source_path(raw_path: str, root: str) -> str:
    root_real = os.path.realpath(os.path.abspath(root))
    candidate = Path(raw_path).expanduser()
    if candidate.is_absolute():
        absolute = os.path.realpath(candidate)
    else:
        normalized = raw_path.replace("\\", "/").lstrip("./")
        root_name = Path(root_real).name
        if normalized == root_name or normalized.startswith(f"{root_name}/"):
            normalized = normalized[len(root_name):].lstrip("/")
        absolute = os.path.realpath(Path(root_real, normalized))
    try:
        if os.path.commonpath((root_real, absolute)) != root_real:
            raise ValueError(f"mapped source escapes project root: {raw_path}")
    except ValueError as exc:
        raise ValueError(f"mapped source escapes project root: {raw_path}") from exc
    return os.path.relpath(absolute, root_real).replace("\\", "/")


def load_program_mappings(
    path: str,
    *,
    root: str,
    program_id_field: str = "program_id",
    source_path_field: str = "source_path",
    evidence_hash_field: str = "evidence_hash",
) -> tuple[ProgramMapping, ...]:
    try:
        raw_payload = Path(path).read_bytes()
        payload = json.loads(raw_payload.decode("utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read program mapping ledger: {exc}") from exc
    ledger_hash = f"sha256:{hashlib.sha256(raw_payload).hexdigest()}"
    mappings: dict[str, ProgramMapping] = {}
    for position, record in enumerate(_records(payload)):
        program_id = str(record.get(program_id_field) or "").strip()
        source_path = str(record.get(source_path_field) or "").strip()
        evidence_hash = str(record.get(evidence_hash_field) or ledger_hash).strip()
        if not program_id or not source_path:
            raise ValueError(
                "program mapping row requires configured program and source "
                f"fields (row {position})"
            )
        mapping = ProgramMapping(
            program_id=program_id,
            source_path=_canonical_source_path(source_path, root),
            evidence_hash=evidence_hash,
        )
        existing = mappings.get(program_id)
        if existing is not None and existing != mapping:
            raise ValueError(f"conflicting program mapping for {program_id!r}")
        mappings[program_id] = mapping
    return tuple(mappings[key] for key in sorted(mappings))
