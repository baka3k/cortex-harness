#!/usr/bin/env python3
"""Phase-12 graph-tool contract query set + shared client helpers.

Dùng chung cho `record_graph.py` (ghi ground truth từ server Python sống) và
`compare_graph.py` (replayAgainst server Rust). Case set phủ toàn bộ graph
tools của unified MCP trên dữ liệu thật của harness (graphs `cortext`,
`stock`, `procsample` — projected qua `project_id` naming convention; chỉ
`cortext` được đăng ký trong dev.json).

Volatile fields được khai báo trong `compare_graph.MASKED_KEYS` (kế thừa
phase-11) — không case nào phụ thuộc timestamp/time.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

REPO_ROOT = "/Users/hieplq1.aip/AI/cortex-harness"

# Node id tĩnh có thật trong graph `dogfood_p05` (sibling instance) — được
# dùng cho các case hit-path; dữ liệu harness không đổi giữa record/compare.
STOCK_FILE_NODE = "deploy/ec2/configure_deerflow_stock_mcp.py"
STOCK_MAIN_FN = "main/0@deploy/ec2/configure_deerflow_stock_mcp.py"

MARKER_QUERY = "parse|config"


def graph_cases() -> List[Dict[str, Any]]:
    cases: List[Dict[str, Any]] = []

    def add(case_id: str, tool: str, arguments: Dict[str, Any], notes: str = "") -> None:
        case = {
            "id": case_id,
            "tool": tool,
            "arguments": arguments,
            "recorded_via": "live-python-server",
        }
        if notes:
            case["notes"] = notes
        cases.append(case)

    # ── Discovery / introspection ────────────────────────────────────────────
    add("list_databases.live", "list_databases", {})
    add(
        "inspect_parser_capabilities.cplus",
        "inspect_parser_capabilities",
        {"parser_type": "cplus"},
    )
    add("list_qdrant_collections.live", "list_qdrant_collections", {})

    # ── search_functions (fanout + scoped parser) ────────────────────────────
    add(
        "search_functions.fanout",
        "search_functions",
        {"query": MARKER_QUERY},
        notes="parser-less fanout across android+cplus engines with dedup merge",
    )
    add(
        "search_functions.cplus_cortext",
        "search_functions",
        {"query": MARKER_QUERY, "parser_type": "cplus", "project_id": "cortext"},
    )
    add(
        "search_by_code.fanout",
        "search_by_code",
        {"query": "import json|argparse", "limit": 5},
    )
    add(
        "search_by_code.cplus",
        "search_by_code",
        {"query": "import json", "parser_type": "cplus", "project_id": "cortext", "limit": 5},
    )

    # ── symbol fetch ─────────────────────────────────────────────────────────
    add(
        "get_symbol.hit",
        "get_symbol",
        {"node_id": STOCK_FILE_NODE, "project_id": "dogfood_p05"},
    )
    add(
        "get_symbol.miss",
        "get_symbol",
        {"node_id": "phase12/definitely/not/a/node.py", "project_id": "cortext"},
    )
    add(
        "get_node_details.hit",
        "get_node_details",
        {"node_ids": [STOCK_FILE_NODE, STOCK_MAIN_FN], "project_id": "dogfood_p05"},
    )
    add(
        "list_possible_calls.cortext",
        "list_possible_calls",
        {"project_id": "cortext", "limit": 10},
    )
    add(
        "listup_symbols_matching_file_path.cortext",
        "listup_symbols_matching_file_path",
        {"modules": ["sync"], "project_id": "cortext", "parser_type": "cplus"},
    )
    add(
        "listup_class_matching_path.cortext",
        "listup_class_matching_path",
        {"class_names": ["Config"], "project_id": "cortext", "parser_type": "cplus"},
    )
    add(
        "list_up_entrypoint.cortext",
        "list_up_entrypoint",
        {"modules": ["sync"], "project_id": "cortext", "parser_type": "cplus", "limit": 10},
    )

    # ── traversal ────────────────────────────────────────────────────────────
    add(
        "query_subgraph.hit",
        "query_subgraph",
        {
            "function_id": STOCK_MAIN_FN,
            "direction": "out",
            "max_depth": 1,
            "project_id": "dogfood_p05",
            "parser_type": "android",
        },
    )
    add(
        "query_subgraph.miss",
        "query_subgraph",
        {
            "function_id": "phase12_no_such_function",
            "direction": "all",
            "max_depth": 2,
            "project_id": "cortext",
            "parser_type": "cplus",
        },
    )
    add(
        "find_paths.miss",
        "find_paths",
        {
            "start_function_id": STOCK_MAIN_FN,
            "end_function_id": "phase12_unreachable_target",
            "max_depth": 2,
            "project_id": "dogfood_p05",
            "parser_type": "android",
        },
    )
    add(
        "trace_flow.hit",
        "trace_flow",
        {
            "start_id": STOCK_MAIN_FN,
            "max_depth": 2,
            "limit": 5,
            "project_id": "dogfood_p05",
            "parser_type": "android",
        },
    )
    add(
        "find_path_between_module.miss",
        "find_path_between_module",
        {
            "source_modules": ["phase12_no_such_src"],
            "target_modules": ["phase12_no_such_dst"],
            "max_depth": 2,
            "project_id": "cortext",
            "parser_type": "cplus",
        },
    )
    add(
        "trace_flow_between_module.miss",
        "trace_flow_between_module",
        {
            "source_modules": ["phase12_no_such_src"],
            "target_modules": ["phase12_no_such_dst"],
            "max_depth": 2,
            "project_id": "cortext",
            "parser_type": "cplus",
        },
    )

    # ── explore / semantic ───────────────────────────────────────────────────
    add(
        "explore_graph.cortext",
        "explore_graph",
        {"query": "hàm xử lý sync config", "project_id": "cortext", "top_k": 5},
        notes="hybrid mode: graph-keyword + BM25 lane, semantic lane empty (Python-plane embedder)",
    )
    add(
        "explore_graph.graph_expanded",
        "explore_graph",
        {
            "query": "sync config loader",
            "project_id": "cortext",
            "top_k": 5,
            "mode": "graph_expanded",
        },
    )
    add(
        "semantic_search.cortext",
        "semantic_search",
        {
            "query": "parse config file safely",
            "project_id": "cortext",
            "parser_type": "cplus",
            "expand_graph": True,
        },
    )
    add(
        "semantic_search.empty_scope",
        "semantic_search",
        {"query": "anything at all", "collection": "cortext"},
        notes="explicit collection scope with no local-store match",
    )

    # ── bridge / workflows / project context ─────────────────────────────────
    add(
        "find_callers_of_endpoint.empty",
        "find_callers_of_endpoint",
        {"endpoint_path": "/api/phase12/nothing", "http_method": "GET"},
    )
    add(
        "get_api_call_chain.empty",
        "get_api_call_chain",
        {"endpoint_path": "/api/phase12/nothing"},
    )
    add(
        "analyze_workflow_impact.hit",
        "analyze_workflow_impact",
        {
            "function_id": STOCK_MAIN_FN,
            "project_id": "dogfood_p05",
            "max_depth": 2,
            "parser_type": "android",
        },
    )
    add(
        "find_workflows_containing.empty",
        "find_workflows_containing",
        {"function_id": STOCK_MAIN_FN, "project_id": "dogfood_p05"},
    )
    add(
        "get_project_modules.cortext",
        "get_project_modules",
        {"project_id": "cortext"},
    )
    add(
        "get_public_apis.cortext",
        "get_public_apis",
        {"project_id": "cortext"},
    )
    add(
        "get_endpoints.cortext",
        "get_endpoints",
        {"project_id": "cortext"},
    )
    add(
        "get_module_architecture_summary.cortext",
        "get_module_architecture_summary",
        {"project_id": "cortext", "item_limit": 3},
    )
    add(
        "get_project_special_files.cortext",
        "get_project_special_files",
        {"project_id": "cortext"},
    )
    add(
        "get_framework_context.cortext",
        "get_framework_context",
        {"project_id": "cortext"},
    )
    add(
        "find_screen_workflows.miss",
        "find_screen_workflows",
        {"project_id": "cortext", "node_a": "Phase12NoSuchScreen"},
    )
    add(
        "get_ipc_message.live",
        "get_ipc_message",
        {"sender": "Activity"},
    )

    # ── pure flow reconstructor ──────────────────────────────────────────────
    add(
        "reconstruct_flow.full",
        "reconstruct_flow",
        {
            "entry_context_json": json.dumps(
                {
                    "type": "backend",
                    "entry_point": "handle_request",
                    "entry_node_id": "fn_handle_request",
                    "screen": None,
                    "trigger": "http",
                }
            ),
            "paths_json": json.dumps(
                [
                    {
                        "path_id": "p1",
                        "nodes": [
                            {
                                "node_id": "fn_handle_request",
                                "name": "handle_request",
                                "mapped_type": "function",
                                "location": {"file": "a.py", "line": 10},
                            },
                            {
                                "node_id": "fn_validate",
                                "name": "validate",
                                "mapped_type": "function",
                                "location": {"file": "a.py", "line": 20},
                            },
                        ],
                        "edges": [
                            {"from": "fn_handle_request", "to": "fn_validate", "type": "CALLS"}
                        ],
                    }
                ]
            ),
        },
    )
    add(
        "reconstruct_flow.no_entry",
        "reconstruct_flow",
        {
            "entry_context_json": json.dumps({"type": "backend", "entry_point": "x", "entry_node_id": "ghost"}),
            "paths_json": json.dumps([{"path_id": "p1", "nodes": [], "edges": []}]),
        },
    )
    return cases
