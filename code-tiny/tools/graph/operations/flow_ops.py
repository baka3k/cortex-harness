"""
Workflow Node Operations

Handles creation and querying of :Workflow nodes and :HAS_STEP relationships
in the code graph.
"""

from typing import Any, Dict, List, Optional
from tools.graph.core.base import GraphDriver


class WorkflowNodeOperations:
    """MERGE-based operations for :Workflow nodes and their step edges."""

    @staticmethod
    async def upsert_workflows(
        driver: GraphDriver,
        workflows: List[Dict[str, Any]],
        database: Optional[str] = None,
    ) -> int:
        """
        MERGE :Workflow nodes from a batch.

        Expected keys per row:
          workflow_id, workflow_name, domain, description, confidence,
          entrypoint_id, language, project, kind
        """
        if not workflows:
            return 0
        query = """
        UNWIND $rows AS row
        MERGE (w:Workflow {workflow_id: row.workflow_id})
        SET w.name             = row.workflow_name,
            w.domain           = row.domain,
            w.description      = row.description,
            w.confidence       = row.confidence,
            w.entrypoint_id    = row.entrypoint_id,
            w.language         = row.language,
            w.project          = row.project,
            w.kind             = row.kind,
            w.updated_at       = datetime()
        RETURN count(w) AS count
        """
        records, _, _ = await driver.execute_query(query, {"rows": workflows}, database)
        return records[0]["count"] if records else 0

    @staticmethod
    async def upsert_workflow_steps(
        driver: GraphDriver,
        step_rows: List[Dict[str, Any]],
        database: Optional[str] = None,
    ) -> int:
        """
        MERGE :HAS_STEP relationships between :Workflow and :Function nodes.

        Expected keys per row:  workflow_id, function_id, step_order
        """
        if not step_rows:
            return 0
        query = """
        UNWIND $rows AS row
        MATCH  (w:Workflow  {workflow_id: row.workflow_id})
        MATCH  (f:Function  {id:          row.function_id})
        MERGE  (w)-[s:HAS_STEP {order: row.step_order}]->(f)
        RETURN count(s) AS count
        """
        records, _, _ = await driver.execute_query(query, {"rows": step_rows}, database)
        return records[0]["count"] if records else 0

    @staticmethod
    async def list_workflows(
        driver: GraphDriver,
        project: Optional[str] = None,
        language: Optional[str] = None,
        domain: Optional[str] = None,
        limit: int = 50,
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return workflow summary rows, optionally filtered."""
        filters = []
        params: Dict[str, Any] = {"limit": limit}
        if project:
            filters.append("w.project = $project")
            params["project"] = project
        if language:
            filters.append("w.language = $language")
            params["language"] = language
        if domain:
            filters.append("w.domain = $domain")
            params["domain"] = domain
        where = ("WHERE " + " AND ".join(filters)) if filters else ""
        query = f"""
        MATCH (w:Workflow)
        {where}
        RETURN w.workflow_id  AS workflow_id,
               w.name         AS name,
               w.domain       AS domain,
               w.description  AS description,
               w.confidence   AS confidence,
               w.entrypoint_id AS entrypoint_id,
               w.language     AS language,
               w.project      AS project,
               w.kind         AS kind
        ORDER BY w.confidence DESC, w.name ASC
        LIMIT $limit
        """
        records, _, _ = await driver.execute_query(query, params, database)
        return [dict(r) for r in records]

    @staticmethod
    async def get_workflow_steps(
        driver: GraphDriver,
        workflow_id: str,
        database: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Return the :Workflow node + ordered :Function steps.

        Returns {'workflow': {...}, 'steps': [{...}, ...]}
        """
        wf_query = """
        MATCH (w:Workflow {workflow_id: $wid})
        RETURN w.workflow_id  AS workflow_id,
               w.name         AS name,
               w.domain       AS domain,
               w.description  AS description,
               w.confidence   AS confidence,
               w.entrypoint_id AS entrypoint_id,
               w.language     AS language,
               w.project      AS project,
               w.kind         AS kind
        """
        steps_query = """
        MATCH (w:Workflow {workflow_id: $wid})-[s:HAS_STEP]->(f:Function)
        RETURN s.order         AS step_order,
               f.id            AS id,
               f.name          AS name,
               f.qualified_name AS qualified_name,
               f.file_path     AS file_path,
               f.start_line    AS start_line,
               f.end_line      AS end_line,
               f.summary       AS summary,
               f.kind          AS kind
        ORDER BY s.order ASC
        """
        params = {"wid": workflow_id}
        wf_records, _, _ = await driver.execute_query(wf_query, params, database)
        step_records, _, _ = await driver.execute_query(steps_query, params, database)
        if not wf_records:
            return {}
        return {
            "workflow": dict(wf_records[0]),
            "steps": [dict(r) for r in step_records],
        }

    @staticmethod
    async def search_workflows(
        driver: GraphDriver,
        query_text: str,
        limit: int = 20,
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Full-text / substring search across workflow names and descriptions."""
        query = """
        MATCH (w:Workflow)
        WHERE toLower(w.name) CONTAINS toLower($q)
           OR toLower(w.description) CONTAINS toLower($q)
           OR toLower(w.domain) CONTAINS toLower($q)
        RETURN w.workflow_id  AS workflow_id,
               w.name         AS name,
               w.domain       AS domain,
               w.description  AS description,
               w.confidence   AS confidence,
               w.entrypoint_id AS entrypoint_id,
               w.language     AS language,
               w.project      AS project,
               w.kind         AS kind
        ORDER BY w.confidence DESC, w.name ASC
        LIMIT $limit
        """
        records, _, _ = await driver.execute_query(
            query, {"q": query_text, "limit": limit}, database
        )
        return [dict(r) for r in records]
