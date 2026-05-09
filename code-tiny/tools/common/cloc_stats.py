from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from shutil import which
from typing import Any, Dict, Optional


def cloc_available() -> bool:
    return which("cloc") is not None


def cloc_enabled() -> bool:
    value = os.getenv("HYPER_ENABLE_CLOC", "0").strip().lower()
    return value in {"1", "true", "yes", "on"}


def collect_cloc_stats(root: str) -> Optional[Dict[str, Any]]:
    if not cloc_enabled():
        return None
    if not cloc_available():
        return None
    try:
        result = subprocess.run(
            ["cloc", "--json", "--quiet", root],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return data


def normalize_cloc_payload(raw: Dict[str, Any]) -> Dict[str, Any]:
    header = raw.get("header", {})
    summary = raw.get("SUM", {})
    languages: Dict[str, Dict[str, int]] = {}
    for key, value in raw.items():
        if key in {"header", "SUM"}:
            continue
        if not isinstance(value, dict):
            continue
        languages[key] = {
            "files": int(value.get("nFiles", 0)),
            "blank": int(value.get("blank", 0)),
            "comment": int(value.get("comment", 0)),
            "code": int(value.get("code", 0)),
        }
    return {
        "cloc_version": header.get("cloc_version"),
        "elapsed_seconds": header.get("elapsed_seconds"),
        "total_files": int(summary.get("nFiles", 0)),
        "total_blank": int(summary.get("blank", 0)),
        "total_comment": int(summary.get("comment", 0)),
        "total_code": int(summary.get("code", 0)),
        "languages": languages,
        "generated_at": datetime.utcnow().isoformat(),
    }


def cloc_stats_payload(
    *,
    project_id: str,
    project_name: str,
    root: str,
    repo: str,
    language: str,
    stats: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "id": project_id,
        "project_id": project_id,
        "project_name": project_name,
        "root": root,
        "repo": repo,
        "language": language,
        "total_files": stats.get("total_files"),
        "total_blank": stats.get("total_blank"),
        "total_comment": stats.get("total_comment"),
        "total_code": stats.get("total_code"),
        "cloc_version": stats.get("cloc_version"),
        "elapsed_seconds": stats.get("elapsed_seconds"),
        "languages_json": json.dumps(stats.get("languages", {}), ensure_ascii=True),
        "generated_at": stats.get("generated_at"),
    }


async def write_cloc_stats_to_neo4j(
    *,
    driver: Any,
    database: Optional[str],
    project_id: str,
    project_name: str,
    root: str,
    repo: str,
    language: str,
    stats: Dict[str, Any],
) -> None:
    payload = cloc_stats_payload(
        project_id=project_id,
        project_name=project_name,
        root=root,
        repo=repo,
        language=language,
        stats=stats,
    )
    query = """
        MERGE (s:CodebaseStats {id: $id})
        SET s.project_id = $project_id,
            s.project_name = $project_name,
            s.root = $root,
            s.repo = $repo,
            s.language = $language,
            s.total_files = $total_files,
            s.total_blank = $total_blank,
            s.total_comment = $total_comment,
            s.total_code = $total_code,
            s.cloc_version = $cloc_version,
            s.elapsed_seconds = $elapsed_seconds,
            s.languages_json = $languages_json,
            s.generated_at = $generated_at
        MERGE (p:Project {project_id: $project_id})
        ON CREATE SET
            p.name = $project_name,
            p.root = $root,
            p.repo = $repo,
            p.language = $language
        MERGE (p)-[:HAS_STATS]->(s)
    """
    await driver.execute_query(query, payload, database=database)
