"""Background service extractor.

Detects classes that:
- inherit `BackgroundService`, or
- implement `IHostedService`.

Emits `BackgroundService` nodes linked to the canonical C# type. The execute
method is surfaced when found.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable

from ..models import SemanticFrameworkItem
from .base import FrameworkExtractor


BACKGROUND_BASES = {"BackgroundService"}
HOSTED_INTERFACES = {"IHostedService", "IHostedService<>"}


class BackgroundServiceExtractor(FrameworkExtractor):
    kind = "BackgroundService"

    def extract(
        self, file_evidence: List[Dict[str, Any]]
    ) -> Iterable[SemanticFrameworkItem]:
        for payload in file_evidence:
            for type_evidence in payload.get("types", []):
                chain = self.base_type_chain(type_evidence)
                execute_method = self._find_execute_method(
                    payload, type_evidence.get("qualified_name", "")
                )
                properties = {
                    "service_type": type_evidence.get("qualified_name", ""),
                    "execute_method": execute_method,
                }
                if not self._matches(chain):
                    continue
                yield SemanticFrameworkItem(
                    kind=self.kind,
                    name=type_evidence.get("name", ""),
                    qualified_name=type_evidence.get("qualified_name", ""),
                    file_path=type_evidence.get("file_path", "") or payload.get("file_path", ""),
                    start_line=type_evidence.get("start_line", 0),
                    end_line=type_evidence.get("end_line", 0),
                    code="",
                    properties=properties,
                    confidence=0.9,
                    extraction_method="roslyn",
                    source_symbol_id=type_evidence.get("symbol_id", ""),
                )

    @staticmethod
    def _matches(chain: Iterable[str]) -> bool:
        for base in chain or ():
            compact = base.replace("Microsoft.Extensions.Hosting.", "").strip()
            if compact in BACKGROUND_BASES or compact in HOSTED_INTERFACES:
                return True
            if compact.startswith("BackgroundService"):
                return True
        return False

    @staticmethod
    def _find_execute_method(payload: Dict[str, Any], qualified_type: str) -> str:
        for function in payload.get("functions", []):
            if function.get("member_kind") != "method":
                continue
            if not qualified_type or function.get("qualified_name", "").startswith(qualified_type):
                name = str(function.get("name", ""))
                if name in {"ExecuteAsync", "StartAsync", "StopAsync"}:
                    return name
        return ""
