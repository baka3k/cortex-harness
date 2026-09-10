"""Logging/Telemetry extractor.

Detects:
- `ILogger<T>` constructor parameters (or fields/properties)
- `ActivitySource` field/property declarations
- `[LoggerMessage]` source-generated methods

The DI wiring for OpenTelemetry/Activity.AddSource is owned by the ASP.NET
overlay; this extractor only captures the surface declarations.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable

from ..models import SemanticFrameworkItem
from .base import FrameworkExtractor


ILOGGER_PATTERN = re.compile(r"^ILogger(<[^>]+>)?$", re.IGNORECASE)
ACTIVITY_SOURCE_PATTERN = re.compile(r"^ActivitySource$")
LOGGER_MESSAGE_PATTERN = re.compile(r"^LoggerMessage$", re.IGNORECASE)


class LoggingTelemetryExtractor(FrameworkExtractor):
    kind = "LoggingTelemetry"

    def extract(
        self, file_evidence: List[Dict[str, Any]]
    ) -> Iterable[SemanticFrameworkItem]:
        for payload in file_evidence:
            for field in payload.get("fields", []):
                yield from self._iter_field(field, payload)
            for member in payload.get("functions", []):
                yield from self._iter_method(member, payload)

    def _iter_field(
        self, field: Dict[str, Any], payload: Dict[str, Any]
    ) -> Iterable[SemanticFrameworkItem]:
        type_name = (field.get("type_name") or "").strip()
        if ILOGGER_PATTERN.match(type_name):
            yield SemanticFrameworkItem(
                kind=self.kind,
                name=field.get("name", ""),
                qualified_name=field.get("qualified_name", ""),
                file_path=field.get("file_path", "") or payload.get("file_path", ""),
                start_line=field.get("start_line", 0),
                end_line=field.get("end_line", 0),
                code="",
                properties={
                    "instrumentation_type": "logger",
                    "logger_category": self._extract_generic(type_name),
                    "field_kind": field.get("kind", "field"),
                },
                confidence=0.85,
                extraction_method="roslyn",
                source_symbol_id=field.get("symbol_id", ""),
            )
        elif ACTIVITY_SOURCE_PATTERN.match(type_name):
            yield SemanticFrameworkItem(
                kind=self.kind,
                name=field.get("name", ""),
                qualified_name=field.get("qualified_name", ""),
                file_path=field.get("file_path", "") or payload.get("file_path", ""),
                start_line=field.get("start_line", 0),
                end_line=field.get("end_line", 0),
                code="",
                properties={
                    "instrumentation_type": "activity_source",
                    "activity_name": field.get("name", ""),
                },
                confidence=0.95,
                extraction_method="roslyn",
                source_symbol_id=field.get("symbol_id", ""),
            )

    def _iter_method(
        self, method: Dict[str, Any], payload: Dict[str, Any]
    ) -> Iterable[SemanticFrameworkItem]:
        for attribute in method.get("attributes", []) or []:
            value = str(attribute).strip()
            if value.lower().startswith("loggermessage"):
                yield SemanticFrameworkItem(
                    kind=self.kind,
                    name=method.get("name", ""),
                    qualified_name=method.get("qualified_name", ""),
                    file_path=method.get("file_path", "") or payload.get("file_path", ""),
                    start_line=method.get("start_line", 0),
                    end_line=method.get("end_line", 0),
                    code="",
                    properties={
                        "instrumentation_type": "logger_message",
                        "logger_method": method.get("name", ""),
                    },
                    confidence=0.85,
                    extraction_method="roslyn",
                    source_symbol_id=method.get("symbol_id", ""),
                )

    @staticmethod
    def _extract_generic(type_name: str) -> str:
        start = type_name.find("<")
        end = type_name.rfind(">")
        if 0 <= start < end:
            return type_name[start + 1:end].strip()
        return ""
