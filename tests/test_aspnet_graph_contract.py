from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

CODE_TINY = Path(__file__).resolve().parents[1] / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.common.aspnet.builders import fact, relationship
from tools.common.aspnet.cli_runtime import apply_graph
from tools.common.aspnet.models import AnalysisModule, AnalysisResult, SourceSpan
from tools.graph.writer.aspnet_writer import AspNetFactWriter


class _FakeDriver:
    def __init__(self, active_state=None) -> None:
        self.queries = []
        self.active_state = active_state

    async def execute_query(self, query, parameters=None, database=None):
        self.queries.append((query, parameters, database))
        if "RETURN state.active_generation" in query:
            return (self.active_state or [], [], None)
        rows = (parameters or {}).get("rows") or ()
        return ([{"count": len(rows) or 1}], ["count"], None)

    async def close(self):
        return None


class AspNetGraphContractTest(unittest.IsolatedAsyncioTestCase):
    async def test_nodes_are_written_before_relationships_with_allowlisted_contract(self) -> None:
        endpoint = fact(
            kind="HttpEndpoint", name="GET /", framework="aspnet_core", project_id="p",
            project_name="P", module_id="m", source=SourceSpan("Program.cs"),
        )
        route = fact(
            kind="Route", name="/", framework="aspnet_core", project_id="p",
            project_name="P", module_id="m", source=SourceSpan("Program.cs"),
        )
        edge = relationship(relationship_type="MAPPED_TO", source_fact=endpoint, target_fact=route)
        generation = "g1"
        driver = _FakeDriver()
        writer = AspNetFactWriter(driver)
        summary = await writer.stage_generation(
            project_id="p", module_id="m", framework="aspnet_core", generation_id=generation,
            node_rows=[endpoint.to_graph_node(generation), route.to_graph_node(generation)],
            relationship_rows=[edge.to_graph_row(generation)],
        )
        self.assertEqual(summary, {"nodes": 2, "relationships": 1})
        self.assertIn("MERGE (node:", driver.queries[0][0])
        self.assertIn("MERGE (source)-[rel:MAPPED_TO", driver.queries[-1][0])

    async def test_unknown_label_is_rejected(self) -> None:
        driver = _FakeDriver()
        writer = AspNetFactWriter(driver)
        with self.assertRaises(ValueError):
            await writer.write_nodes(
                [{"kind": "Unknown", "framework": "aspnet_core", "generation_id": "g"}],
                "aspnet_core", "g",
            )

    async def test_partial_run_preserves_existing_complete_generation(self) -> None:
        driver = _FakeDriver([{"active_generation": "complete-g", "coverage_status": "complete"}])
        result = AnalysisResult(
            project_id="p", project_name="P", framework="aspnet_core",
            modules=(AnalysisModule("m", ".", "aspnet_core"),),
            coverage_status="partial",
        )
        args = SimpleNamespace(neo4j_batch_size=1000, verbose=False)
        with patch(
            "tools.common.aspnet.cli_runtime._create_driver",
            new=AsyncMock(return_value=(driver, None)),
        ):
            summary = await apply_graph(args, result)
        self.assertEqual(summary["stage"], "preserved_complete")
        self.assertEqual(summary["preserved_modules"], 1)
        self.assertFalse(any("MERGE (node:" in query for query, _, _ in driver.queries))


if __name__ == "__main__":
    unittest.main()
