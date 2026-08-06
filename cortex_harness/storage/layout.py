"""Versioned instance-layout and manifest helpers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import ResolvedStorage


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def manifest_payload(resolved: ResolvedStorage, *, created_at: str | None = None) -> dict[str, Any]:
    return {
        "schema_version": resolved.schema_version,
        "instance_id": resolved.instance_id,
        "created_at": created_at or _utc_now(),
        "path_provenance": resolved.path_provenance,
        "owners": {
            resolved.code_owner_id: {
                "qdrant_path": str(resolved.qdrant_code_path),
                "falkordb_path": str(resolved.falkordb_code_path),
            },
            resolved.doc_owner_id: {
                "qdrant_path": str(resolved.qdrant_doc_path),
                "falkordb_path": str(resolved.falkordb_doc_path),
            },
        },
    }


def load_manifest(resolved: ResolvedStorage) -> dict[str, Any] | None:
    path = Path(resolved.manifest_path)
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Invalid storage manifest at {path}: expected object")
    return payload


def _manifest_drift(message: str, resolved: ResolvedStorage) -> ValueError:
    return ValueError(
        f"{message} at {resolved.manifest_path}. Refusing to reinterpret existing "
        "embedded storage. Restore the matching CORTEX_STORAGE_INSTANCE/owner/path "
        "configuration, or run 'storage-migrate-layout' to move data into the new layout."
    )


def _validate_manifest_layout(existing: dict[str, Any], resolved: ResolvedStorage) -> None:
    owners = existing.get("owners")
    expected_owners = {resolved.code_owner_id, resolved.doc_owner_id}
    if not isinstance(owners, dict) or set(owners) != expected_owners:
        actual = sorted(owners) if isinstance(owners, dict) else owners
        raise _manifest_drift(
            f"Storage owner identity drift: manifest owners {actual!r} do not match "
            f"configured owners {sorted(expected_owners)!r}",
            resolved,
        )

    expected_paths = {
        resolved.code_owner_id: {
            "qdrant_path": Path(resolved.qdrant_code_path).resolve(),
            "falkordb_path": Path(resolved.falkordb_code_path).resolve(),
        },
        resolved.doc_owner_id: {
            "qdrant_path": Path(resolved.qdrant_doc_path).resolve(),
            "falkordb_path": Path(resolved.falkordb_doc_path).resolve(),
        },
    }
    for owner, expected in expected_paths.items():
        configured = owners.get(owner)
        if not isinstance(configured, dict):
            raise _manifest_drift(
                f"Storage owner identity drift: manifest entry for {owner!r} is invalid",
                resolved,
            )
        for field, expected_path in expected.items():
            raw_path = configured.get(field)
            if not isinstance(raw_path, str) or not raw_path.strip():
                raise _manifest_drift(
                    f"Storage path drift: manifest {owner}.{field} is missing or invalid",
                    resolved,
                )
            manifest_path = Path(raw_path).expanduser()
            canonical_path = manifest_path.resolve()
            if (
                not manifest_path.is_absolute()
                or str(manifest_path) != str(canonical_path)
                or canonical_path != expected_path
            ):
                raise _manifest_drift(
                    f"Storage path drift: manifest {owner}.{field}={raw_path!r} does not "
                    f"match configured canonical path {str(expected_path)!r}",
                    resolved,
                )


def ensure_layout(resolved: ResolvedStorage) -> dict[str, Any]:
    """Create the instance tree and idempotent manifest.

    Existing manifests are validated rather than overwritten, preventing an
    instance directory from being silently reinterpreted under a new schema or
    identity.
    """
    existing = load_manifest(resolved)
    if existing is not None:
        if existing.get("schema_version") != resolved.schema_version:
            raise _manifest_drift(
                f"Storage schema drift: manifest version "
                f"{existing.get('schema_version')!r} does not match "
                f"configured version {resolved.schema_version!r}",
                resolved,
            )
        if existing.get("instance_id") != resolved.instance_id:
            raise _manifest_drift(
                f"Storage instance drift: manifest instance "
                f"{existing.get('instance_id')!r} does not match "
                f"configured instance {resolved.instance_id!r}",
                resolved,
            )
        _validate_manifest_layout(existing, resolved)
        resolved.ensure_directories()
        return existing
    resolved.ensure_directories()
    payload = manifest_payload(resolved)
    path = Path(resolved.manifest_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)
    return payload
