"""
Default JSON payloads and category map for each MCP tool.
Edit these to match your project's typical inputs.

Each registered MCP tool should appear exactly once in TOOL_CATEGORIES.
Tools not yet categorized are rendered under the "Other" bucket.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List

# Map: tool_name -> default arguments dict
TOOL_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "activate_project": {
        "parser_type": "cplus",
        "database_name": "hyper_graph",
    },
    "list_databases": {},
    "list_parsers": {},
    "list_mcp_functions": {},
    "list_qdrant_collections": {
        "include_vectors": False,
    },
    "search_functions": {
        "query": "MyClass|myFunction",
        "db": "hyper_graph",
        "top_k": 50,
        "content_mode": "summary",
        "include_raw_fields": False,
    },
    "search_by_code": {
        "query": "DataNormal|Ticket",
        "db": "hyper_graph",
        "top_k": 500,
        "content_mode": "summary",
        "include_raw_fields": False,
    },
    "get_symbol": {
        "node_id": "YOUR_NODE_ID",
        "db": "hyper_graph",
        "content_mode": "summary",
        "include_raw_fields": False,
    },
    "get_node_details": {
        "node_ids": ["NODE_ID_1", "NODE_ID_2"],
        "db": "hyper_graph",
        "content_mode": "summary",
        "include_raw_fields": False,
    },
    "query_subgraph": {
        "function_id": "YOUR_FUNCTION_ID",
        "db": "hyper_graph",
        "direction": "all",
        "max_depth": 2,
        "content_mode": "summary",
        "include_raw_fields": False,
    },
    "find_paths": {
        "start_function_id": "START_FUNCTION_ID",
        "end_function_id": "END_FUNCTION_ID",
        "db": "hyper_graph",
        "max_depth": 8,
        "content_mode": "summary",
        "include_raw_fields": False,
    },
    "find_path_between_module": {
        "source_modules": ["src/module_a"],
        "target_modules": ["src/module_b"],
        "db": "hyper_graph",
        "max_depth": 8,
        "content_mode": "summary",
        "include_raw_fields": False,
    },
    "listup_symbols_matching_file_path": {
        "modules": ["src/main"],
        "db": "hyper_graph",
        "content_mode": "summary",
        "include_raw_fields": False,
    },
    "listup_class_matching_path": {
        "class_names": ["MyClass"],
        "db": "hyper_graph",
        "content_mode": "summary",
        "include_raw_fields": False,
    },
    "list_up_entrypoint": {
        "modules": ["src/main"],
        "db": "hyper_graph",
        "top_k": 200,
        "content_mode": "summary",
        "include_raw_fields": False,
    },
    "trace_flow": {
        "start_id": "START_NODE_ID",
        "db": "hyper_graph",
        "direction": "out",
        "max_depth": 6,
        "content_mode": "summary",
        "include_raw_fields": False,
    },
    "trace_flow_between_module": {
        "source_modules": ["src/module_a"],
        "target_modules": ["src/module_b"],
        "db": "hyper_graph",
        "max_depth": 8,
        "direction": "out",
        "content_mode": "summary",
        "include_raw_fields": False,
    },
    "semantic_search": {
        "query": "function that handles user authentication",
        "mode": "combined",
        "top_k": 10,
        "content_mode": "summary",
        "include_raw_fields": False,
        "expand_graph": False,
        "graph_depth": 2,
        "graph_direction": "both",
        "graph_limit": 50,
    },
    "annotate_node": {
        "node_id": "YOUR_NODE_ID",
        "db": "hyper_graph",
        "note": "Important function",
        "tags": "review,todo",
        "severity": "medium",
    },
    "get_ipc_message": {
        "sender": "ComponentA",
        "receiver": "ComponentB",
    },
    "list_possible_calls": {
        "db": "hyper_graph",
        "top_k": 200,
        "content_mode": "summary",
        "include_raw_fields": False,
    },
    # --- New tools (added 2026-07-27) ----------------------------------------
    "inspect_parser_capabilities": {
        "parser_type": "cplus",
        "db": "",
    },
    "compute_scc": {
        "nodes": ["auth", "payment"],
        "edges": [{"from": "auth", "to": "payment"}],
        "edge_semantics": "depends_on",
    },
    "topological_sort": {
        "nodes": ["auth", "payment", "user"],
        "edges": [
            {"from": "user", "to": "auth"},
            {"from": "auth", "to": "payment"},
        ],
        "edge_semantics": "depends_on",
        "output_mode": "both",
        "on_cycle": "auto_condense_scc",
    },
    "plan_dependency_order": {
        "modules": ["src/auth", "src/payment"],
        "db": "hyper_graph",
        "on_cycle": "auto_condense_scc",
    },
    "plan_file_dependency_order": {
        "modules": ["src/auth", "src/payment"],
        "db": "hyper_graph",
        "include_cross_module": True,
        "on_cycle": "auto_condense_scc",
    },
    "plan_function_dependency_order": {
        "modules": ["src/auth", "src/payment"],
        "db": "hyper_graph",
        "include_cross_module": True,
        "on_cycle": "auto_condense_scc",
    },
    "find_screen_workflows": {
        "project_id": "YOUR_PROJECT_ID",
        "node_a": "HomeScreen",
        "node_b": "DetailScreen",
        "max_hops": 8,
        "max_paths": 100,
    },
    "explore_graph": {
        "query": "user login authentication flow",
        "mode": "hybrid",
        "top_k": 10,
    },
    "reconstruct_flow": {
        "entry_context_json": (
            '{"type":"backend","entry_point":"main",'
            '"entry_node_id":"n1","screen":null,"trigger":null}'
        ),
        "paths_json": (
            '[{"path_id":"path_1","nodes":[{"node_id":"n1",'
            '"name":"main","mapped_type":"function",'
            '"location":{"file":"main.c","line":10}}],"edges":[]}]'
        ),
    },
    "get_project_modules": {
        "project_id": "YOUR_PROJECT_ID",
        "limit": 50,
    },
    "get_public_apis": {
        "project_id": "YOUR_PROJECT_ID",
        "limit": 50,
    },
    "get_endpoints": {
        "project_id": "YOUR_PROJECT_ID",
        "protocol": "http",
        "limit": 50,
    },
    "get_module_architecture_summary": {
        "project_id": "YOUR_PROJECT_ID",
        "limit": 50,
    },
    "get_project_special_files": {
        "project_id": "YOUR_PROJECT_ID",
        "limit": 50,
    },
    "get_framework_context": {
        "project_id": "YOUR_PROJECT_ID",
        "framework": "spring",
        "limit": 50,
    },
    "find_callers_of_endpoint": {
        "endpoint_path": "/api/users/:id",
        "http_method": "GET",
    },
    "get_api_call_chain": {
        "component_name": "UserProfileScreen",
        "max_depth": 5,
    },
    "analyze_workflow_impact": {
        "function_id": "YOUR_NODE_ID",
        "direction": "downstream",
        "max_depth": 4,
    },
    "find_workflows_containing": {
        "function_id": "YOUR_NODE_ID",
        "include_indirect": True,
    },
}


# Map: category name -> ordered list of tool names in that category.
# Every entry in TOOL_DEFAULTS must appear in exactly one category;
# the renderer falls back to "Other" for tools missing from the map.
TOOL_CATEGORIES: Dict[str, List[str]] = {
    "Session & Discovery": [
        "activate_project",
        "list_databases",
        "list_parsers",
        "inspect_parser_capabilities",
        "list_mcp_functions",
        "list_qdrant_collections",
    ],
    "Search": [
        "search_functions",
        "search_by_code",
        "semantic_search",
        "explore_graph",
        "get_symbol",
        "get_node_details",
    ],
    "Graph Traversal": [
        "query_subgraph",
        "find_paths",
        "find_path_between_module",
        "trace_flow",
        "trace_flow_between_module",
        "list_possible_calls",
        "list_up_entrypoint",
        "listup_symbols_matching_file_path",
        "listup_class_matching_path",
    ],
    "Planning & Dependency": [
        "compute_scc",
        "topological_sort",
        "plan_dependency_order",
        "plan_file_dependency_order",
        "plan_function_dependency_order",
    ],
    "Project Context": [
        "get_project_modules",
        "get_public_apis",
        "get_endpoints",
        "get_module_architecture_summary",
        "get_project_special_files",
        "get_framework_context",
    ],
    "Fullstack & Workflow": [
        "find_callers_of_endpoint",
        "get_api_call_chain",
        "analyze_workflow_impact",
        "find_workflows_containing",
        "find_screen_workflows",
        "reconstruct_flow",
        "get_ipc_message",
    ],
    "Annotation": [
        "annotate_node",
    ],
}

# Inverse map: tool name -> category. Built once at import time so callers can
# look up a tool's bucket in O(1) without rebuilding the dict on every render.
_CATEGORY_OF: Dict[str, str] = {
    name: category
    for category, names in TOOL_CATEGORIES.items()
    for name in names
}

# Category rendered for tools not present in TOOL_CATEGORIES.
OTHER_CATEGORY = "Other"


def category_of(tool_name: str) -> str:
    """Return the primary category for ``tool_name``, falling back to ``OTHER_CATEGORY``."""
    return _CATEGORY_OF.get(tool_name, OTHER_CATEGORY)


_TEST_DIR = os.path.join(os.path.dirname(__file__), "input")


def get_default(tool_name: str) -> Dict[str, Any]:
    """Return default payload for a tool.

    Priority:
      1. temp/test/{tool_name}.json  — file-based defaults (edit freely)
      2. TOOL_DEFAULTS dict          — in-code fallback
      3. {}
    """
    import copy
    import json

    json_path = os.path.join(_TEST_DIR, f"{tool_name}.json")
    if os.path.isfile(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            pass  # corrupted file → fall through to dict default
    return copy.deepcopy(TOOL_DEFAULTS.get(tool_name, {}))
