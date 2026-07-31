from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

from tools.common.legacy_encoding import read_legacy_text

from .models import ShellAnalysisResult, ShellFile
from .parser import parse_shell_text


_SKIP_DIRS = {".git", ".venv", "node_modules", "build", "dist", "target"}


def scan_shell_files(root: str) -> list[str]:
    paths: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in _SKIP_DIRS]
        for name in filenames:
            if name.lower().endswith(".sh"):
                paths.append(os.path.relpath(os.path.join(dirpath, name), root).replace("\\", "/"))
    return sorted(paths)


def run_shell_analysis(
    root: str,
    *,
    project_id: str,
    changed_paths: Iterable[str] | None = None,
    deleted_paths: Iterable[str] = (),
) -> ShellAnalysisResult:
    root = os.path.realpath(root)
    selected = scan_shell_files(root) if changed_paths is None else sorted(
        path.replace("\\", "/") for path in changed_paths if path.lower().endswith(".sh")
    )
    files: list[ShellFile] = []
    for relative_path in selected:
        absolute_path = Path(root, relative_path)
        if not absolute_path.is_file():
            continue
        decoded = read_legacy_text(absolute_path)
        parsed = parse_shell_text(decoded.text, file_path=relative_path, project_root=root)
        files.append(ShellFile(**{**parsed.__dict__, "encoding": decoded.encoding}))
    return ShellAnalysisResult(
        project_id=project_id,
        files=tuple(files),
        changed_paths=tuple(selected),
        deleted_paths=tuple(deleted_paths),
    )