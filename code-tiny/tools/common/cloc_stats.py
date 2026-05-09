from __future__ import annotations

import json
import subprocess
from datetime import datetime
from shutil import which
from typing import Any, Dict, Optional


def cloc_available() -> bool:
    return which("cloc") is not None


def collect_cloc_stats(root: str) -> Optional[Dict[str, Any]]:
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
