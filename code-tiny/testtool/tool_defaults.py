"""
Default JSON payloads for each MCP tool.
Edit these to match your project's typical inputs.
"""
from __future__ import annotations

import os
from typing import Any, Dict

# Map: tool_name -> default arguments dict
TOOL_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "activate_project": {
        "parser_type": "cplus",
        "database_name": "neo4j",
    },
    "list_databases": {},
    "list_parsers": {},
    "list_mcp_functions": {},
    "list_qdrant_collections": {
        "include_vectors": False,
    },
    "search_functions": {
        "query": "MyClass|myFunction",
        "db": "neo4j",
        "top_k": 50,
        "content_mode": "summary",
        "include_raw_fields": False,
    },
    "get_id_by_name": {
        "query": "MyClass|myFunction",
        "db": "neo4j",
        "top_k": 20,
        "content_mode": "summary",
        "include_raw_fields": False,
    },
    "search_by_code": {
        "query": "DataNormal|Ticket",
        "db": "neo4j",
        "top_k": 500,
        "content_mode": "summary",
        "include_raw_fields": False,
    },
    "get_symbol": {
        "node_id": "YOUR_NODE_ID",
        "db": "neo4j",
        "content_mode": "summary",
        "include_raw_fields": False,
    },
    "get_node_details": {
        "node_ids": ["NODE_ID_1", "NODE_ID_2"],
        "db": "neo4j",
        "content_mode": "summary",
        "include_raw_fields": False,
    },
    "query_subgraph": {
        "function_id": "YOUR_FUNCTION_ID",
        "db": "neo4j",
        "direction": "all",
        "max_depth": 2,
        "content_mode": "summary",
        "include_raw_fields": False,
    },
    "find_paths": {
        "start_function_id": "START_FUNCTION_ID",
        "end_function_id": "END_FUNCTION_ID",
        "db": "neo4j",
        "max_depth": 8,
        "content_mode": "summary",
        "include_raw_fields": False,
    },
    "find_path_between_module": {
        "source_modules": ["src/module_a"],
        "target_modules": ["src/module_b"],
        "db": "neo4j",
        "max_depth": 8,
        "content_mode": "summary",
        "include_raw_fields": False,
    },
    "listup_symbols_matching_file_path": {
        "modules": ["src/main"],
        "db": "neo4j",
        "content_mode": "summary",
        "include_raw_fields": False,
    },
    "listup_class_matching_path": {
        "class_names": ["MyClass"],
        "db": "neo4j",
        "content_mode": "summary",
        "include_raw_fields": False,
    },
    "list_up_entrypoint": {
        "modules": ["src/main"],
        "db": "neo4j",
        "top_k": 200,
        "content_mode": "summary",
        "include_raw_fields": False,
    },
    "trace_flow": {
        "start_id": "START_NODE_ID",
        "db": "neo4j",
        "direction": "out",
        "max_depth": 6,
        "content_mode": "summary",
        "include_raw_fields": False,
    },
    "trace_flow_between_module": {
        "source_modules": ["src/module_a"],
        "target_modules": ["src/module_b"],
        "db": "neo4j",
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
    },
    "annotate_node": {
        "node_id": "YOUR_NODE_ID",
        "db": "neo4j",
        "note": "Important function",
        "tags": "review,todo",
        "severity": "medium",
    },
    "get_ipc_message": {
        "sender": "ComponentA",
        "receiver": "ComponentB",
    },
    "list_possible_calls": {
        "db": "neo4j",
        "top_k": 200,
        "content_mode": "summary",
        "include_raw_fields": False,
    },
}


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
