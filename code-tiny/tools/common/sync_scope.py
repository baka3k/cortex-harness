from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import portalocker


class LockBusyError(RuntimeError):
    """Raised when another process owns the scan scope lock."""


def canonical_root(root: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(root)))


def scan_scope_id(project_id: str, root: str) -> str:
    identity = f"{project_id}\0{canonical_root(root)}".encode("utf-8")
    return hashlib.sha256(identity).hexdigest()[:24]


def resolve_sync_cache_dir(cache_dir: Optional[str], root: str) -> str:
    if cache_dir:
        return os.path.realpath(os.path.abspath(os.path.expanduser(cache_dir)))
    return os.path.join(os.path.realpath(os.path.abspath(root)), ".cache")


def read_lock_metadata(path: str) -> dict:
    for candidate in (Path(path + ".metadata.json"), Path(path)):
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, json.JSONDecodeError):
            continue
    return {}


class ProjectRunLock:
    """OS-backed project lock; the on-disk JSON is diagnostic metadata only."""

    def __init__(
        self,
        path: str,
        description: str,
        scope_id: str,
        root: str,
        *,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.path = os.path.realpath(os.path.abspath(path))
        self.description = description
        self.scope_id = scope_id
        self.root = canonical_root(root)
        self.timeout_seconds = max(0.0, float(timeout_seconds))
        self._lock: Optional[portalocker.Lock] = None
        self._handle = None

    @property
    def acquired(self) -> bool:
        return self._handle is not None

    def acquire(self) -> None:
        if self.acquired:
            return
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        lock = portalocker.Lock(
            self.path,
            mode="a+",
            timeout=self.timeout_seconds,
            flags=portalocker.LOCK_EX | portalocker.LOCK_NB,
        )
        try:
            handle = lock.acquire()
        except portalocker.exceptions.LockException as exc:
            raise LockBusyError(
                f"scan scope is busy: {self.description} ({self.scope_id})"
            ) from exc
        self._lock = lock
        self._handle = handle
        metadata = {
            "pid": os.getpid(),
            "scope_id": self.scope_id,
            "description": self.description,
            "root": self.root,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            handle.seek(0)
            handle.truncate()
            json.dump(metadata, handle, ensure_ascii=True)
            handle.write("\n")
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
            metadata_path = Path(self.path + ".metadata.json")
            metadata_temp = metadata_path.with_suffix(metadata_path.suffix + ".tmp")
            metadata_temp.write_text(
                json.dumps(metadata, ensure_ascii=True, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(metadata_temp, metadata_path)
        except Exception:
            self.release()
            raise

    def release(self) -> None:
        if self._lock is None:
            return
        try:
            self._lock.release()
        finally:
            self._lock = None
            self._handle = None

    def __enter__(self) -> "ProjectRunLock":
        self.acquire()
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()
