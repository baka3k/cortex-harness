"""
Workflow-aware impact scorer for hyper-graph.

Given a function_id and the call-graph nodes already fetched by ImpactAnalyzer,
this module queries Neo4j for workflow/navigator context and returns structured
impact data with severity ratings and a rule-based recommendation.

Usage::

    scorer = WorkflowImpactScorer(neo4j_driver, database="neo4j")
    result = await scorer.score(function_id, call_graph_nodes)
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Domain groups for severity rules
_PAYMENT_AUTH: Set[str] = {"payment", "auth", "authentication", "authorization"}
_ORDER_LOYALTY: Set[str] = {"order", "loyalty", "checkout"}

_TIMEOUT_SECONDS: float = 10.0
_DEFAULT_MAX_DEPTH: int = 4


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class WorkflowImpact:
    workflow_id: str
    workflow_name: str
    domain: str
    confidence: float
    impact_type: str      # "direct" | "indirect" | "cascade"
    step_index: int       # position in workflow; -1 if indirect
    call_depth: int       # 0 = direct HAS_STEP, 1..N = via CALLS chain
    severity: str         # "critical" | "high" | "medium" | "low"
    reason: str


@dataclass
class NavigatorImpact:
    navigator_id: str
    var_name: str
    nav_type: str         # "stack" | "tab" | "drawer"
    affected_route: str
    impact_type: str      # "route_missing" | "component_changed" | "param_schema_changed"


@dataclass
class WorkflowImpactResult:
    function_id: str
    directly_affected_workflows: List[WorkflowImpact] = field(default_factory=list)
    indirectly_affected_workflows: List[WorkflowImpact] = field(default_factory=list)
    cascade_workflows: List[WorkflowImpact] = field(default_factory=list)
    navigator_impacts: List[NavigatorImpact] = field(default_factory=list)
    shared_screen_conflict: bool = False
    workflow_risk_score: float = 0.0
    overall_risk_score: float = 0.0  # set by caller (impact_service) after merging base risk
    recommendation: str = ""


# ── Severity helpers ──────────────────────────────────────────────────────────

def _severity_from_domain(domain: str, step_index: int, call_depth: int) -> str:
    """
    Compute severity string from domain, step position, and call depth.

    Rules (in priority order):
    - payment/auth + step_index ≤ 2 → "critical"
    - payment/auth + call_depth == 0 (any other direct step) → "high"
    - payment/auth + indirect → "medium"
    - order/loyalty → "medium"
    - others → "low"
    """
    d = (domain or "").lower()
    if d in _PAYMENT_AUTH:
        if step_index >= 0 and step_index <= 2:
            return "critical"
        if call_depth == 0:
            return "high"
        return "medium"
    if d in _ORDER_LOYALTY:
        return "medium"
    return "low"


def _compute_workflow_risk(result: "WorkflowImpactResult") -> float:
    """
    Compute a workflow-level risk score in [0.0, 1.0].

    Formula:
      base   = 0.1 × len(direct_workflows)
      domain = Σ domain_weight per direct workflow
               (payment/auth → +0.3, order/loyalty → +0.2, other → +0.1)
      entry  = +0.15 per workflow where step_index == 0
      indir  = Σ indirect domain weight (payment/auth → +0.1, order/loyalty → +0.05)
      cascade= +0.2 if shared_screen_conflict
    """
    score = 0.1 * len(result.directly_affected_workflows)

    for w in result.directly_affected_workflows:
        d = w.domain.lower()
        if d in _PAYMENT_AUTH:
            score += 0.3
        elif d in _ORDER_LOYALTY:
            score += 0.2
        else:
            score += 0.1
        if w.step_index == 0:
            score += 0.15

    for w in result.indirectly_affected_workflows:
        d = w.domain.lower()
        if d in _PAYMENT_AUTH:
            score += 0.1
        elif d in _ORDER_LOYALTY:
            score += 0.05
        else:
            score += 0.02

    if result.shared_screen_conflict:
        score += 0.2

    return min(1.0, round(score, 3))


def _generate_recommendation(result: "WorkflowImpactResult") -> str:
    """
    Build a rule-based, human-readable recommendation string (sync, < 5 ms).
    No LLM calls.
    """
    parts: List[str] = []

    critical = [w for w in result.directly_affected_workflows if w.severity == "critical"]
    high = [w for w in result.directly_affected_workflows if w.severity == "high"]

    if critical:
        names = ", ".join(w.workflow_name for w in critical)
        first = critical[0]
        step_desc = (
            f" at step {first.step_index + 1} (entrypoint)"
            if first.step_index == 0
            else f" at step {first.step_index + 1}"
        )
        parts.append(f"CRITICAL: Changes affect {names}{step_desc}.")
    elif high:
        names = ", ".join(w.workflow_name for w in high)
        parts.append(f"HIGH: Changes directly affect {names}.")
    elif result.directly_affected_workflows:
        names = ", ".join(w.workflow_name for w in result.directly_affected_workflows)
        parts.append(f"Changes directly affect: {names}.")

    for w in result.indirectly_affected_workflows[:3]:
        parts.append(f"Also impacts {w.workflow_name} indirectly (depth={w.call_depth}).")

    if result.shared_screen_conflict:
        cascade_count = len(result.cascade_workflows)
        cascade_names = ", ".join(
            {w.workflow_name for w in result.cascade_workflows}  # unique names
        )
        parts.append(
            f"Shared screen/component used across {cascade_count} other workflow(s)"
            f" — cascade risk HIGH."
            + (f" Also affects: {cascade_names}." if cascade_names else "")
        )

    if result.navigator_impacts:
        routes = ", ".join(n.affected_route for n in result.navigator_impacts[:3])
        parts.append(f"Navigator routes affected: {routes}. Verify routing config.")

    # Recommend test strategy
    all_workflows = (
        result.directly_affected_workflows
        + result.indirectly_affected_workflows
        + result.cascade_workflows
    )
    domains = {w.domain.lower() for w in all_workflows}
    needs_e2e = bool(domains & _PAYMENT_AUTH)
    if needs_e2e:
        flow_names = " + ".join(sorted(domains & _PAYMENT_AUTH))
        parts.append(
            f"Recommended: run e2e tests for {flow_names} flows before merge."
        )
    elif all_workflows:
        parts.append("Recommended: run integration tests for affected workflows before merge.")

    if not parts:
        return "No workflow impact detected. Proceed with standard unit tests."

    return " ".join(parts)


# ── Scorer class ──────────────────────────────────────────────────────────────

class WorkflowImpactScorer:
    """
    Queries Neo4j for workflow/navigator impact of a changed function.

    Uses the sync `neo4j` driver (wrapped in run_in_executor) to match the
    existing MCP pattern in unified_mcp.py.
    """

    def __init__(self, neo4j_driver: Any, database: str = "neo4j") -> None:
        self._driver = neo4j_driver
        self._database = database

    def _run_sync(self, cypher: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        with self._driver.session(database=self._database) as session:
            result = session.run(cypher, params)
            return [dict(r) for r in result]

    async def _query(self, cypher: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        loop = asyncio.get_event_loop()
        return await asyncio.wait_for(
            loop.run_in_executor(None, self._run_sync, cypher, params),
            timeout=_TIMEOUT_SECONDS,
        )

    async def score(
        self,
        function_id: str,
        function_nodes: List[Dict[str, Any]],
        max_depth: int = _DEFAULT_MAX_DEPTH,
    ) -> WorkflowImpactResult:
        """
        Return WorkflowImpactResult for the given function.

        Args:
            function_id:    symbol_id of the changed function.
            function_nodes: call-graph nodes already fetched by ImpactAnalyzer
                            (used for node id context; not used in queries directly).
            max_depth:      cap on CALLS traversal depth (default 4).
        """
        result = WorkflowImpactResult(function_id=function_id)
        capped_depth = min(max_depth, _DEFAULT_MAX_DEPTH)

        # 1. Direct workflow membership ────────────────────────────────────────
        direct_rows = await self._query(
            f"""
            MATCH (w:Workflow)-[s:HAS_STEP]->(f:Function)
            WHERE f.symbol_id = $id OR f.file_path = $id
            RETURN w.workflow_id                 AS workflow_id,
                   w.name                        AS workflow_name,
                   w.domain                      AS domain,
                   coalesce(w.confidence, 0.5)   AS confidence,
                   coalesce(s.order, -1)          AS step_index
            """,
            {"id": function_id},
        )

        for row in direct_rows:
            domain = (row.get("domain") or "").lower()
            step_index = row.get("step_index")
            if step_index is None:
                step_index = -1
            sev = _severity_from_domain(domain, int(step_index), 0)
            result.directly_affected_workflows.append(
                WorkflowImpact(
                    workflow_id=row.get("workflow_id") or "",
                    workflow_name=row.get("workflow_name") or "",
                    domain=domain,
                    confidence=float(row.get("confidence") or 0.5),
                    impact_type="direct",
                    step_index=int(step_index),
                    call_depth=0,
                    severity=sev,
                    reason=(
                        f"Function is a direct step (index {int(step_index)}) in "
                        f"workflow '{row.get('workflow_name')}'."
                    ),
                )
            )

        direct_wf_ids = {w.workflow_id for w in result.directly_affected_workflows}

        # 2. Indirect membership via CALLS chain ───────────────────────────────
        # Neo4j does not allow parameterised path lengths; use safe integer interpolation.
        indirect_rows = await self._query(
            f"""
            MATCH (w:Workflow)-[:HAS_STEP]->(entry:Function)
            MATCH path = (entry)-[:CALLS*1..{capped_depth}]->(f:Function)
            WHERE (f.symbol_id = $id OR f.file_path = $id)
              AND NOT w.workflow_id IN $direct_ids
            RETURN DISTINCT
                   w.workflow_id                 AS workflow_id,
                   w.name                        AS workflow_name,
                   w.domain                      AS domain,
                   coalesce(w.confidence, 0.5)   AS confidence,
                   length(path)                  AS call_depth
            ORDER BY call_depth ASC, confidence DESC
            LIMIT 20
            """,
            {
                "id": function_id,
                "direct_ids": list(direct_wf_ids),
            },
        )

        for row in indirect_rows:
            domain = (row.get("domain") or "").lower()
            call_depth = int(row.get("call_depth") or 1)
            sev = _severity_from_domain(domain, -1, call_depth)
            result.indirectly_affected_workflows.append(
                WorkflowImpact(
                    workflow_id=row.get("workflow_id") or "",
                    workflow_name=row.get("workflow_name") or "",
                    domain=domain,
                    confidence=float(row.get("confidence") or 0.5),
                    impact_type="indirect",
                    step_index=-1,
                    call_depth=call_depth,
                    severity=sev,
                    reason=(
                        f"Function reachable via CALLS chain (depth={call_depth}) "
                        f"from workflow '{row.get('workflow_name')}'."
                    ),
                )
            )

        # 3. Navigator routes pointing at this function ────────────────────────
        nav_rows = await self._query(
            f"""
            MATCH (nav:Navigator)-[r:HAS_ROUTE]->(f:Function)
            WHERE f.symbol_id = $id OR f.file_path = $id
            RETURN coalesce(nav.id, nav.var_name)    AS navigator_id,
                   nav.var_name                      AS var_name,
                   coalesce(nav.nav_type, 'stack')   AS nav_type,
                   coalesce(r.name, f.name)           AS route_name
            """,
            {"id": function_id},
        )

        for row in nav_rows:
            result.navigator_impacts.append(
                NavigatorImpact(
                    navigator_id=row.get("navigator_id") or "",
                    var_name=row.get("var_name") or "",
                    nav_type=row.get("nav_type") or "stack",
                    affected_route=row.get("route_name") or "",
                    impact_type="component_changed",
                )
            )

        # 4. Shared-screen cascade (function is a screen used in multiple workflows)
        cascade_rows = await self._query(
            f"""
            MATCH (w1:Workflow)-[:HAS_STEP]->(s:Function)<-[:HAS_STEP]-(w2:Workflow)
            WHERE (s.symbol_id = $id OR s.file_path = $id)
              AND w1.workflow_id < w2.workflow_id
            RETURN DISTINCT
                   w1.workflow_id AS wf1_id, w1.name AS wf1_name, w1.domain AS wf1_domain,
                   w2.workflow_id AS wf2_id, w2.name AS wf2_name, w2.domain AS wf2_domain,
                   s.name         AS shared_screen
            LIMIT 10
            """,
            {"id": function_id},
        )

        if cascade_rows:
            result.shared_screen_conflict = True
            seen_wf_ids = {w.workflow_id for w in result.directly_affected_workflows}
            seen_wf_ids |= {w.workflow_id for w in result.indirectly_affected_workflows}

            for row in cascade_rows:
                for wf_id, wf_name, wf_domain in (
                    (row.get("wf1_id"), row.get("wf1_name"), row.get("wf1_domain")),
                    (row.get("wf2_id"), row.get("wf2_name"), row.get("wf2_domain")),
                ):
                    if wf_id and wf_id not in seen_wf_ids:
                        domain = (wf_domain or "").lower()
                        result.cascade_workflows.append(
                            WorkflowImpact(
                                workflow_id=wf_id,
                                workflow_name=wf_name or "",
                                domain=domain,
                                confidence=0.5,
                                impact_type="cascade",
                                step_index=-1,
                                call_depth=0,
                                severity=_severity_from_domain(domain, -1, 0),
                                reason=(
                                    f"Shared screen '{row.get('shared_screen')}' "
                                    f"is also a step in this workflow."
                                ),
                            )
                        )
                        seen_wf_ids.add(wf_id)

        # 5. Compute risk and recommendation ───────────────────────────────────
        result.workflow_risk_score = _compute_workflow_risk(result)
        result.recommendation = _generate_recommendation(result)

        return result
