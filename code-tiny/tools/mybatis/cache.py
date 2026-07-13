from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Optional

from tools.common.analyzer_cache import safe_cache_root
from tools.mybatis.models import MYBATIS_PARSER_VERSION, MyBatisAnalysisResult, MyBatisDependencyIndex


def mybatis_cache_dir(cache_dir: Optional[str], root: str, project_id: str) -> str:
    base = safe_cache_root(cache_dir, "mybatis_facts", project_root=root)
    return os.path.join(base, _safe_segment(project_id))


def default_fact_artifact_path(cache_dir: Optional[str], root: str, project_id: str) -> str:
    return os.path.join(mybatis_cache_dir(cache_dir, root, project_id), "mybatis_facts.json")


def default_dependency_index_path(cache_dir: Optional[str], root: str, project_id: str) -> str:
    return os.path.join(mybatis_cache_dir(cache_dir, root, project_id), "mybatis_dependencies.json")


def write_fact_artifact(path: str, result: MyBatisAnalysisResult) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload: Dict[str, Any] = result.to_dict()
    payload["cache_version"] = MYBATIS_PARSER_VERSION
    _atomic_json_write(path, payload)


def write_dependency_index(path: str, index: MyBatisDependencyIndex) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _atomic_json_write(path, {"cache_version": MYBATIS_PARSER_VERSION, "dependency_index": index.__dict__})


def _atomic_json_write(path: str, payload: Dict[str, Any]) -> None:
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=True, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(tmp_path, path)


def _safe_segment(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", (value or "").strip()).strip("._")
    return cleaned or "project"
