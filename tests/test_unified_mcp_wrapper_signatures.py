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


def test_proxied_tools_accept_result_formatting_options():
    """Regression guard for the unified/backend schema-drift bug class.

    Before the pass-through proxy refactor, wrappers in ``unified_mcp.py``
    re-declared a subset of the backend signature and silently dropped fields
    like ``content_mode`` and ``include_raw_fields``. Pydantic then rejected
    any payload containing those fields with ``Unexpected keyword argument``.

    Now these tools are dynamically registered from the backend callable, so
    their JSON schema must include every named parameter that the backend
    exposes. Asserting against the live ``mcp_server`` tool registry catches
    any future re-introduction of the drift.
    """
    import asyncio
    import sys

    sys.path.insert(0, str(ROOT / "code-tiny" / "mcp"))
    import unified_mcp  # noqa: E402

    formatting_opts = {"content_mode", "include_raw_fields"}
    proxied_query_tools = [
        "listup_symbols_matching_file_path",
        "search_functions",
        "search_by_code",
        "get_symbol",
        "get_node_details",
        "find_paths",
        "query_subgraph",
        "list_possible_calls",
        "semantic_search",
    ]

    async def _props() -> dict[str, set[str]]:
        live = await unified_mcp.mcp_server.list_tools()
        return {
            tool.name: set(tool.parameters.get("properties", {}).keys())
            for tool in live
        }

    properties_by_name = asyncio.run(_props())
    for tool_name in proxied_query_tools:
        props = properties_by_name.get(tool_name)
        assert props is not None, f"{tool_name} not registered"
        missing = formatting_opts - props
        assert not missing, f"{tool_name} missing fields {missing}"


def test_fanout_tools_expose_parser_type_in_live_schema():
    """Every fan-out tool must let callers scope with ``parser_type``.

    Without it a schema-validated client can never avoid the cross-engine
    fan-out. Asserted against the live registry so backend signature drift
    is caught.
    """
    import asyncio
    import sys

    sys.path.insert(0, str(ROOT / "code-tiny" / "mcp"))
    import unified_mcp  # noqa: E402

    async def _props() -> dict[str, set[str]]:
        live = await unified_mcp.mcp_server.list_tools()
        return {
            tool.name: set(tool.parameters.get("properties", {}).keys())
            for tool in live
        }

    properties_by_name = asyncio.run(_props())
    missing = {}
    for tool_name in sorted(unified_mcp._FANOUT_SEARCH_TOOLS):
        props = properties_by_name.get(tool_name)
        assert props is not None, f"{tool_name} not registered"
        if "parser_type" not in props:
            missing[tool_name] = sorted(props)
    assert not missing, f"fan-out tools without parser_type: {sorted(missing)}"


def test_fanout_tools_advertise_parser_type_in_catalog():
    spec = importlib.util.spec_from_file_location("test_tool_metadata", TOOL_METADATA)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    import sys

    sys.path.insert(0, str(ROOT / "code-tiny" / "mcp"))
    import unified_mcp  # noqa: E402

    assert set(module.FANOUT_SEARCH_TOOL_NAMES) == set(unified_mcp._FANOUT_SEARCH_TOOLS)

    catalog = module.build_catalog(set(module.FANOUT_SEARCH_TOOL_NAMES))
    seen = set()
    for entry in catalog:
        inputs = {item["name"] for item in entry.get("inputs", [])}
        assert "parser_type" in inputs, entry["name"]
        seen.add(entry["name"])
    assert seen == set(module.FANOUT_SEARCH_TOOL_NAMES)


def test_catalog_contains_each_registered_tool_once():
    spec = importlib.util.spec_from_file_location("test_tool_metadata_unique", TOOL_METADATA)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    requested = {entry["name"] for entry in module._FULL_CATALOG}
    catalog = module.build_catalog(requested)
    names = [entry["name"] for entry in catalog]

    assert len(names) == len(set(names))
    assert set(names) == requested


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
        # Internal helpers (prefixed with `_`) may still take a resolved `db`
        # positional arg per plan exemption — they receive the resolved graph
        # name, not a tool input.
        if name.startswith("_"):
            continue
        if not name.startswith("tool_"):
            continue
        positional = node.args.args
        defaults = [None] * (len(positional) - len(node.args.defaults)) + list(
            node.args.defaults
        )
        for argument, default in zip(positional, defaults):
            assert argument.arg != "db", f"db param should not exist in tool wrapper signatures: {name}"
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


def test_graph_backends_use_shared_provider_default_and_hyper_graph():
    for backend in GRAPH_BACKENDS:
        source = backend.read_text(encoding="utf-8")
        assert "normalize_graph_provider_name(value)" in source, backend
        assert 'or "hyper_graph"' in source, backend


# ── Repo-scope acceptance gate (plan 261016-1100 phase 05) ───────────────

# Tools that intentionally do NOT take ``repo`` (RR surface exclusions —
# docs/REPO_QUERY_RULES.md mirrors this list):
#   * annotate_node            — write path (R6 mirror)
#   * list_* / inspect_*       — metadata/contract tools
#   * get_project_* / get_public_apis / get_endpoints / get_framework_context
#                              — ProjectModule/ApiEndpoint labels carry no repo
#   * compute_scc / topological_sort / plan_dependency_order
#                              — operate on caller-provided nodes/edges only
#   * reconstruct_flow         — pure JSON reshaping, no graph read
#   * find_screen_workflows    — queries live in tools/ts/workflow_finder.py
#                                (deferred with the writer integration)
#   * analyze_workflow_impact / find_workflows_containing
#                              — :Workflow nodes carry no repo property
_REPO_EXCLUDED_UNIFIED_TOOLS = frozenset(
    {
        "annotate_node",
        "list_databases",
        "list_qdrant_collections",
        "list_parsers",
        "inspect_parser_capabilities",
        "list_mcp_functions",
        "get_project_modules",
        "get_public_apis",
        "get_endpoints",
        "get_module_architecture_summary",
        "get_project_special_files",
        "get_framework_context",
        "compute_scc",
        "topological_sort",
        "plan_dependency_order",
        "reconstruct_flow",
        "find_screen_workflows",
        "analyze_workflow_impact",
        "find_workflows_containing",
    }
)


def _live_tool_properties() -> tuple[object, dict[str, set[str]]]:
    import asyncio
    import sys

    sys.path.insert(0, str(ROOT / "code-tiny" / "mcp"))
    import unified_mcp  # noqa: E402

    async def _props():
        live = await unified_mcp.mcp_server.list_tools()
        return {
            tool.name: set(tool.parameters.get("properties", {}).keys())
            for tool in live
        }

    return unified_mcp, asyncio.run(_props())


def test_repo_scope_acceptance_gate_live_schema():
    """Every query-read tool exposes ``repo``; every other tool does not.

    A missing ``repo`` on a query tool is a BUG (the param would be dropped
    by schema validation), not a skip. The required set is derived from the
    registry minus the documented exclusion list, so adding a new query tool
    without ``repo`` fails here until it is either given the param or added
    to the exclusion list with a reason.
    """
    unified_mcp, properties_by_name = _live_tool_properties()
    registered = set(properties_by_name)
    expected_required = {
        name
        for name in unified_mcp._UNIFIED_TOOL_NAMES
        if name not in _REPO_EXCLUDED_UNIFIED_TOOLS
    }
    assert registered >= unified_mcp._UNIFIED_TOOL_NAMES, (
        f"unified registry missing tools: {sorted(unified_mcp._UNIFIED_TOOL_NAMES - registered)}"
    )

    missing_repo = {}
    for tool_name in sorted(expected_required):
        props = properties_by_name[tool_name]
        if "repo" not in props:
            missing_repo[tool_name] = sorted(props)
    assert not missing_repo, f"query tools without repo param: {missing_repo}"

    leaking_repo = {}
    for tool_name in sorted(_REPO_EXCLUDED_UNIFIED_TOOLS):
        props = properties_by_name.get(tool_name, set())
        if "repo" in props:
            leaking_repo[tool_name] = sorted(props)
    assert not leaking_repo, f"excluded tools expose repo: {leaking_repo}"


def test_repo_scope_catalog_matches_live_schema():
    """Catalog entries advertising ``repo`` must be exactly the live tools
    that accept it (catalog ↔ signature consistency)."""
    catalog = _catalog()
    advertising = {
        entry["name"]
        for entry in catalog
        if any(item.get("name") == "repo" for item in entry.get("inputs", []))
    }
    unified_mcp, properties_by_name = _live_tool_properties()
    live_with_repo = {
        name for name, props in properties_by_name.items() if "repo" in props
    }
    # Only compare over the unified surface (java_mcp-only catalog entries
    # are dev-launcher tools that never register here).
    comparable = advertising & unified_mcp._UNIFIED_TOOL_NAMES
    assert comparable <= live_with_repo, (
        f"catalog advertises repo but live schema lacks it: {sorted(comparable - live_with_repo)}"
    )
    unexpected = live_with_repo - advertising
    assert not unexpected, (
        f"live schema has repo but catalog does not advertise it: {sorted(unexpected)}"
    )
