import ast
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UNIFIED_MCP = ROOT / "code-tiny" / "mcp" / "unified_mcp.py"
TOOL_METADATA = ROOT / "code-tiny" / "mcp" / "tool_metadata.py"
GRAPH_BACKENDS = [
    ROOT / "code-tiny" / "mcp" / "cplus" / "cplus_mcp.py",
    ROOT / "code-tiny" / "mcp" / "android" / "android_mcp.py",
    ROOT / "code-tiny" / "mcp" / "fastmcp_server.py",
]


def _function_nodes() -> dict[str, ast.AsyncFunctionDef]:
    tree = ast.parse(UNIFIED_MCP.read_text(encoding="utf-8"))
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
    }


def _function_args(name: str) -> set[str]:
    node = _function_nodes().get(name)
    if node is None:
        raise AssertionError(f"{name} not found")
    return {arg.arg for arg in node.args.args}


def _catalog() -> list[dict]:
    spec = importlib.util.spec_from_file_location("test_tool_metadata", TOOL_METADATA)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._FULL_CATALOG


def test_query_wrappers_accept_result_formatting_options():
    wrappers = [
        "tool_listup_symbols_matching_file_path",
        "tool_search_functions",
        "tool_search_by_code",
        "tool_get_symbol",
        "tool_get_node_details",
        "tool_find_paths",
        "tool_query_subgraph",
        "tool_list_possible_calls",
    ]

    for wrapper in wrappers:
        args = _function_args(wrapper)
        assert "content_mode" in args, wrapper
        assert "include_raw_fields" in args, wrapper


def test_catalog_inputs_are_accepted_by_handwritten_wrappers():
    wrappers = _function_nodes()
    mismatches = {}

    for entry in _catalog():
        wrapper_name = f"tool_{entry['name']}"
        wrapper = wrappers.get(wrapper_name)
        if wrapper is None:
            continue
        accepted = {arg.arg for arg in wrapper.args.args}
        advertised = {item["name"] for item in entry.get("inputs", [])}
        missing = sorted(advertised - accepted)
        if missing:
            mismatches[wrapper_name] = missing

    assert not mismatches, repr(mismatches)


def test_unified_routed_tool_signatures_do_not_default_to_neo4j():
    for name, node in _function_nodes().items():
        positional = node.args.args
        defaults = [None] * (len(positional) - len(node.args.defaults)) + list(
            node.args.defaults
        )
        for argument, default in zip(positional, defaults):
            if argument.arg != "db":
                continue
            assert not (
                isinstance(default, ast.Constant) and default.value == "neo4j"
            ), name


def test_graph_backends_honor_configured_falkordb_provider():
    for backend in GRAPH_BACKENDS:
        source = backend.read_text(encoding="utf-8")
        assert "CODE_GRAPH_PROVIDER" in source, backend
        assert "DEFAULT_GRAPH_PROVIDER" in source, backend
        assert "GraphProvider.FALKORDB" in source, backend
        assert "DEFAULT_GRAPH_DB" in source, backend
