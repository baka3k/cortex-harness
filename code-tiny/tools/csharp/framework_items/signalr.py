"""SignalR hub extractor.

Detects classes that inherit `Hub` or `Hub<T>`. Emits `SignalRHub` nodes.
ASP.NET overlay still owns `endpoints.MapHub<T>("/path")` registrations.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable

from ..models import SemanticFrameworkItem
from .base import FrameworkExtractor


HUB_BASES = {"Hub", "Hub<>"}


class SignalRHubExtractor(FrameworkExtractor):
    kind = "SignalRHub"

    def extract(
        self, file_evidence: List[Dict[str, Any]]
    ) -> Iterable[SemanticFrameworkItem]:
        for payload in file_evidence:
            for type_evidence in payload.get("types", []):
                chain = self.base_type_chain(type_evidence)
                if not self._is_hub(chain):
                    continue
                yield SemanticFrameworkItem(
                    kind=self.kind,
                    name=type_evidence.get("name", ""),
                    qualified_name=type_evidence.get("qualified_name", ""),
                    file_path=type_evidence.get("file_path", "") or payload.get("file_path", ""),
                    start_line=type_evidence.get("start_line", 0),
                    end_line=type_evidence.get("end_line", 0),
                    code="",
                    properties={
                        "hub_name": type_evidence.get("qualified_name", ""),
                        "client_methods": [],
                    },
                    confidence=0.85,
                    extraction_method="roslyn",
                    source_symbol_id=type_evidence.get("symbol_id", ""),
                )

    @staticmethod
    def _is_hub(chain: Iterable[str]) -> bool:
        for base in chain or ():
            compact = base.replace("Microsoft.AspNetCore.SignalR.", "").strip()
            if any(compact == known or compact.startswith(known) for known in HUB_BASES):
                return True
        return False
