from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, Optional

CACHE_VERSION = 1


def _safe_cache_segment(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in value)
    return cleaned or "root"


def _project_cache_segment(project_root: Optional[str]) -> Optional[str]:
    if not project_root:
        return None
    normalized = os.path.realpath(os.path.abspath(project_root))
    basename = os.path.basename(normalized.rstrip(os.sep)) or "root"
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]
    return f"{_safe_cache_segment(basename)}_{digest}"


def safe_cache_root(
    cache_dir: Optional[str],
    default_name: str,
    *,
    project_root: Optional[str] = None,
) -> str:
    base_root = cache_dir or os.path.join(os.getcwd(), ".cache")
    root = os.path.join(base_root, default_name)
    project_segment = _project_cache_segment(project_root)
    if project_segment:
        root = os.path.join(root, project_segment)
    os.makedirs(root, exist_ok=True)
    return root


def file_signature(path: str) -> Dict[str, int]:
    stat = os.stat(path)
    return {"mtime_ns": stat.st_mtime_ns, "size": stat.st_size}


def _hash_rel_path(rel_path: str) -> str:
    digest = hashlib.sha1(rel_path.encode("utf-8")).hexdigest()
    return digest[:20]


def parse_cache_path(cache_root: str, rel_path: str) -> str:
    safe_name = _hash_rel_path(rel_path)
    return os.path.join(cache_root, f"{safe_name}.json")


def load_parse_cache(
    cache_root: str,
    rel_path: str,
    signature: Dict[str, int],
) -> Optional[Dict[str, Any]]:
    path = parse_cache_path(cache_root, rel_path)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if data.get("version") != CACHE_VERSION:
        return None
    if data.get("signature") != signature:
        return None
    return data.get("payload")


def write_parse_cache(
    cache_root: str,
    rel_path: str,
    signature: Dict[str, int],
    payload: Dict[str, Any],
) -> None:
    path = parse_cache_path(cache_root, rel_path)
    temp_path = f"{path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(
            {
                "version": CACHE_VERSION,
                "signature": signature,
                "payload": payload,
            },
            handle,
        )
    os.replace(temp_path, path)


def load_state(path: str) -> Dict[str, int]:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def write_state(path: str, state: Dict[str, int]) -> None:
    temp_path = f"{path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(state, handle)
    os.replace(temp_path, path)
