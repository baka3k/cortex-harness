from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from fastapi import Request

from ..utils import fetch_node_annotations
from .graph_service import graph_query_service

logger = logging.getLogger(__name__)


class ImpactAnalyzer:
    def __init__(self):
        self.graph_service = graph_query_service
        self._workflow_scorer: Optional[Any] = None  # WorkflowImpactScorer, lazy-init

    def _get_workflow_scorer(self, db: str) -> Optional[Any]:
        """
        Lazy-init a WorkflowImpactScorer backed by a direct Neo4j driver.

        Returns None if:
        - WORKFLOW_IMPACT_DISABLED env var is set to '1'
        - Required env vars (NEO4J_URI / NEO4J_USER / NEO4J_PASS) are missing
        - neo4j package is not installed
        - Driver construction fails for any reason
        """
        if os.environ.get("WORKFLOW_IMPACT_DISABLED", "").strip() == "1":
            return None

        if self._workflow_scorer is not None:
            return self._workflow_scorer

        try:
            import neo4j as _neo4j  # noqa: PLC0415
            from tools.common.workflow_impact_scorer import WorkflowImpactScorer  # noqa: PLC0415

            uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
            user = os.environ.get("NEO4J_USER", "")
            pwd = os.environ.get("NEO4J_PASS", "")
            if user and pwd:
                driver = _neo4j.GraphDatabase.driver(uri, auth=(user, pwd))
            else:
                driver = _neo4j.GraphDatabase.driver(uri)

            self._workflow_scorer = WorkflowImpactScorer(driver, database=db)
            return self._workflow_scorer
        except Exception as exc:
            logger.debug("WorkflowImpactScorer unavailable: %s", exc)
            return None

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
        base_result: Dict[str, Any] = {
            "risk_score": round(risk, 3),
            "node_count": len(nodes),
            "edge_count": len(edges),
            "external_dependency_count": len(externals),
            "impacted_nodes": impacted,
            "annotations": annotations,
            "suggested_tests": self._suggest_tests(len(nodes), len(externals), annotations),
        }

        # ── Workflow impact layer (non-breaking extension) ─────────────────────
        function_id = payload.get("function_id", "")
        db = payload.get("db", "neo4j")
        scorer = self._get_workflow_scorer(db)
        if scorer and function_id:
            try:
                max_depth = int(payload.get("max_depth") or 4)
                wf_impact = await scorer.score(function_id, nodes, max_depth=max_depth)

                # Merge function-level risk with workflow risk
                overall = min(1.0, round(0.4 * risk + 0.6 * wf_impact.workflow_risk_score, 3))
                wf_impact.overall_risk_score = overall

                base_result["workflow_impact"] = {
                    "directly_affected_workflows": [
                        {
                            "name": w.workflow_name,
                            "domain": w.domain,
                            "severity": w.severity,
                            "step_index": w.step_index,
                            "reason": w.reason,
                        }
                        for w in wf_impact.directly_affected_workflows
                    ],
                    "indirectly_affected_workflows": [
                        {
                            "name": w.workflow_name,
                            "domain": w.domain,
                            "severity": w.severity,
                            "call_depth": w.call_depth,
                        }
                        for w in wf_impact.indirectly_affected_workflows
                    ],
                    "cascade_workflows": [
                        {
                            "name": w.workflow_name,
                            "domain": w.domain,
                            "severity": w.severity,
                            "reason": w.reason,
                        }
                        for w in wf_impact.cascade_workflows
                    ],
                    "navigator_impacts": [
                        {
                            "navigator": n.var_name,
                            "route": n.affected_route,
                            "impact_type": n.impact_type,
                        }
                        for n in wf_impact.navigator_impacts
                    ],
                    "shared_screen_conflict": wf_impact.shared_screen_conflict,
                    "workflow_risk_score": wf_impact.workflow_risk_score,
                    "overall_risk_score": overall,
                    "recommendation": wf_impact.recommendation,
                }
                # Override top-level risk_score with the combined overall score
                base_result["risk_score"] = overall
            except Exception as exc:
                logger.warning("WorkflowImpactScorer failed for %s: %s", function_id, exc)
                base_result["workflow_impact"] = {"error": str(exc)}

        return base_result


impact_analyzer = ImpactAnalyzer()


async def analyze_impact(request: Request, payload: Dict[str, Any]) -> Dict[str, Any]:
    return await impact_analyzer.analyze(request, payload)
