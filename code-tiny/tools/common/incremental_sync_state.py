from __future__ import annotations

import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.common.sync_scope import resolve_sync_cache_dir, scan_scope_id


STATE_SCHEMA_VERSION = 2


@dataclass
class IncrementalSyncState:
    project_id: str
    root: str
    schema_version: int = STATE_SCHEMA_VERSION
    last_good_sha: str = ""
    dirty: bool = False
    last_error: str = ""
    last_run_before: str = ""
    last_run_after: str = ""
    updated_at: str = ""
    snapshot_id: str = ""
    inventory_path: str = ""
    repositories: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    working_tree_paths: List[str] = field(default_factory=list)
    filter_version: int = 1
    migration_required: bool = False
    migrated_from: Optional[int] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any], project_id: str, root: str) -> "IncrementalSyncState":
        raw_schema = data.get("schema_version")
        migrated_from = None
        migration_required = False
        if raw_schema is None:
            migrated_from = 1
            migration_required = bool(data)
        elif int(raw_schema) != STATE_SCHEMA_VERSION:
            migrated_from = int(raw_schema)
            migration_required = True
        return cls(
            project_id=project_id,
            root=os.path.realpath(os.path.abspath(root)),
            last_good_sha=str(data.get("last_good_sha") or ""),
            dirty=bool(data.get("dirty", False)),
            last_error=str(data.get("last_error") or ""),
            last_run_before=str(data.get("last_run_before") or ""),
            last_run_after=str(data.get("last_run_after") or ""),
            updated_at=str(data.get("updated_at") or ""),
            snapshot_id=str(data.get("snapshot_id") or ""),
            inventory_path=str(data.get("inventory_path") or ""),
            repositories=dict(data.get("repositories") or {}),
            working_tree_paths=list(data.get("working_tree_paths") or []),
            filter_version=int(data.get("filter_version") or 1),
            migration_required=migration_required or bool(data.get("migration_required", False)),
            migrated_from=migrated_from if migrated_from is not None else data.get("migrated_from"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": STATE_SCHEMA_VERSION,
            "project_id": self.project_id,
            "root": self.root,
            "last_good_sha": self.last_good_sha,
            "dirty": self.dirty,
            "last_error": self.last_error,
            "last_run_before": self.last_run_before,
            "last_run_after": self.last_run_after,
            "updated_at": self.updated_at,
            "snapshot_id": self.snapshot_id,
            "inventory_path": self.inventory_path,
            "repositories": self.repositories,
            "working_tree_paths": sorted(set(self.working_tree_paths)),
            "filter_version": self.filter_version,
            "migration_required": self.migration_required,
            "migrated_from": self.migrated_from,
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_project_id(project_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", project_id).strip("._")
    return cleaned or "project"


def state_file_path(cache_dir: Optional[str], project_id: str, root: str) -> str:
    cache_root = os.path.join(resolve_sync_cache_dir(cache_dir, root), "incremental_sync")
    scope = scan_scope_id(project_id, root)
    return os.path.join(cache_root, f"{_safe_project_id(project_id)}_{scope}.json")


def legacy_state_file_path(cache_dir: Optional[str], project_id: str, root: str) -> str:
    cache_root = os.path.join(resolve_sync_cache_dir(cache_dir, root), "incremental_sync")
    return os.path.join(cache_root, f"{_safe_project_id(project_id)}.json")


def load_sync_state(path: str, project_id: str, root: str) -> IncrementalSyncState:
    state_path = Path(path)
    if not state_path.exists():
        return IncrementalSyncState(project_id=project_id, root=os.path.realpath(os.path.abspath(root)))
    data = json.loads(state_path.read_text(encoding="utf-8") or "{}")
    if not isinstance(data, dict):
        data = {}
    return IncrementalSyncState.from_dict(data, project_id=project_id, root=root)


def save_sync_state(path: str, state: IncrementalSyncState) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(json.dumps(state.to_dict(), ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    for attempt in range(5):
        try:
            os.replace(temp, target)
            break
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.02 * (attempt + 1))


def backup_legacy_state(path: str, state: IncrementalSyncState) -> str:
    if not state.migration_required:
        return ""
    source = Path(path)
    suffix = state.migrated_from or 1
    backup = source.with_suffix(source.suffix + f".v{suffix}.bak")
    if source.exists() and not backup.exists():
        backup.parent.mkdir(parents=True, exist_ok=True)
        temp = backup.with_suffix(backup.suffix + ".tmp")
        shutil.copyfile(source, temp)
        os.replace(temp, backup)
    return str(backup)


def mark_dirty(
    path: str,
    state: IncrementalSyncState,
    *,
    error: str,
    before_sha: str,
    after_sha: str,
) -> IncrementalSyncState:
    state.dirty = True
    state.last_error = error
    state.last_run_before = before_sha
    state.last_run_after = after_sha
    state.updated_at = _now_iso()
    save_sync_state(path, state)
    return state


def mark_clean(
    path: str,
    state: IncrementalSyncState,
    *,
    last_good_sha: str,
    before_sha: str,
    after_sha: str,
    snapshot_id: Optional[str] = None,
    inventory_path: Optional[str] = None,
    repositories: Optional[Dict[str, Dict[str, Any]]] = None,
    working_tree_paths: Optional[List[str]] = None,
    filter_version: int = 1,
) -> IncrementalSyncState:
    state.dirty = False
    state.last_error = ""
    state.last_good_sha = last_good_sha
    state.last_run_before = before_sha
    state.last_run_after = after_sha
    if snapshot_id is not None:
        state.snapshot_id = snapshot_id
    if inventory_path is not None:
        state.inventory_path = inventory_path
    if repositories is not None:
        state.repositories = repositories
    if working_tree_paths is not None:
        state.working_tree_paths = working_tree_paths
    state.filter_version = filter_version
    state.migration_required = False
    state.updated_at = _now_iso()
    save_sync_state(path, state)
    return state
