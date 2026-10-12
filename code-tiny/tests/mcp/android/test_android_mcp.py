import importlib.util
import sys
import unittest
from pathlib import Path


_MCP_DIR = Path(__file__).resolve().parents[3] / "mcp"
if str(_MCP_DIR) not in sys.path:
    sys.path.insert(0, str(_MCP_DIR))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


android_mcp = _load(
    "android_mcp_under_test",
    _MCP_DIR / "android" / "android_mcp.py",
)


class AndroidMCPTests(unittest.IsolatedAsyncioTestCase):
    def test_falkordb_path_edges_keep_type_and_domain_node_ids(self):
        # The FalkorDB driver normalizes Edge objects to a flat properties
        # dict with underscore-prefixed meta keys; _record_rel must surface
        # them under the plain type/start_id/end_id contract (mirrors the
        # cplus backend test).
        graph = android_mcp._paths_to_graph([{
            "nodes": [
                {"id": "function-a", "_graph_id": 10},
                {"id": "function-b", "_graph_id": 20},
            ],
            "edges": [{
                "_type": "CALLS",
                "_start_id": 10,
                "_end_id": 20,
                "confidence": 0.9,
            }],
        }])

        self.assertEqual(graph["edges"], [{
            "type": "CALLS",
            "properties": {"confidence": 0.9},
            "start_id": "function-a",
            "end_id": "function-b",
        }])
        # Unlike the cplus backend, android keeps ``_graph_id`` on node
        # properties — it is part of the android node payload contract.
        self.assertEqual(graph["nodes"][0]["properties"]["_graph_id"], 10)

    def test_plain_edge_dict_still_uses_top_level_keys(self):
        rel = {"type": "CALLS", "start_id": "a", "end_id": "b", "properties": {"count": 2}}
        self.assertEqual(
            android_mcp._record_rel(rel),
            {"type": "CALLS", "properties": {"count": 2}, "start_id": "a", "end_id": "b"},
        )

    def test_neo4j_relationship_tuple_keeps_type_and_endpoints(self):
        rel = ({"id": "a"}, "CALLS", {"id": "b"})
        self.assertEqual(
            android_mcp._record_rel(rel),
            {"type": "CALLS", "properties": {}, "start_id": "a", "end_id": "b"},
        )
