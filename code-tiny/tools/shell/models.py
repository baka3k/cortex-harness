from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ShellDiagnostic:
    code: str
    message: str
    file_path: str
    line: int
    severity: str = "warning"


@dataclass(frozen=True)
class ShellFunction:
    symbol_id: str
    name: str
    file_path: str
    start_line: int
    end_line: int
    code: str


@dataclass(frozen=True)
class ShellRelation:
    source_id: str
    source_label: str
    target_id: str
    target_label: str
    rel_type: str
    line: int
    raw_target: str
    resolved: bool


@dataclass(frozen=True)
class ShellFile:
    file_path: str
    line_count: int
    encoding: str
    functions: tuple[ShellFunction, ...]
    relations: tuple[ShellRelation, ...]
    diagnostics: tuple[ShellDiagnostic, ...]


@dataclass(frozen=True)
class ShellAnalysisResult:
    project_id: str
    files: tuple[ShellFile, ...]
    changed_paths: tuple[str, ...] = ()
    deleted_paths: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)