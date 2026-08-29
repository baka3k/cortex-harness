"""Export and import local Cortex Harness database bundles (.cortexdb).

A bundle is a gzipped tar archive containing a ``manifest.json`` and the
physical storage files for a single ``project_id``:

* ``qdrant/code/<collection>`` — full local Qdrant directory for the code
  collection (the Qdrant embedded storage layout).
* ``qdrant/doc/<collection>``  — full local Qdrant directory for the doc
  collection (when present).
* ``falkordb/code.rdb`` — FalkorDBLite dump for the code graph.
* ``falkordb/doc.rdb``  — FalkorDBLite dump for the doc graph (when present).

The archive is self-describing: ``manifest.json`` records the source
``project_id``, schema version, owners, collection/graph names and file
checksums so the importer can reconstruct the destination layout without any
out-of-band metadata.

This module is **local-only**. Remote-backed instances are rejected with a
clear error because the archive format stores raw on-disk files that have no
meaning against a server endpoint.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tarfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .project_config import resolve_start_config
from .storage.config import BackendMode, ResolvedStorage, resolve_storage
from .storage.layout import load_manifest

BUNDLE_SUFFIX = ".cortexdb"
BUNDLE_SCHEMA = "cortex-db-bundle@1"
MANIFEST_NAME = "manifest.json"


class DbTransferError(RuntimeError):
    """Raised when an export or import cannot be completed."""


@dataclass(frozen=True)
class ExportResult:
    archive_path: Path
    project_id: str
    size_bytes: int
    entries: dict


@dataclass(frozen=True)
class ImportResult:
    project_id: str
    restored: dict
    backup_path: Optional[Path]


# ---------------------------------------------------------------------------
# Config resolution
# ---------------------------------------------------------------------------

def _load_project_config(project_root: Path) -> tuple[dict, Path]:
    """Load the active dev.json and the root it was resolved against."""
    root, config_path = resolve_start_config(project_root, project_root)
    if not config_path.is_file():
        raise DbTransferError(
            f"No active dev.json found at {config_path}. Run 'dev init' first."
        )
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise DbTransferError(f"Invalid dev.json at {config_path}: expected object")
    return payload, root


def _resolve_targets(cfg: dict) -> dict:
    """Derive project_id, graph names and collection names from ``cfg``."""
    project = cfg.get("project", {}) or {}
    project_id = str(project.get("code") or project.get("name") or "").strip()
    if not project_id:
        raise DbTransferError(
            "dev.json is missing 'project.code' — cannot derive project_id"
        )
    code_env = dict(cfg.get("code", {}).get("env", {}) or {})
    doc_env = dict(cfg.get("doc", {}).get("env", {}) or {})
    code_graph = str(code_env.get("FALKORDB_GRAPH") or project_id).strip()
    doc_graph = str(
        doc_env.get("FALKORDB_GRAPH") or f"{project_id}_doc"
    ).strip()
    code_collection = str(
        code_env.get("QDRANT_COLLECTION") or project.get("code") or project_id
    ).strip()
    doc_collection = str(
        doc_env.get("QDRANT_COLLECTION_DOC")
        or doc_env.get("QDRANT_COLLECTION")
        or f"{project_id}_doc"
    ).strip()
    return {
        "project_id": project_id,
        "code_graph": code_graph,
        "doc_graph": doc_graph,
        "code_collection": code_collection,
        "doc_collection": doc_collection,
    }


def _resolve_local_storage(
    project_root: Path, cfg: dict, targets: dict
) -> ResolvedStorage:
    """Resolve local storage and reject remote-backed configurations."""
    backend_raw = cfg.get("storage_backend") or "local"
    try:
        mode, _ = BackendMode(backend_raw), None
    except ValueError as exc:
        raise DbTransferError(
            f"Unknown storage_backend {backend_raw!r}"
        ) from exc
    if str(backend_raw).strip().casefold() != "local":
        raise DbTransferError(
            "db-transfer only supports storage_backend='local'; the active "
            f"config is '{backend_raw}'. Switch to local embedded storage or "
            "export directly from the machine that owns the local files."
        )
    resolve_config: dict = dict(cfg.get("code", {}).get("env", {}) or {})
    return resolve_storage(
        project_root,
        config=resolve_config,
        code_graph=targets["code_graph"],
        doc_graph=targets["doc_graph"],
        code_collection=targets["code_collection"],
        doc_collection=targets["doc_collection"],
    )


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_member_name(name: str) -> str:
    """Reject tar member names that would escape the destination tree."""
    if name.startswith(("/", "\\")) or ".." in name.split("/"):
        raise DbTransferError(f"Unsafe archive member name: {name!r}")
    return name


def _copy_tree(src: Path, dst: Path) -> int:
    """Recursively copy ``src`` into ``dst``; return bytes copied."""
    if not src.exists():
        return 0
    dst.mkdir(parents=True, exist_ok=True)
    total = 0
    for item in src.rglob("*"):
        relative = item.relative_to(src)
        target = dst / relative
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with item.open("rb") as reader, target.open("wb") as writer:
            shutil.copyfileobj(reader, writer, length=1 << 20)
            total += item.stat().st_size
    return total


def _copy_file(src: Path, dst: Path) -> int:
    if not src.exists():
        return 0
    dst.parent.mkdir(parents=True, exist_ok=True)
    with src.open("rb") as reader, dst.open("wb") as writer:
        shutil.copyfileobj(reader, writer, length=1 << 20)
    return src.stat().st_size


def _backup_if_needed(target: Path, backups_root: Path, *, overwrite: bool) -> Optional[Path]:
    """Create a timestamped backup when ``target`` exists and overwrite is set.

    Returns the backup path on success, or ``None`` when no backup was needed.
    Raises :class:`DbTransferError` when the target exists but ``overwrite`` is
    ``False`` (interactive safety gate — operator must pass ``OVERWRITE=1``).
    """
    if not target.exists() or not any(target.iterdir()) if target.is_dir() else not target.exists():
        return None
    if not overwrite:
        raise DbTransferError(
            f"Destination {target} already contains data. Re-run with "
            "OVERWRITE=1 to back it up and replace it."
        )
    backups_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backups_root / f"{target.name}.{stamp}.bak"
    shutil.move(str(target), str(backup_path))
    return backup_path


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_project(
    project_root: Path,
    *,
    output: Optional[Path] = None,
    project_id_override: Optional[str] = None,
    role: str = "both",
) -> ExportResult:
    """Create a ``.cortexdb`` bundle for the active project.

    ``role`` selects which storage lanes are bundled: ``"code"``, ``"doc"``,
    or ``"both"`` (default). Missing lanes are skipped with a warning so a
    partially populated instance still exports successfully.
    """
    cfg, resolved_root = _load_project_config(project_root)
    targets = _resolve_targets(cfg)
    if project_id_override:
        targets["project_id"] = project_id_override
    resolved = _resolve_local_storage(resolved_root, cfg, targets)

    if role not in {"code", "doc", "both"}:
        raise DbTransferError(f"role must be code|doc|both; got {role!r}")

    staging = Path(tempfile_dir(resolved_root)) / f".cortexdb-export-{targets['project_id']}-{_timestamp()}"
    staging.mkdir(parents=True, exist_ok=True)

    entries: dict = {"qdrant": {}, "falkordb": {}}
    total_bytes = 0

    lane_plan = []
    if role in {"code", "both"}:
        lane_plan.append(
            ("code", resolved.qdrant_code_path, resolved.falkordb_code_path,
             targets["code_collection"], targets["code_graph"])
        )
    if role in {"doc", "both"}:
        lane_plan.append(
            ("doc", resolved.qdrant_doc_path, resolved.falkordb_doc_path,
             targets["doc_collection"], targets["doc_graph"])
        )

    for lane_name, qdrant_path, falkordb_path, collection, graph in lane_plan:
        if not qdrant_path.exists():
            print(
                f"[warn] Qdrant lane {lane_name} missing at {qdrant_path}; skipping",
                file=sys.stderr,
            )
        else:
            dst_qdrant = staging / "qdrant" / lane_name
            bytes_copied = _copy_tree(qdrant_path, dst_qdrant)
            entries["qdrant"][lane_name] = {
                "source_path": str(qdrant_path),
                "archive_path": f"qdrant/{lane_name}",
                "collection": collection,
                "bytes": bytes_copied,
            }
            total_bytes += bytes_copied

        if not Path(falkordb_path).exists():
            print(
                f"[warn] FalkorDB lane {lane_name} missing at {falkordb_path}; skipping",
                file=sys.stderr,
            )
        else:
            dst_falkor = staging / "falkordb" / f"{lane_name}.rdb"
            bytes_copied = _copy_file(Path(falkordb_path), dst_falkor)
            entries["falkordb"][lane_name] = {
                "source_path": str(falkordb_path),
                "archive_path": f"falkordb/{lane_name}.rdb",
                "graph": graph,
                "bytes": bytes_copied,
                "sha256": _sha256_file(Path(falkordb_path)),
            }
            total_bytes += bytes_copied

    if not entries["qdrant"] and not entries["falkordb"]:
        shutil.rmtree(staging, ignore_errors=True)
        raise DbTransferError(
            "No local storage files found for project "
            f"{targets['project_id']!r}. Run ingestion first."
        )

    manifest = {
        "schema": BUNDLE_SCHEMA,
        "project_id": targets["project_id"],
        "schema_version": resolved.schema_version,
        "instance_id": resolved.instance_id,
        "code_owner_id": resolved.code_owner_id,
        "doc_owner_id": resolved.doc_owner_id,
        "targets": {
            "code_graph": targets["code_graph"],
            "doc_graph": targets["doc_graph"],
            "code_collection": targets["code_collection"],
            "doc_collection": targets["doc_collection"],
        },
        "entries": entries,
        "created_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ).replace("+00:00", "Z"),
        "source_manifest": _maybe_load_source_manifest(resolved),
    }
    manifest_path = staging / MANIFEST_NAME
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    archive_path = _resolve_output_path(output, targets["project_id"])
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(staging, arcname=".")

    shutil.rmtree(staging, ignore_errors=True)

    size_bytes = archive_path.stat().st_size
    print(
        f"[ok] Exported project_id={targets['project_id']!r} -> {archive_path} "
        f"({size_bytes:,} bytes)"
    )
    return ExportResult(
        archive_path=archive_path,
        project_id=targets["project_id"],
        size_bytes=size_bytes,
        entries=entries,
    )


def _maybe_load_source_manifest(resolved: ResolvedStorage) -> Optional[dict]:
    manifest = load_manifest(resolved)
    return manifest


def _resolve_output_path(output: Optional[Path], project_id: str) -> Path:
    if output:
        path = Path(output).expanduser()
        if not path.suffix:
            path = path.with_suffix(BUNDLE_SUFFIX)
        return path.resolve() if path.is_absolute() else Path.cwd() / path
    destination = Path.cwd() / "outputs" / "db"
    return destination / f"{project_id}-{_timestamp()}{BUNDLE_SUFFIX}"


def tempfile_dir(project_root: Path) -> Path:
    """Prefer a project-local temp directory so staging stays on the same FS."""
    candidate = project_root / ".cache" / "db-transfer"
    candidate.mkdir(parents=True, exist_ok=True)
    return candidate


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

def import_project(
    project_root: Path,
    archive: Path,
    *,
    overwrite: bool = False,
    role: str = "both",
) -> ImportResult:
    """Restore a ``.cortexdb`` bundle into the local data home.

    The archive is extracted into a project-local staging area, validated
    against :data:`BUNDLE_SCHEMA`, and then copied into the instance tree
    selected by the destination environment (``CORTEX_DATA_HOME``,
    ``CORTEX_STORAGE_INSTANCE``). Remote-backed configurations are rejected.
    """
    archive_path = Path(archive).expanduser()
    if not archive_path.is_file():
        raise DbTransferError(f"Archive not found: {archive_path}")

    if role not in {"code", "doc", "both"}:
        raise DbTransferError(f"role must be code|doc|both; got {role!r}")

    staging = Path(tempfile_dir(project_root)) / f".cortexdb-import-{_timestamp()}"
    staging.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(archive_path, "r:*") as tar:
            for member in tar.getmembers():
                _safe_member_name(member.name)
            extract_kwargs: dict = {}
            if hasattr(tarfile, "data_filter"):
                extract_kwargs["filter"] = "data"
            tar.extractall(staging, **extract_kwargs)

        manifest_path = staging / MANIFEST_NAME
        if not manifest_path.is_file():
            raise DbTransferError(
                f"Archive {archive_path} is missing {MANIFEST_NAME}"
            )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise DbTransferError("Bundle manifest must be a JSON object")
        if manifest.get("schema") != BUNDLE_SCHEMA:
            raise DbTransferError(
                "Unsupported bundle schema "
                f"{manifest.get('schema')!r}; expected {BUNDLE_SCHEMA!r}"
            )

        cfg, resolved_root = _load_project_config(project_root)
        targets = _resolve_targets(cfg)
        targets["project_id"] = str(manifest.get("project_id") or targets["project_id"])
        resolved = _resolve_local_storage(resolved_root, cfg, targets)

        restored: dict = {"qdrant": {}, "falkordb": {}, "backup": None}

        lane_plan = []
        if role in {"code", "both"}:
            lane_plan.append(
                ("code", resolved.qdrant_code_path, resolved.falkordb_code_path)
            )
        if role in {"doc", "both"}:
            lane_plan.append(
                ("doc", resolved.qdrant_doc_path, resolved.falkordb_doc_path)
            )

        for lane_name, qdrant_dest, falkordb_dest in lane_plan:
            src_qdrant = staging / "qdrant" / lane_name
            if src_qdrant.exists():
                backup = _backup_if_needed(
                    qdrant_dest, resolved.backups_path, overwrite=overwrite
                )
                if backup is not None:
                    restored["backup"] = restored["backup"] or str(backup.parent)
                if qdrant_dest.exists():
                    shutil.rmtree(qdrant_dest)
                _copy_tree(src_qdrant, qdrant_dest)
                restored["qdrant"][lane_name] = str(qdrant_dest)

            src_falkor = staging / "falkordb" / f"{lane_name}.rdb"
            if src_falkor.exists():
                backup = _backup_if_needed(
                    Path(falkordb_dest), resolved.backups_path, overwrite=overwrite
                )
                if backup is not None:
                    restored["backup"] = restored["backup"] or str(backup.parent)
                if Path(falkordb_dest).exists():
                    Path(falkordb_dest).unlink()
                _copy_file(src_falkor, Path(falkordb_dest))
                restored["falkordb"][lane_name] = str(falkordb_dest)

        if not restored["qdrant"] and not restored["falkordb"]:
            raise DbTransferError(
                "Archive contains no storage entries for role "
                f"{role!r} (project_id={manifest.get('project_id')!r})"
            )

        backup_path = Path(restored["backup"]) if restored["backup"] else None
        print(
            f"[ok] Imported project_id={manifest.get('project_id')!r} from {archive_path} "
            f"(lanes={list(restored['qdrant'].keys()) + list(restored['falkordb'].keys())})"
        )
        if backup_path is not None:
            print(f"[info] Previous data backed up to {backup_path}")
        return ImportResult(
            project_id=str(manifest.get("project_id")),
            restored=restored,
            backup_path=backup_path,
        )
    finally:
        shutil.rmtree(staging, ignore_errors=True)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _parse_bool(value: str) -> bool:
    return value.strip().casefold() in {"1", "true", "yes", "y", "on"}


def main(argv: Optional[list[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(
            "usage: db-transfer export --project-root <dir> [--output <path>] "
            "[--role code|doc|both]\n"
            "       db-transfer import  --project-root <dir> --archive <path> "
            "[--overwrite 1] [--role code|doc|both]",
            file=sys.stderr,
        )
        return 2

    command = args[0]
    rest = args[1:]
    parsed: dict = {}
    i = 0
    while i < len(rest):
        token = rest[i]
        if token.startswith("--"):
            key = token[2:]
            if i + 1 < len(rest) and not rest[i + 1].startswith("--"):
                parsed[key] = rest[i + 1]
                i += 2
                continue
            parsed[key] = "1"
            i += 1
            continue
        raise DbTransferError(f"Unexpected argument: {token!r}")

    try:
        project_root = Path(parsed.get("project-root") or Path.cwd()).expanduser()
        role = str(parsed.get("role") or "both").strip()
        if command == "export":
            export_project(
                project_root,
                output=Path(parsed["output"]).expanduser() if "output" in parsed else None,
                project_id_override=parsed.get("project-id"),
                role=role,
            )
        elif command == "import":
            archive = parsed.get("archive")
            if not archive:
                raise DbTransferError("--archive <path> is required for import")
            import_project(
                project_root,
                Path(archive).expanduser(),
                overwrite=_parse_bool(str(parsed.get("overwrite") or "0")),
                role=role,
            )
        else:
            raise DbTransferError(f"Unknown command: {command!r}")
    except DbTransferError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
