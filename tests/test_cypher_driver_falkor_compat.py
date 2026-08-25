from __future__ import annotations

import inspect
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.graph.core.cypher_driver import CypherGraphDriver  # noqa: E402


def test_module_path_query_uses_falkordb_compatible_variable_path():
    source = inspect.getsource(CypherGraphDriver._find_module_paths_directed)

    assert "shortestPath" not in source
    assert "MATCH p=(s)" in source
    assert "ORDER BY length(p)" in source


def test_mcp_backends_do_not_use_neo4j_only_shortest_path_match():
    backend_paths = [
        CODE_TINY / "mcp" / "cplus" / "cplus_mcp.py",
        CODE_TINY / "mcp" / "android" / "android_mcp.py",
        CODE_TINY / "mcp" / "java" / "java_mcp.py",
        CODE_TINY / "mcp" / "fastmcp_server.py",
        CODE_TINY / "tools" / "common" / "graph_expander.py",
    ]

    for path in backend_paths:
        source = path.read_text(encoding="utf-8")
        assert "MATCH p=shortestPath" not in source, path
        assert "MATCH p = shortestPath" not in source, path
