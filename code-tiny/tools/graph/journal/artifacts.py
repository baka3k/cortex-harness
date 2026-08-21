"""Immutable, content-addressed payload artifacts for journal batches."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import secrets
import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterable

from .identity import canonical_json
from .models import ArtifactRef, JournalError, TerminalErrorCode


_UNSAFE_FILESYSTEMS = {
    "9p",
    "afs",
    "cifs",
    "fuse.sshfs",
    "nfs",
    "nfs4",
    "smbfs",
}
_SAFE_FILESYSTEMS = {
    "apfs",
    "btrfs",
    "ext2/ext3",
    "ext2",
    "ext3",
    "ext4",
    "f2fs",
    "hfs",
    "hfsplus",
    "ntfs-local",
    "overlay",
    "overlayfs",
    "ramfs",
    "tmpfs",
    "ufs",
    "xfs",
    "zfs",
}


def _filesystem_type(path: Path) -> str | None:
    """Resolve the filesystem type so WAL placement can fail closed."""
    if os.name == "nt":
        try:
            import ctypes

            drive_type = ctypes.windll.kernel32.GetDriveTypeW(
                str(path.resolve().anchor)
            )
            if drive_type == 4:
                return "network"
            if drive_type in {3, 6}:
                return "ntfs-local"
            return None
        except (AttributeError, OSError):
            return None
    mounts = Path("/proc/mounts")
    if mounts.exists():
        resolved = str(path.resolve())
        best: tuple[int, str] | None = None
        try:
            lines = mounts.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            lines = []
        for line in lines:
            parts = line.split()
            if len(parts) < 3:
                continue
            mount_point = parts[1].replace("\\040", " ")
            if resolved == mount_point or resolved.startswith(
                mount_point.rstrip("/") + "/"
            ):
                candidate = (len(mount_point), parts[2].casefold())
                if best is None or candidate[0] > best[0]:
                    best = candidate
        if best:
            return best[1]
    try:
        mounted = subprocess.run(
            ("mount",), check=False, capture_output=True, text=True, timeout=2
        )
    except (OSError, subprocess.SubprocessError):
        mounted = None
    if mounted is not None and mounted.returncode == 0:
        resolved = str(path.resolve())
        best_mount: tuple[int, str] | None = None
        for line in mounted.stdout.splitlines():
            _device, separator, remainder = line.partition(" on ")
            if not separator:
                continue
            mount_point, option_separator, options = remainder.rpartition(" (")
            if not option_separator or not options.endswith(")"):
                continue
            if resolved == mount_point or resolved.startswith(
                mount_point.rstrip("/") + "/"
            ):
                fs_type = options[:-1].split(",", 1)[0].strip().casefold()
                candidate = (len(mount_point), fs_type)
                if best_mount is None or candidate[0] > best_mount[0]:
                    best_mount = candidate
        if best_mount:
            return best_mount[1]
    for command in (("stat", "-f", "-c", "%T", str(path)),):
        try:
            result = subprocess.run(
                command, check=False, capture_output=True, text=True, timeout=2
            )
        except (OSError, subprocess.SubprocessError):
            continue
        value = result.stdout.strip().casefold()
        if result.returncode == 0 and value and value != "%t":
            return value
    return None


def ensure_safe_local_directory(path: Path) -> Path:
    requested = Path(path).expanduser()
    if not requested.is_absolute():
        raise JournalError(
            TerminalErrorCode.UNSAFE_PLACEMENT,
            "journal paths must be absolute",
            details={"path": str(requested)},
        )
    current = Path(requested.anchor)
    for component in requested.parts[1:]:
        current /= component
        if current.exists() and current.is_symlink():
            raise JournalError(
                TerminalErrorCode.UNSAFE_PLACEMENT,
                "journal paths must not traverse symlinks",
                details={"path": str(current)},
            )
    try:
        resolved = requested.resolve()
        resolved.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(resolved, 0o700)
        info = resolved.stat()
    except JournalError:
        raise
    except PermissionError as exc:
        raise JournalError(
            TerminalErrorCode.PERMISSION_DENIED, "journal directory is not writable"
        ) from exc
    except OSError as exc:
        raise _translate_os_error(exc, "cannot create journal directory") from exc
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise JournalError(
            TerminalErrorCode.UNSAFE_PLACEMENT,
            "journal directory is not owned by the current user",
            details={"path": str(resolved)},
        )
    fs_type = _filesystem_type(resolved)
    if fs_type in _UNSAFE_FILESYSTEMS or fs_type not in _SAFE_FILESYSTEMS:
        raise JournalError(
            TerminalErrorCode.UNSAFE_PLACEMENT,
            f"filesystem type {fs_type or 'unknown'} does not support the local WAL contract",
            details={"path": str(resolved), "filesystem_type": fs_type},
        )
    return resolved


def _translate_os_error(exc: OSError, message: str) -> JournalError:
    if exc.errno in {errno.ENOSPC, getattr(errno, "EDQUOT", -1)}:
        return JournalError(TerminalErrorCode.DISK_FULL, message)
    if isinstance(exc, PermissionError) or exc.errno in {
        errno.EACCES,
        errno.EPERM,
        errno.EROFS,
    }:
        return JournalError(TerminalErrorCode.PERMISSION_DENIED, message)
    return JournalError(TerminalErrorCode.INVALID_CONTRACT, f"{message}: {exc}")


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        # Windows cannot open a directory with os.open(O_RDONLY); journal
        # durability degrades to the per-file fsync already performed by the
        # artifact writers.
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class ArtifactStore:
    def __init__(
        self,
        root: Path,
        *,
        max_artifact_bytes: int = 256 * 1024 * 1024,
        min_free_bytes: int = 512 * 1024 * 1024,
    ) -> None:
        self.root = ensure_safe_local_directory(Path(root))
        self.max_artifact_bytes = max_artifact_bytes
        self.min_free_bytes = min_free_bytes

    def _run_directory(self, run_id: str) -> Path:
        if not run_id or Path(run_id).name != run_id:
            raise JournalError(
                TerminalErrorCode.UNSAFE_PLACEMENT,
                "artifact run identity must be one safe path component",
            )
        run_dir = self.root / run_id
        if run_id in {".", ".."} or run_dir.resolve().parent != self.root:
            raise JournalError(
                TerminalErrorCode.UNSAFE_PLACEMENT,
                "artifact run directory escapes the artifact root",
            )
        try:
            if run_dir.is_symlink():
                raise JournalError(
                    TerminalErrorCode.UNSAFE_PLACEMENT,
                    "artifact run directory must not be a symlink",
                )
            run_dir.mkdir(mode=0o700, exist_ok=True)
            os.chmod(run_dir, 0o700)
            info = run_dir.stat()
        except JournalError:
            raise
        except OSError as exc:
            raise _translate_os_error(
                exc, "cannot create artifact run directory"
            ) from exc
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise JournalError(
                TerminalErrorCode.UNSAFE_PLACEMENT,
                "artifact run directory is not owned by the current user",
            )
        return run_dir

    def _admit(self, byte_count: int) -> None:
        if byte_count > self.max_artifact_bytes:
            raise JournalError(
                TerminalErrorCode.ADMISSION_REJECTED,
                "artifact exceeds the configured item byte limit",
                details={"bytes": byte_count, "limit": self.max_artifact_bytes},
            )
        free = shutil.disk_usage(self.root).free
        if free - byte_count < self.min_free_bytes:
            raise JournalError(
                TerminalErrorCode.DISK_FULL,
                "artifact admission would violate disk headroom",
                details={"free_bytes": free, "required_headroom": self.min_free_bytes},
            )

    def write_bytes(
        self, run_id: str, payload: bytes, *, row_count: int
    ) -> ArtifactRef:
        if row_count < 0:
            raise ValueError("row_count must be non-negative")
        self._admit(len(payload))
        digest = hashlib.sha256(payload).hexdigest()
        run_dir = self._run_directory(run_id)
        target = run_dir / f"{digest}.jsonl"
        relative = target.relative_to(self.root).as_posix()
        if target.exists():
            ref = ArtifactRef(digest, relative, len(payload), row_count)
            self.verify(ref)
            return ref
        temp = run_dir / f".{digest}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
        descriptor: int | None = None
        try:
            descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb", closefd=True) as handle:
                descriptor = None
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, target)
            os.chmod(target, 0o400)
            _fsync_directory(run_dir)
        except OSError as exc:
            raise _translate_os_error(exc, "cannot persist journal artifact") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
        return ArtifactRef(digest, relative, len(payload), row_count)

    def write_jsonl(self, run_id: str, rows: Iterable[Any]) -> ArtifactRef:
        run_dir = self._run_directory(run_id)
        temp = run_dir / f".payload.{os.getpid()}.{secrets.token_hex(8)}.tmp"
        free = shutil.disk_usage(self.root).free
        if free < self.min_free_bytes:
            raise JournalError(
                TerminalErrorCode.DISK_FULL,
                "artifact admission would violate disk headroom",
            )
        available = max(0, free - self.min_free_bytes)
        byte_limit = min(self.max_artifact_bytes, available)
        hasher = hashlib.sha256()
        byte_count = 0
        row_count = 0
        descriptor: int | None = None
        try:
            descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb", closefd=True) as handle:
                descriptor = None
                for row in rows:
                    encoded = canonical_json(row) + b"\n"
                    byte_count += len(encoded)
                    if byte_count > byte_limit:
                        code = (
                            TerminalErrorCode.ADMISSION_REJECTED
                            if byte_count > self.max_artifact_bytes
                            else TerminalErrorCode.DISK_FULL
                        )
                        raise JournalError(
                            code, "artifact exceeds its bounded storage admission"
                        )
                    handle.write(encoded)
                    hasher.update(encoded)
                    row_count += 1
                handle.flush()
                os.fsync(handle.fileno())
            digest = hasher.hexdigest()
            target = run_dir / f"{digest}.jsonl"
            relative = target.relative_to(self.root).as_posix()
            ref = ArtifactRef(digest, relative, byte_count, row_count)
            if target.exists():
                self.verify(ref)
            else:
                os.replace(temp, target)
                os.chmod(target, 0o400)
                _fsync_directory(run_dir)
            return ref
        except JournalError:
            raise
        except OSError as exc:
            raise _translate_os_error(exc, "cannot persist journal artifact") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass

    def path_for(self, ref: ArtifactRef) -> Path:
        path = (self.root / ref.relative_path).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise JournalError(
                TerminalErrorCode.UNSAFE_PLACEMENT,
                "artifact reference escapes the artifact root",
            ) from exc
        return path

    def verify(self, ref: ArtifactRef) -> Path:
        path = self.path_for(ref)
        try:
            hasher = hashlib.sha256()
            byte_count = 0
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    byte_count += len(chunk)
                    hasher.update(chunk)
        except OSError as exc:
            raise _translate_os_error(exc, "cannot read journal artifact") from exc
        if byte_count != ref.byte_count or hasher.hexdigest() != ref.sha256:
            raise JournalError(
                TerminalErrorCode.ARTIFACT_HASH_MISMATCH,
                "journal artifact does not match its immutable reference",
                details={"sha256": ref.sha256, "path": ref.relative_path},
            )
        return path

    def read_jsonl(self, ref: ArtifactRef) -> list[Any]:
        path = self.verify(ref)
        try:
            with path.open("r", encoding="utf-8") as handle:
                rows = [json.loads(line) for line in handle if line.strip()]
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise JournalError(
                TerminalErrorCode.ARTIFACT_HASH_MISMATCH,
                "journal artifact is not valid canonical JSONL",
            ) from exc
        if len(rows) != ref.row_count:
            raise JournalError(
                TerminalErrorCode.ARTIFACT_HASH_MISMATCH,
                "journal artifact row count does not match its reference",
            )
        return rows

    def remove(self, ref: ArtifactRef) -> None:
        path = self.path_for(ref)
        try:
            path.unlink(missing_ok=True)
            parent = path.parent
            if parent != self.root:
                try:
                    parent.rmdir()
                except OSError:
                    pass
            _fsync_directory(self.root)
        except OSError as exc:
            raise _translate_os_error(exc, "cannot remove journal artifact") from exc
