from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class Jp1Diagnostic:
    code: str
    message: str
    file_path: str
    line: int


@dataclass
class Jp1Unit:
    unit_id: str
    name: str
    file_path: str
    parent_id: str | None
    start_line: int
    end_line: int
    unit_type: str = ""
    comment: str = ""
    exec_target: str = ""


@dataclass(frozen=True)
class Jp1Relation:
    source_id: str
    source_label: str
    target_id: str
    target_label: str
    rel_type: str
    line: int
    raw_target: str = ""
    resolved: bool = True


@dataclass(frozen=True)
class Jp1File:
    file_path: str
    encoding: str
    units: tuple[Jp1Unit, ...]
    relations: tuple[Jp1Relation, ...]
    diagnostics: tuple[Jp1Diagnostic, ...]


@dataclass(frozen=True)
class Jp1AnalysisResult:
    project_id: str
    files: tuple[Jp1File, ...]
    changed_paths: tuple[str, ...] = ()
    deleted_paths: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)