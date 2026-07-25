"""Shared helpers for pure descriptor parsers."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Iterable, Tuple

from ..models import (
    AnalysisDiagnostic,
    ConfidenceLevel,
    DiagnosticCode,
    SourceEvidence,
    normalize_module_path,
)


MAX_DESCRIPTOR_BYTES = 2 * 1024 * 1024
MAX_XML_DEPTH = 64
MAX_XML_NODES = 50_000

_DYNAMIC_MARKERS = re.compile(
    r"(?:(?:\$\{?)?(?:System\.getenv|providers\.|project\.findProperty|exec\b)|"
    r"\b(?:eval|shell)\s*\(|`[^`]+`)",
    re.IGNORECASE,
)


def module_path_for_file(path: str) -> str:
    parent = str(PurePosixPath(path.replace("\\", "/")).parent)
    return normalize_module_path(parent)


def evidence(path: str, line: int = 1, end_line: int | None = None) -> Tuple[SourceEvidence, ...]:
    return (SourceEvidence(path, line, end_line),)


def line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, max(0, offset)) + 1


def dynamic_diagnostics(
    text: str,
    *,
    path: str,
    module_path: str,
) -> Tuple[AnalysisDiagnostic, ...]:
    diagnostics = []
    for match in _DYNAMIC_MARKERS.finditer(text):
        diagnostics.append(
            AnalysisDiagnostic(
                DiagnosticCode.DYNAMIC_EXPRESSION,
                "Dynamic descriptor expression was retained as unresolved evidence.",
                file_path=path,
                module_path=module_path,
                details={"line": line_number(text, match.start())},
            )
        )
    return tuple(diagnostics)


def confidence_for_diagnostics(
    diagnostics: Iterable[AnalysisDiagnostic],
) -> ConfidenceLevel:
    return ConfidenceLevel.MEDIUM if tuple(diagnostics) else ConfidenceLevel.HIGH
