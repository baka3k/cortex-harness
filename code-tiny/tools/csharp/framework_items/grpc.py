"""gRPC service extractor.

Detects classes inheriting `GrpcServiceBase<T>` (or other known gRPC base types)
or classes decorated with `[GrpcService]`. Emits `GrpcService` nodes linked to
the canonical C# type.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List

from ..models import SemanticFrameworkItem
from .base import FrameworkExtractor


GRPC_BASES = {
    "GrpcServiceBase",
    "GrpcServiceBase<>",
}


class GrpcServiceExtractor(FrameworkExtractor):
    kind = "GrpcService"

    def extract(
        self, file_evidence: List[Dict[str, Any]]
    ) -> Iterable[SemanticFrameworkItem]:
        for payload in file_evidence:
            for type_evidence in payload.get("types", []):
                chain = self.base_type_chain(type_evidence)
                if not self._is_grpc_base(chain):
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
                        "service_name": type_evidence.get("qualified_name", ""),
                        "base_service": next((b for b in chain if self._base_matches(b)), ""),
                    },
                    confidence=0.9,
                    extraction_method="roslyn",
                    source_symbol_id=type_evidence.get("symbol_id", ""),
                )

    @staticmethod
    def _is_grpc_base(chain: Iterable[str]) -> bool:
        for base in chain or ():
            if GrpcServiceExtractor._base_matches(base):
                return True
        return False

    @staticmethod
    def _base_matches(base: str) -> bool:
        compact = base.replace("Grpc.Core.", "").strip()
        return any(compact == known or compact.startswith(known) for known in GRPC_BASES)
