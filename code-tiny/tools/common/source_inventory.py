from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional, Set, Tuple


INVENTORY_SCHEMA_VERSION = 1


class SourceChangedError(RuntimeError):
    """Raised when source files change while a scan is running."""


@dataclass(frozen=True)
class InventoryEntry:
    size: int
    mtime_ns: int
    sha256: str
    repository_scope: str = "."


@dataclass(frozen=True)
class SourceInventory:
    entries: Dict[str, InventoryEntry]
    snapshot_id: str
    schema_version: int = INVENTORY_SCHEMA_VERSION
    filter_version: int = 1


def _to_posix(path: str) -> str:
    normalized = path.replace("\\", "/")
    return normalized[2:] if normalized.startswith("./") else normalized


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fingerprint(path: Path, previous: Optional[InventoryEntry], force_hash: bool) -> InventoryEntry:
    before = path.stat()
    if previous and not force_hash and previous.size == before.st_size and previous.mtime_ns == before.st_mtime_ns:
        return previous
    sha256 = _hash_file(path)
    after = path.stat()
    if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
        raise SourceChangedError(f"source changed while hashing: {path}")
    return InventoryEntry(size=after.st_size, mtime_ns=after.st_mtime_ns, sha256=sha256)


def _snapshot_id(entries: Dict[str, InventoryEntry], filter_version: int = 1) -> str:
    canonical = {
        "filter_version": filter_version,
        "entries": {path: asdict(entries[path]) for path in sorted(entries)},
    }
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def capture_source_inventory(
    root: str,
    paths: Iterable[str],
    *,
    previous: Optional[SourceInventory] = None,
    force_hash_paths: Optional[Iterable[str]] = None,
) -> SourceInventory:
    root_path = Path(os.path.realpath(os.path.abspath(root)))
    forced = {_to_posix(item) for item in (force_hash_paths or ())}
    entries: Dict[str, InventoryEntry] = {}
    for raw_path in sorted({_to_posix(item) for item in paths if item}):
        full_path = root_path / Path(raw_path)
        if not full_path.is_file():
            continue
        prior = previous.entries.get(raw_path) if previous else None
        entries[raw_path] = _fingerprint(full_path, prior, raw_path in forced)
    return SourceInventory(entries=entries, snapshot_id=_snapshot_id(entries))


def diff_source_inventories(
    before: Optional[SourceInventory], after: SourceInventory
) -> Tuple[Set[str], Set[str]]:
    old_entries = before.entries if before else {}
    changed = {
        path
        for path, entry in after.entries.items()
        if path not in old_entries or old_entries[path].sha256 != entry.sha256
    }
    deleted = set(old_entries) - set(after.entries)
    return changed, deleted


def _payload(inventory: SourceInventory) -> dict:
    return {
        "schema_version": inventory.schema_version,
        "filter_version": inventory.filter_version,
        "snapshot_id": inventory.snapshot_id,
        "entries": {path: asdict(inventory.entries[path]) for path in sorted(inventory.entries)},
    }


def write_inventory_generation(cache_dir: os.PathLike[str] | str, inventory: SourceInventory) -> str:
    directory = Path(cache_dir) / "inventories"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{inventory.snapshot_id}.json"
    serialized = json.dumps(_payload(inventory), ensure_ascii=True, indent=2) + "\n"
    if target.exists():
        if target.read_text(encoding="utf-8") != serialized:
            raise ValueError(f"inventory generation collision: {target}")
        return str(target)
    temp = target.with_suffix(".json.tmp")
    temp.write_text(serialized, encoding="utf-8")
    for attempt in range(5):
        try:
            os.replace(temp, target)
            break
        except FileExistsError:
            temp.unlink(missing_ok=True)
            break
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.02 * (attempt + 1))
    return str(target)


def load_inventory_generation(path: str) -> SourceInventory:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    entries = {
        _to_posix(name): InventoryEntry(**value)
        for name, value in (data.get("entries") or {}).items()
    }
    inventory = SourceInventory(
        entries=entries,
        snapshot_id=str(data.get("snapshot_id") or ""),
        schema_version=int(data.get("schema_version") or INVENTORY_SCHEMA_VERSION),
        filter_version=int(data.get("filter_version") or 1),
    )
    if inventory.snapshot_id != _snapshot_id(entries, inventory.filter_version):
        raise ValueError(f"inventory checksum mismatch: {path}")
    return inventory


def validate_inventory_unchanged(root: str, inventory: SourceInventory, paths: Iterable[str]) -> None:
    selected = {_to_posix(item) for item in paths if item}
    current = capture_source_inventory(
        root,
        selected,
        force_hash_paths=selected,
    )
    expected = {path: inventory.entries.get(path) for path in selected}
    actual = {path: current.entries.get(path) for path in selected}
    if expected != actual:
        changed = sorted(path for path in selected if expected.get(path) != actual.get(path))
        raise SourceChangedError("source changed during scan: " + ", ".join(changed[:10]))
