from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Dict, List, Tuple


def stable_id(*parts: object) -> str:
    normalized = "\x1f".join(str(part).strip() for part in parts)
    return "web::" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class EndpointFact:
    endpoint_id: str
    project_id: str
    framework: str
    http_method: str
    path: str
    file_path: str
    start_line: int
    handler_name: str
    handler_scope: str = ""
    handler_file: str = ""
    handler_label: str = "Function"
    resolution_status: str = "unresolved"
    confidence: float = 0.7

    def node_row(self) -> Dict[str, object]:
        return {
            "id": self.endpoint_id,
            "symbol_id": self.endpoint_id,
            "project_id": self.project_id,
            "framework": self.framework,
            "name": f"{self.http_method} {self.path}",
            "http_method": self.http_method,
            "path": self.path,
            "route": self.path,
            "file_path": self.file_path,
            "start_line": self.start_line,
            "handler_name": self.handler_name,
            "handler_scope": self.handler_scope,
            "resolution_status": self.resolution_status,
            "confidence": self.confidence,
        }

    def relationship_row(self) -> Dict[str, object]:
        return {
            "id": stable_id(self.endpoint_id, "HANDLES", self.handler_name, self.handler_file),
            "type": "HANDLES",
            "endpoint_id": self.endpoint_id,
            "project_id": self.project_id,
            "framework": self.framework,
            "handler_name": self.handler_name,
            "handler_scope": self.handler_scope,
            "handler_file": self.handler_file,
            "handler_label": self.handler_label,
            "resolution_status": self.resolution_status,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class WebAnalysisResult:
    project_id: str
    endpoints: Tuple[EndpointFact, ...]

    def graph_rows(self) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
        return (
            [item.node_row() for item in self.endpoints],
            [item.relationship_row() for item in self.endpoints],
        )
