"""Cross-process leases for embedded storage owners."""

from __future__ import annotations

import json
import os
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import portalocker


class StorageLeaseConflictError(RuntimeError):
    """Another process currently owns an embedded store."""


class StorageLease:
    def __init__(self, target: Path, *, instance_id: str, owner_id: str, backend: str) -> None:
        self.target = Path(target).resolve()
        self.instance_id = instance_id
        self.owner_id = owner_id
        self.backend = backend
        # Keep the application lease beside the store.  Acquiring it must not
        # create the target itself because migration needs to copy into a
        # previously absent directory atomically.
        base = self.target.parent
        self.lock_path = base / f".{self.target.name}.cortex-owner.lock"
        self._lock: portalocker.Lock | None = None

    @property
    def metadata(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "owner_id": self.owner_id,
            "backend": self.backend,
            "target": str(self.target),
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "acquired_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        }

    def acquire(self) -> "StorageLease":
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock = portalocker.Lock(
            str(self.lock_path), mode="a+", timeout=0,
            flags=portalocker.LOCK_EX | portalocker.LOCK_NB,
        )
        try:
            handle = lock.acquire()
        except (portalocker.AlreadyLocked, portalocker.LockException) as exc:
            current = "unknown owner"
            try:
                current = self.lock_path.read_text(encoding="utf-8").strip() or current
            except OSError:
                pass
            raise StorageLeaseConflictError(
                f"Embedded {self.backend} store is already owned: {self.target}. "
                f"Current lease: {current}. Stop that owner process or select a different "
                "CORTEX_STORAGE_INSTANCE/CORTEX_STORAGE_OWNER."
            ) from exc
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps(self.metadata, sort_keys=True))
        handle.flush()
        os.fsync(handle.fileno())
        self._lock = lock
        return self

    def release(self) -> None:
        if self._lock is None:
            return
        try:
            handle = self._lock.fh
            if handle is not None:
                handle.seek(0)
                handle.truncate()
                handle.flush()
            self._lock.release()
        finally:
            self._lock = None

    def __enter__(self) -> "StorageLease":
        return self.acquire()

    def __exit__(self, *_: object) -> None:
        self.release()


def assert_owner_stopped(target: Path, *, instance_id: str, owner_id: str, backend: str) -> None:
    lease = StorageLease(target, instance_id=instance_id, owner_id=owner_id, backend=backend)
    lease.acquire()
    lease.release()
