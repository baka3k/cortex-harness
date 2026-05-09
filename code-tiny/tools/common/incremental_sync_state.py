from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from tools.common.analyzer_cache import safe_cache_root


@dataclass
class IncrementalSyncState:
    project_id: str
    root: str
    last_good_sha: str = ""
    dirty: bool = False
    last_error: str = ""
    last_run_before: str = ""
    last_run_after: str = ""
    updated_at: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any], project_id: str, root: str) -> "IncrementalSyncState":
        return cls(
            project_id=project_id,
            root=root,
            last_good_sha=str(data.get("last_good_sha") or ""),
            dirty=bool(data.get("dirty", False)),
            last_error=str(data.get("last_error") or ""),
            last_run_before=str(data.get("last_run_before") or ""),
            last_run_after=str(data.get("last_run_after") or ""),
            updated_at=str(data.get("updated_at") or ""),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_id": self.project_id,
            "root": self.root,
            "last_good_sha": self.last_good_sha,
            "dirty": self.dirty,
            "last_error": self.last_error,
            "last_run_before": self.last_run_before,
            "last_run_after": self.last_run_after,
            "updated_at": self.updated_at,
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_project_id(project_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", project_id).strip("._")
    return cleaned or "project"


def state_file_path(cache_dir: Optional[str], project_id: str, root: str) -> str:
    cache_root = safe_cache_root(cache_dir, "incremental_sync", project_root=root)
    return os.path.join(cache_root, f"{_safe_project_id(project_id)}.json")


def load_sync_state(path: str, project_id: str, root: str) -> IncrementalSyncState:
    state_path = Path(path)
    if not state_path.exists():
        return IncrementalSyncState(project_id=project_id, root=os.path.abspath(root))
    data = json.loads(state_path.read_text(encoding="utf-8") or "{}")
    if not isinstance(data, dict):
        data = {}
    return IncrementalSyncState.from_dict(data, project_id=project_id, root=os.path.abspath(root))


def save_sync_state(path: str, state: IncrementalSyncState) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(json.dumps(state.to_dict(), ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    temp.replace(target)


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
) -> IncrementalSyncState:
    state.dirty = False
    state.last_error = ""
    state.last_good_sha = last_good_sha
    state.last_run_before = before_sha
    state.last_run_after = after_sha
    state.updated_at = _now_iso()
    save_sync_state(path, state)
    return state

