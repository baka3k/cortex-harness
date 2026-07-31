from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

from tools.common.legacy_encoding import read_legacy_text

from .models import Jp1AnalysisResult, Jp1File
from .parser import parse_jp1_text
from .sniff import is_jp1_file


_SKIP_DIRS = {".git", ".venv", "node_modules", "build", "dist", "target"}


def scan_jp1_files(root: str) -> list[str]:
    paths: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in _SKIP_DIRS]
        for name in filenames:
            absolute = os.path.join(dirpath, name)
            if is_jp1_file(absolute):
                paths.append(os.path.relpath(absolute, root).replace("\\", "/"))
    return sorted(paths)


def run_jp1_analysis(root: str, *, project_id: str, changed_paths: Iterable[str] | None = None, deleted_paths: Iterable[str] = ()) -> Jp1AnalysisResult:
    root = os.path.realpath(root)
    selected = scan_jp1_files(root) if changed_paths is None else sorted(
        path.replace("\\", "/") for path in changed_paths if is_jp1_file(str(Path(root, path)))
    )
    files: list[Jp1File] = []
    for relative_path in selected:
        decoded = read_legacy_text(Path(root, relative_path))
        parsed = parse_jp1_text(decoded.text, file_path=relative_path, project_root=root)
        files.append(Jp1File(**{**parsed.__dict__, "encoding": decoded.encoding}))
    return Jp1AnalysisResult(project_id, tuple(files), tuple(selected), tuple(deleted_paths))