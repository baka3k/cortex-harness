from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple


@dataclass(frozen=True)
class DiffEntry:
    status: str
    old_path: Optional[str]
    new_path: Optional[str]


def _to_posix(path: str) -> str:
    return path.replace("\\", "/")


def parse_name_status_line(line: str) -> Optional[DiffEntry]:
    text = (line or "").strip()
    if not text:
        return None
    parts = text.split("\t")
    if not parts:
        return None
    raw_status = parts[0].strip().upper()
    if not raw_status:
        return None
    status = raw_status[0]
    if status in {"A", "M", "D"}:
        if len(parts) < 2:
            return None
        path = _to_posix(parts[1].strip())
        if not path:
            return None
        if status == "D":
            return DiffEntry(status=status, old_path=path, new_path=None)
        return DiffEntry(status=status, old_path=None, new_path=path)
    if status == "R":
        if len(parts) < 3:
            return None
        old_path = _to_posix(parts[1].strip())
        new_path = _to_posix(parts[2].strip())
        if not old_path or not new_path:
            return None
        return DiffEntry(status="R", old_path=old_path, new_path=new_path)
    return None


def collect_git_diff_entries(root: str, before_sha: str, after_sha: str) -> List[DiffEntry]:
    cmd = [
        "git",
        "-C",
        root,
        "diff",
        "--name-status",
        "--find-renames",
        before_sha,
        after_sha,
    ]
    output = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
    entries: List[DiffEntry] = []
    for line in output.splitlines():
        parsed = parse_name_status_line(line)
        if parsed:
            entries.append(parsed)
    return entries


def collect_changed_and_deleted(entries: Sequence[DiffEntry]) -> Tuple[Set[str], Set[str]]:
    changed: Set[str] = set()
    deleted: Set[str] = set()
    for entry in entries:
        if entry.status in {"A", "M"} and entry.new_path:
            changed.add(entry.new_path)
            continue
        if entry.status == "D" and entry.old_path:
            deleted.add(entry.old_path)
            continue
        if entry.status == "R":
            if entry.old_path:
                deleted.add(entry.old_path)
            if entry.new_path:
                changed.add(entry.new_path)
    return changed, deleted


def parser_for_path(path: str, parser_extensions: Dict[str, Sequence[str]]) -> Optional[str]:
    suffix = Path(path).suffix.lower()
    for parser, exts in parser_extensions.items():
        if suffix in exts:
            return parser
    return None


def group_by_parser(paths: Iterable[str], parser_extensions: Dict[str, Sequence[str]]) -> Dict[str, Set[str]]:
    grouped: Dict[str, Set[str]] = {}
    for path in paths:
        parser = parser_for_path(path, parser_extensions)
        if not parser:
            continue
        grouped.setdefault(parser, set()).add(path)
    return grouped


def load_manifest_paths(path: str, root: str) -> Set[str]:
    if not path:
        return set()
    manifest = Path(path)
    if not manifest.exists():
        return set()
    text = manifest.read_text(encoding="utf-8")
    data: object
    stripped = text.lstrip()
    parsed_json = False
    if manifest.suffix.lower() == ".json" or stripped.startswith("{") or stripped.startswith("["):
        try:
            data = json.loads(text or "[]")
            parsed_json = True
        except json.JSONDecodeError:
            data = []
    else:
        data = []
    if parsed_json:
        if isinstance(data, dict):
            data = data.get("files") or []
    else:
        data = [line.strip() for line in text.splitlines() if line.strip()]
    if not isinstance(data, list):
        return set()
    resolved: Set[str] = set()
    root_abs = os.path.realpath(os.path.abspath(root))
    for raw in data:
        if not isinstance(raw, str):
            continue
        raw = raw.strip()
        if not raw:
            continue
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = (Path(root_abs) / candidate).resolve()
        else:
            candidate = candidate.resolve()
        try:
            rel = candidate.relative_to(root_abs)
        except ValueError:
            continue
        resolved.add(_to_posix(str(rel)))
    return resolved


def write_manifest_paths(path: str, files: Iterable[str]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"files": sorted({_to_posix(item) for item in files if item})}
    target.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
