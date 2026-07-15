from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Iterable


def stable_digest(*parts: object, length: int = 24) -> str:
    payload = "\x1f".join(str(part) for part in parts)
    return hashlib.sha256(payload.encode("utf-8", errors="surrogatepass")).hexdigest()[:length]


def normalize_relative_path(path: str) -> str:
    value = (path or "").replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    return value.lstrip("/")


def resolve_inside_root(root: str, path: str, *, require_exists: bool = False) -> tuple[str, str]:
    root_abs = os.path.realpath(os.path.abspath(root))
    candidate = path if os.path.isabs(path) else os.path.join(root_abs, path)
    candidate_abs = os.path.realpath(os.path.abspath(candidate))
    try:
        common = os.path.commonpath((root_abs, candidate_abs))
    except ValueError as exc:
        raise ValueError(f"path is outside project root: {path}") from exc
    if common != root_abs or candidate_abs == root_abs:
        raise ValueError(f"path is outside project root: {path}")
    if require_exists and not os.path.isfile(candidate_abs):
        raise FileNotFoundError(candidate_abs)
    return candidate_abs, normalize_relative_path(os.path.relpath(candidate_abs, root_abs))


def semantic_id(framework: str, project_id: str, module_id: str, kind: str, *coordinates: object) -> str:
    safe_framework = re.sub(r"[^a-z0-9_]+", "_", framework.lower()).strip("_")
    safe_kind = re.sub(r"[^a-z0-9_]+", "_", kind.lower()).strip("_") or "fact"
    return f"{safe_framework}::{safe_kind}::{stable_digest(project_id, module_id, *coordinates)}"


def relationship_id(
    framework: str,
    project_id: str,
    module_id: str,
    relationship: str,
    from_id: str,
    to_id: str,
    *coordinates: object,
) -> str:
    return semantic_id(
        framework,
        project_id,
        module_id,
        "relationship",
        relationship,
        from_id,
        to_id,
        *coordinates,
    )


def module_id(framework: str, module_path: str) -> str:
    normalized = normalize_relative_path(module_path) or "."
    return f"{framework}::module::{stable_digest(normalized)}"


def stable_unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted({str(value) for value in values if str(value)}))


def project_files(root: str, extensions: set[str], ignored_dirs: set[str]) -> tuple[str, ...]:
    root_path = Path(root).resolve()
    values: list[str] = []
    for current, dirnames, filenames in os.walk(root_path, topdown=True, followlinks=False):
        dirnames[:] = sorted(
            name for name in dirnames if name not in ignored_dirs and not name.startswith(".")
        )
        for name in sorted(filenames):
            if Path(name).suffix.lower() not in extensions:
                continue
            absolute = Path(current) / name
            if absolute.is_symlink():
                continue
            values.append(normalize_relative_path(str(absolute.relative_to(root_path))))
    return tuple(sorted(values))
