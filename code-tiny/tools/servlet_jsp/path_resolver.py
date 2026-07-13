from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from typing import Optional, Tuple
from urllib.parse import unquote, urlsplit


_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class PathResolution:
    status: str
    reference: str
    relative_path: str = ""
    absolute_path: str = ""
    target_kind: str = "file"
    message: str = ""


def normalize_relative_path(path: str) -> str:
    value = (path or "").replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    return value.strip("/")


def resolve_project_path(
    root: str,
    reference: str,
    *,
    base_file: str = "",
    web_root_relative: bool = False,
    require_exists: bool = False,
) -> PathResolution:
    raw = (reference or "").strip()
    if not raw:
        return PathResolution("invalid", raw, message="empty path")
    if _CONTROL_RE.search(raw):
        return PathResolution("rejected", raw, message="control characters are not allowed")
    decoded = unquote(raw)
    if _CONTROL_RE.search(decoded):
        return PathResolution("rejected", raw, message="decoded control characters are not allowed")
    split = urlsplit(decoded)
    if split.scheme and split.scheme.lower() not in {""}:
        if split.scheme.lower() in {"http", "https", "mailto"}:
            return PathResolution("external", raw, target_kind="external", message="external resources are not fetched")
        return PathResolution("rejected", raw, target_kind="external", message=f"unsupported scheme: {split.scheme}")
    path_part = split.path
    if path_part.startswith(("\\\\", "//")) or _DRIVE_RE.match(path_part):
        return PathResolution("rejected", raw, message="UNC and drive-qualified paths are not allowed")
    root_real = os.path.realpath(os.path.abspath(root))
    if path_part.startswith("/"):
        if not web_root_relative:
            return PathResolution("rejected", raw, message="absolute filesystem paths are not allowed")
        candidate_rel = normalize_relative_path(path_part)
    else:
        base_dir = os.path.dirname(normalize_relative_path(base_file)) if base_file else ""
        candidate_rel = normalize_relative_path(os.path.normpath(os.path.join(base_dir, path_part)))
    if candidate_rel == ".." or candidate_rel.startswith("../"):
        return PathResolution("rejected", raw, message="path traversal escapes the project root")
    candidate_abs = os.path.abspath(os.path.join(root_real, candidate_rel))
    candidate_real = os.path.realpath(candidate_abs)
    try:
        if os.path.commonpath((root_real, candidate_real)) != root_real:
            return PathResolution("rejected", raw, message="resolved path escapes the project root")
    except ValueError:
        return PathResolution("rejected", raw, message="resolved path is on another filesystem root")
    if require_exists and not os.path.exists(candidate_real):
        return PathResolution("missing", raw, candidate_rel, candidate_real, message="target does not exist")
    if os.path.exists(candidate_real) and not os.path.isfile(candidate_real):
        return PathResolution("rejected", raw, candidate_rel, candidate_real, message="target is not a regular file")
    return PathResolution("resolved", raw, candidate_rel, candidate_real)


def read_bounded_file(path: str, max_bytes: int) -> Tuple[bytes, bool]:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise OSError(f"Not a regular file: {path}")
        data = os.read(fd, max_bytes + 1)
        return data[:max_bytes], len(data) > max_bytes
    finally:
        os.close(fd)

