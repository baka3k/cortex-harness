from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from tools.common.analyzer_cache import safe_cache_root
from tools.spring.models import SPRING_PARSER_VERSION, SpringAnalysisResult


def spring_cache_dir(cache_dir: Optional[str], root: str, project_id: str) -> str:
    base = safe_cache_root(cache_dir, "spring_facts", project_root=root)
    return os.path.join(base, _safe_segment(project_id))


def default_fact_artifact_path(cache_dir: Optional[str], root: str, project_id: str) -> str:
    return os.path.join(spring_cache_dir(cache_dir, root, project_id), "spring_facts.json")


def write_fact_artifact(path: str, result: SpringAnalysisResult) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload: Dict[str, Any] = result.to_dict()
    payload["cache_version"] = SPRING_PARSER_VERSION
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=True, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(tmp_path, path)


def _safe_segment(value: str) -> str:
    import re

    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", (value or "").strip()).strip("._")
    return cleaned or "project"
