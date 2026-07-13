from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from typing import Any, Dict, Optional, Tuple

from tools.servlet_jsp.models import SERVLET_JSP_PARSER_VERSION, ResourceBudgets, ServletJspAnalysisResult


SNAPSHOT_SCHEMA_VERSION = 1
_SAFE_SEGMENT_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def servlet_jsp_cache_dir(cache_dir: Optional[str], root: str, project_id: str) -> str:
    if cache_dir:
        base = os.path.realpath(os.path.abspath(os.path.expanduser(cache_dir)))
    else:
        base = os.path.join(
            os.path.realpath(os.path.abspath(os.path.expanduser(os.environ.get("XDG_CACHE_HOME", "~/.cache")))),
            "hyper-graph",
        )
    root_digest = hashlib.sha256(os.path.realpath(root).encode("utf-8")).hexdigest()[:16]
    target = os.path.join(base, "servlet_jsp", f"{_safe_segment(project_id)}-{root_digest}")
    _secure_makedirs(target)
    return target


def preview_artifact_path(cache_dir: Optional[str], root: str, project_id: str) -> str:
    return os.path.join(servlet_jsp_cache_dir(cache_dir, root, project_id), "servlet_jsp_preview.json")


def generation_snapshot_path(
    cache_dir: Optional[str],
    root: str,
    project_id: str,
    module_id: str,
    generation_id: str,
) -> str:
    filename = f"applied-{_safe_segment(module_id)}-{_safe_segment(generation_id)}.json"
    return os.path.join(servlet_jsp_cache_dir(cache_dir, root, project_id), filename)


def write_preview_artifact(path: str, result: ServletJspAnalysisResult) -> str:
    payload = {
        "artifact_role": "preview",
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "parser_version": SERVLET_JSP_PARSER_VERSION,
        "result": result.to_dict(),
    }
    return secure_atomic_json_write(path, payload)


def write_generation_snapshot(
    path: str,
    result: ServletJspAnalysisResult,
    *,
    module_id: str,
    generation_id: str,
    budgets: ResourceBudgets,
) -> str:
    return secure_atomic_json_write(
        path,
        generation_snapshot_payload(
            result,
            module_id=module_id,
            generation_id=generation_id,
            budgets=budgets,
        ),
    )


def generation_snapshot_payload(
    result: ServletJspAnalysisResult,
    *,
    module_id: str,
    generation_id: str,
    budgets: ResourceBudgets,
) -> Dict[str, Any]:
    return {
        "artifact_role": "graph_applied_generation",
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "parser_version": SERVLET_JSP_PARSER_VERSION,
        "project_id": result.project_id,
        "project_root_digest": hashlib.sha256(os.path.realpath(result.root).encode("utf-8")).hexdigest(),
        "module_id": module_id,
        "generation_id": generation_id,
        "budget_fingerprint": budgets.fingerprint(),
        "result": result.to_dict(),
    }


def generation_snapshot_checksum(
    result: ServletJspAnalysisResult,
    *,
    module_id: str,
    generation_id: str,
    budgets: ResourceBudgets,
) -> str:
    return _payload_checksum(
        generation_snapshot_payload(
            result,
            module_id=module_id,
            generation_id=generation_id,
            budgets=budgets,
        )
    )


def load_generation_snapshot(
    path: str,
    *,
    root: str,
    project_id: str,
    module_id: str,
    generation_id: str,
    expected_checksum: str,
    budgets: ResourceBudgets,
    max_bytes: int = 512 * 1024 * 1024,
) -> Tuple[Optional[Dict[str, Any]], str]:
    try:
        info = os.lstat(path)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            return None, "snapshot is not a regular non-symlink file"
        if info.st_size > max_bytes:
            return None, "snapshot exceeds the configured size limit"
        with open(path, "rb") as handle:
            raw = handle.read(max_bytes + 1)
        if len(raw) > max_bytes:
            return None, "snapshot exceeds the configured size limit"
        envelope = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, f"snapshot is unreadable: {exc}"
    checksum = str(envelope.pop("payload_sha256", ""))
    actual = _payload_checksum(envelope)
    if not checksum or checksum != actual or (expected_checksum and expected_checksum != actual):
        return None, "snapshot checksum mismatch"
    expected = {
        "artifact_role": "graph_applied_generation",
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "parser_version": SERVLET_JSP_PARSER_VERSION,
        "project_id": project_id,
        "project_root_digest": hashlib.sha256(os.path.realpath(root).encode("utf-8")).hexdigest(),
        "module_id": module_id,
        "generation_id": generation_id,
        "budget_fingerprint": budgets.fingerprint(),
    }
    for key, value in expected.items():
        if envelope.get(key) != value:
            return None, f"snapshot metadata mismatch: {key}"
    return envelope, "ok"


def secure_atomic_json_write(path: str, payload: Dict[str, Any]) -> str:
    destination = os.path.abspath(path)
    parent = os.path.dirname(destination)
    _secure_makedirs(parent)
    if os.path.lexists(destination) and stat.S_ISLNK(os.lstat(destination).st_mode):
        raise OSError(f"Refusing to replace symlinked output: {destination}")
    envelope = dict(payload)
    checksum = _payload_checksum(envelope)
    envelope["payload_sha256"] = checksum
    fd, temp_path = tempfile.mkstemp(prefix=f".{os.path.basename(destination)}.", suffix=".tmp", dir=parent, text=True)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(envelope, handle, ensure_ascii=True, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, destination)
        dir_fd = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise
    return checksum


def _payload_checksum(payload: Dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _secure_makedirs(path: str) -> None:
    absolute = os.path.abspath(path)
    drive, tail = os.path.splitdrive(absolute)
    current = drive + os.sep if absolute.startswith(os.sep) else drive
    for part in [item for item in tail.split(os.sep) if item]:
        current = os.path.join(current, part) if current else part
        if os.path.lexists(current):
            info = os.lstat(current)
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise OSError(f"Unsafe cache/output path component: {current}")
            continue
        os.mkdir(current, 0o700)
    try:
        os.chmod(absolute, 0o700)
    except OSError:
        pass


def _safe_segment(value: str) -> str:
    return _SAFE_SEGMENT_RE.sub("_", (value or "").strip()).strip("._") or "value"
