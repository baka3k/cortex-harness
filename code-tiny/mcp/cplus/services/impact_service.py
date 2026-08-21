from __future__ import annotations

from typing import Any, Dict, List

from fastapi import Request

from tools.common.call_evidence import traversal_outcome

from ..utils import fetch_node_annotations
from .graph_service import graph_query_service


class ImpactAnalyzer:
    def __init__(self):
        self.graph_service = graph_query_service

    @staticmethod
    def _external_nodes(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        external_markers = ("third_party", "external", "vendor", "/usr", "node_modules")
        externals: List[Dict[str, Any]] = []
        for node in nodes:
            path = (node.get("file") or "").lower()
            if any(marker in path for marker in external_markers):
                externals.append(node)
        return externals

    @staticmethod
    def _severity_weight(annotations: Dict[int, Dict[str, Any]]) -> float:
        weight = 0.0
        for entry in annotations.values():
            severity = (entry.get("severity") or "").lower()
            if severity in {"critical", "high"}:
                weight += 0.2
            elif severity in {"medium", "moderate"}:
                weight += 0.1
            elif severity == "low":
                weight += 0.05
        return weight

    def _suggest_tests(self, node_count: int, externals: int, annotations: Dict[int, Dict[str, Any]]) -> List[str]:
        suggestions: List[str] = []
        if node_count > 10:
            suggestions.append("Add regression tests covering the expanded call graph.")
        if externals:
            suggestions.append("Run integration tests for external/service boundaries.")
        if any((entry or {}).get("severity") in {"critical", "high"} for entry in annotations.values()):
            suggestions.append("Review annotated high-risk functions with manual code review.")
        if not suggestions:
            suggestions.append("Execute unit tests for directly impacted modules.")
        return suggestions

    async def analyze(self, request: Request, payload: Dict[str, Any]) -> Dict[str, Any]:
        graph = await self.graph_service.query_subgraph(request, payload)
        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])
        node_ids = [node.get("id") for node in nodes if isinstance(node.get("id"), int)]
        annotations = fetch_node_annotations(payload["db"], node_ids)
        externals = self._external_nodes(nodes)
        severity_weight = self._severity_weight(annotations)
        risk = min(
            1.0,
            0.2
            + (len(nodes) / 50.0)
            + (len(edges) / 150.0)
            + (len(externals) * 0.05)
            + severity_weight,
        )
        impacted = []
        for node in nodes:
            node_id = node.get("id")
            impacted.append(
                {
                    "id": node_id,
                    "qual_name": node.get("qual_name"),
                    "file": node.get("file"),
                    "depth": node.get("depth"),
                    "annotation": annotations.get(node_id),
                }
            )
        # Thread semantic coverage through the impact answer.  This service
        # reads through the graph HTTP proxy, which does not yet surface the
        # coverage plane, so coverage falls back to ``unknown`` — the safe
        # direction: empty impact sets stay ``incomplete`` rather than
        # claiming an authoritative negative.  "Unaffected" is measured on
        # nodes other than the seed function (id coerced to string on both
        # sides; graph node ids may be int-typed).
        seed_id = str(
            payload.get("function_id") or payload.get("id") or payload.get("functionId") or ""
        )
        impacted_beyond_seed = [
            node for node in impacted if str(node.get("id")) != seed_id
        ]
        coverage = graph.get("semantic_coverage")
        if coverage is None:
            from tools.common.call_evidence import frontier_coverage

            coverage = frontier_coverage([])
        return {
            "risk_score": round(risk, 3),
            "node_count": len(nodes),
            "edge_count": len(edges),
            "external_dependency_count": len(externals),
            "impacted_nodes": impacted,
            "annotations": annotations,
            "suggested_tests": self._suggest_tests(len(nodes), len(externals), annotations),
            "semantic_coverage": coverage,
            "outcome": traversal_outcome(
                str(coverage.get("status") or "unknown"),
                not impacted_beyond_seed,
            ),
        }


impact_analyzer = ImpactAnalyzer()


async def analyze_impact(request: Request, payload: Dict[str, Any]) -> Dict[str, Any]:
    return await impact_analyzer.analyze(request, payload)
