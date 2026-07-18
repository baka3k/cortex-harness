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
    source: str = "committed"
    repository_scope: str = "."


@dataclass(frozen=True)
class RepositoryScope:
    source_prefix: str
    root: str
    git_root: str
    git_pathspec: str = "."


def _to_posix(path: str) -> str:
    return path.replace("\\", "/")


def parse_name_status_line(line: str) -> Optional[DiffEntry]:
    """Compatibility parser for non-NUL Git output and existing callers."""
    text = (line or "").rstrip("\r\n")
    if not text:
        return None
    parts = text.split("\t")
    raw_status = parts[0].upper() if parts else ""
    if not raw_status:
        return None
    status = raw_status[0]
    if status in {"A", "M", "D", "T"} and len(parts) >= 2:
        path = _to_posix(parts[1])
        if status == "D":
            return DiffEntry("D", path, None)
        return DiffEntry("M" if status == "T" else status, None, path)
    if status in {"R", "C"} and len(parts) >= 3:
        return DiffEntry("R" if status == "R" else "A", _to_posix(parts[1]), _to_posix(parts[2]))
    return None


def _git_bytes(root: str, *args: str) -> bytes:
    return subprocess.check_output(
        ["git", "-C", root, *args], stderr=subprocess.STDOUT
    )


def _git_text(root: str, *args: str) -> str:
    return _git_bytes(root, *args).decode("utf-8", errors="surrogateescape").strip()


def _repository_context(root: str) -> Tuple[str, str]:
    scan_root = os.path.realpath(os.path.abspath(root))
    git_root = os.path.realpath(_git_text(scan_root, "rev-parse", "--show-toplevel"))
    try:
        relative = os.path.relpath(scan_root, git_root)
    except ValueError as exc:
        raise subprocess.CalledProcessError(128, ["git", "rev-parse"]) from exc
    if relative == os.pardir or relative.startswith(os.pardir + os.sep):
        raise subprocess.CalledProcessError(128, ["git", "rev-parse"])
    return git_root, _to_posix(relative) if relative != "." else "."


def _parse_name_status_z(data: bytes, *, source: str, repository_scope: str) -> List[DiffEntry]:
    tokens = data.decode("utf-8", errors="surrogateescape").split("\0")
    if tokens and tokens[-1] == "":
        tokens.pop()
    entries: List[DiffEntry] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if "\t" in token:
            raw_status, first_path = token.split("\t", 1)
        else:
            raw_status = token
            if index >= len(tokens):
                break
            first_path = tokens[index]
            index += 1
        status = raw_status[:1].upper()
        if status in {"R", "C"}:
            if index >= len(tokens):
                break
            second_path = tokens[index]
            index += 1
            if status == "R":
                entries.append(DiffEntry("R", _to_posix(first_path), _to_posix(second_path), source, repository_scope))
            else:
                entries.append(DiffEntry("A", None, _to_posix(second_path), source, repository_scope))
        elif status == "D":
            entries.append(DiffEntry("D", _to_posix(first_path), None, source, repository_scope))
        elif status in {"A", "M", "T"}:
            entries.append(DiffEntry("M" if status == "T" else status, None, _to_posix(first_path), source, repository_scope))
    return entries


def _map_into_scan_root(entry: DiffEntry, pathspec: str) -> Optional[DiffEntry]:
    prefix = "" if pathspec == "." else pathspec.rstrip("/") + "/"

    def mapped(path: Optional[str]) -> Optional[str]:
        if path is None:
            return None
        normalized = _to_posix(path)
        if prefix and not normalized.startswith(prefix):
            return None
        return normalized[len(prefix):] if prefix else normalized

    old_path = mapped(entry.old_path)
    new_path = mapped(entry.new_path)
    if entry.status == "R":
        if old_path is not None and new_path is not None:
            return DiffEntry("R", old_path, new_path, entry.source, entry.repository_scope)
        if old_path is not None:
            return DiffEntry("D", old_path, None, entry.source, entry.repository_scope)
        if new_path is not None:
            return DiffEntry("A", None, new_path, entry.source, entry.repository_scope)
        return None
    if entry.status == "D" and old_path is not None:
        return DiffEntry("D", old_path, None, entry.source, entry.repository_scope)
    if entry.status in {"A", "M"} and new_path is not None:
        return DiffEntry(entry.status, None, new_path, entry.source, entry.repository_scope)
    return None


def _collect_diff(root: str, args: Sequence[str], source: str) -> List[DiffEntry]:
    git_root, pathspec = _repository_context(root)
    data = _git_bytes(
        git_root,
        "diff",
        "--name-status",
        "-z",
        "--find-renames",
        *args,
        "--",
        pathspec,
    )
    parsed = _parse_name_status_z(data, source=source, repository_scope=".")
    return [mapped for item in parsed if (mapped := _map_into_scan_root(item, pathspec))]


def collect_git_diff_entries(root: str, before_sha: str, after_sha: str) -> List[DiffEntry]:
    return _collect_diff(root, [before_sha, after_sha], "committed")


def collect_worktree_entries(root: str) -> List[DiffEntry]:
    entries = _collect_diff(root, ["--cached", "HEAD"], "staged")
    entries.extend(_collect_diff(root, [], "unstaged"))
    git_root, pathspec = _repository_context(root)
    data = _git_bytes(
        git_root,
        "ls-files",
        "-z",
        "--others",
        "--exclude-standard",
        "--",
        pathspec,
    )
    prefix = "" if pathspec == "." else pathspec.rstrip("/") + "/"
    for raw_path in data.decode("utf-8", errors="surrogateescape").split("\0"):
        if not raw_path:
            continue
        normalized = _to_posix(raw_path)
        if prefix and not normalized.startswith(prefix):
            continue
        entries.append(DiffEntry("A", None, normalized[len(prefix):] if prefix else normalized, "untracked", "."))
    return entries


def _submodule_paths(repo_root: str) -> List[str]:
    gitmodules = os.path.join(repo_root, ".gitmodules")
    if not os.path.isfile(gitmodules):
        return []
    try:
        output = _git_text(repo_root, "config", "--file", gitmodules, "--get-regexp", r"^submodule\..*\.path$")
    except subprocess.CalledProcessError:
        return []
    results: List[str] = []
    for line in output.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2:
            results.append(parts[1])
    return results


def discover_repository_scopes(root: str, recursive: bool = True) -> Tuple[List[RepositoryScope], List[dict]]:
    scan_root = os.path.realpath(os.path.abspath(root))
    try:
        git_root, pathspec = _repository_context(scan_root)
    except subprocess.CalledProcessError:
        return [], [{"code": "not_git_repository", "path": scan_root}]
    scopes = [RepositoryScope(".", scan_root, git_root, pathspec)]
    warnings: List[dict] = []
    if not recursive:
        return scopes, warnings
    queue = [git_root]
    visited = {os.path.normcase(git_root)}
    while queue:
        parent = queue.pop(0)
        for relative in _submodule_paths(parent):
            child = os.path.realpath(os.path.join(parent, relative))
            child_key = os.path.normcase(child)
            try:
                common = os.path.commonpath([scan_root, child])
            except ValueError:
                continue
            if os.path.normcase(common) != os.path.normcase(scan_root):
                continue
            prefix = _to_posix(os.path.relpath(child, scan_root))
            git_marker = os.path.join(child, ".git")
            if not (os.path.isdir(git_marker) or os.path.isfile(git_marker)):
                warnings.append({"code": "submodule_uninitialized", "path": prefix})
                continue
            if child_key in visited:
                warnings.append({"code": "submodule_cycle", "path": prefix})
                continue
            visited.add(child_key)
            scopes.append(RepositoryScope(prefix, child, child, "."))
            queue.append(child)
    scopes.sort(key=lambda item: (item.source_prefix != ".", item.source_prefix))
    return scopes, warnings


def collect_changed_and_deleted(entries: Sequence[DiffEntry]) -> Tuple[Set[str], Set[str]]:
    changed: Set[str] = set()
    deleted: Set[str] = set()
    for entry in entries:
        if entry.status in {"A", "M"} and entry.new_path:
            changed.add(entry.new_path)
        elif entry.status == "D" and entry.old_path:
            deleted.add(entry.old_path)
        elif entry.status == "R":
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
        if parser:
            grouped.setdefault(parser, set()).add(path)
    return grouped


def load_manifest_paths(path: str, root: str) -> Set[str]:
    if not path or not Path(path).exists():
        return set()
    manifest = Path(path)
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
    if parsed_json and isinstance(data, dict):
        data = data.get("files") or []
    elif not parsed_json:
        data = [line.strip() for line in text.splitlines() if line.strip()]
    if not isinstance(data, list):
        return set()
    resolved: Set[str] = set()
    root_abs = os.path.realpath(os.path.abspath(root))
    for raw in data:
        if not isinstance(raw, str) or not raw.strip():
            continue
        candidate = Path(raw.strip())
        candidate = (Path(root_abs) / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
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
