"""Non-destructive migration from the repository-local storage layout."""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from contextlib import nullcontext
from pathlib import Path

from .config import ResolvedStorage
from .lease import StorageLease
from .layout import ensure_layout


@dataclass(frozen=True)
class MigrationItem:
    source: Path
    target: Path
    action: str
    digest: str | None = None
    inventory: tuple[str, ...] = ()


def _tree_digest(path: Path) -> str:
    digest = hashlib.sha256()
    if path.is_file():
        digest.update(path.read_bytes())
    elif path.is_dir():
        for entry in sorted(item for item in path.rglob("*") if item.is_file()):
            digest.update(entry.relative_to(path).as_posix().encode())
            with entry.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
    return digest.hexdigest()


def _has_content(path: Path) -> bool:
    if not path.exists():
        return False
    if path.is_file():
        return True
    return next(path.iterdir(), None) is not None


def _reopen_inventory(path: Path, backend: str) -> tuple[str, ...]:
    if backend == "qdrant":
        from qdrant_client import QdrantClient
        client = QdrantClient(path=str(path))
        try:
            return tuple(sorted(item.name for item in client.get_collections().collections))
        finally:
            client.close()
    if backend == "falkordb":
        from redislite.falkordb_client import FalkorDB
        client = FalkorDB(str(path))
        try:
            return tuple(sorted(
                item.decode("utf-8") if isinstance(item, bytes) else str(item)
                for item in client.list_graphs()
            ))
        finally:
            client.close()
    raise ValueError(f"Unsupported migration backend: {backend}")


def _marker_path(target: Path) -> Path:
    return target.parent / f".{target.name}.cortex-migration.json"


def _load_marker(target: Path) -> dict[str, object] | None:
    path = _marker_path(target)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_marker(target: Path, *, source: Path, digest: str, inventory: tuple[str, ...]) -> None:
    path = _marker_path(target)
    path.write_text(json.dumps({
        "source": str(source), "source_sha256": digest,
        "target": str(target), "inventory": list(inventory),
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def migrate_legacy_layout(
    resolved: ResolvedStorage,
    legacy_root: Path,
    *,
    dry_run: bool = True,
) -> list[MigrationItem]:
    """Copy legacy stores without deleting or overwriting their sources.

    Existing targets with the same digest are reported as verified no-ops;
    divergent targets fail rather than merging two embedded databases.
    """
    legacy_root = Path(legacy_root).resolve()
    pairs = [
        (legacy_root / "local_qdrant_db" / "code", resolved.qdrant_code_path, resolved.code_owner_id, "qdrant"),
        (legacy_root / "local_qdrant_db" / "doc", resolved.qdrant_doc_path, resolved.doc_owner_id, "qdrant"),
        (legacy_root / "local_falkordb_db" / "cortex.rdb", Path(resolved.falkordb_code_path), resolved.code_owner_id, "falkordb"),
        # The legacy file could contain both code and document graphs. Copy it
        # to each new owner so the subsequent project-scoped verification or
        # re-ingest can retire unrelated graphs without data loss.
        (legacy_root / "local_falkordb_db" / "cortex.rdb", Path(resolved.falkordb_doc_path), resolved.doc_owner_id, "falkordb"),
    ]
    report: list[MigrationItem] = []
    for source, target, owner, backend in pairs:
        if not source.exists():
            continue
        target_lease = (
            nullcontext()
            if dry_run
            else StorageLease(
                target,
                instance_id=resolved.instance_id,
                owner_id=owner,
                backend=backend,
            )
        )
        source_lease = (
            nullcontext()
            if dry_run
            else StorageLease(
                source,
                instance_id=resolved.instance_id,
                owner_id=f"legacy-{owner}",
                backend=f"legacy-{backend}",
            )
        )
        # Hold both ends continuously. Cooperative legacy owners and parallel
        # migration attempts now fail before hashing, and neither source nor
        # target can change through copy, reopen verification, and marker
        # publication.
        with source_lease, target_lease:
            source_digest = _tree_digest(source)
            if _has_content(target):
                marker = _load_marker(target)
                if marker is None or marker.get("source_sha256") != source_digest:
                    target_digest = _tree_digest(target)
                    if target_digest != source_digest:
                        raise FileExistsError(f"Migration target exists with different content: {target}")
                inventory = () if dry_run else _reopen_inventory(target, backend)
                report.append(MigrationItem(source, target, "verified-noop", source_digest, inventory))
                continue
            report.append(MigrationItem(source, target, "would-copy" if dry_run else "copied", source_digest))
            if dry_run:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, target, dirs_exist_ok=False)
            else:
                shutil.copy2(source, target)
            if _tree_digest(target) != source_digest:
                raise IOError(f"Migration verification failed for {target}")
            inventory = _reopen_inventory(target, backend)
            _write_marker(target, source=source, digest=source_digest, inventory=inventory)
            report[-1] = MigrationItem(source, target, "copied", source_digest, inventory)
    if not dry_run:
        ensure_layout(resolved)
    return report
