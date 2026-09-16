#!/usr/bin/env python3
"""Fixed contract query set for the phase-11 MCP wire-contract harness.

Shared by `record_contract.py` (records Python-side ground truth) and
`compare_contract.py` (replays against the Rust server).

Two fixture sources are used (recorded in each fixture's `recorded_via`):

* `live-python-server` — the production `unified_mcp.py` FastMCP server,
  launched the way `code-tiny/mcp.sh` / `scripts/mcp-lifecycle.py` launch it
  (venv python, streamable HTTP, `stateless_http=True`, `json_response=True`)
  on a harness test port.
* `contract-layer` — the pure Python contract modules in-process
  (`cortex_harness.mcp_contract`, `code-tiny/mcp/tool_metadata.py`,
  `code-tiny/tools/common/project_registry.py`). Used for the error paths
  that only exist as contract-layer semantics (project_not_registered) and
  for the phase-11 Rust stub envelope (capability_unavailable), whose ground
  truth is by definition the contract layer applied to the documented legacy
  payload.

Every call below is deterministic *before any graph access* so the fixture
set can be recorded and replayed without FalkorDB/embedding state.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

REPO_ROOT = "/Users/user/AI/cortex-harness"

# Sorted unified tool names (unified_mcp._UNIFIED_TOOL_NAMES).
UNIFIED_TOOL_NAMES: List[str] = [
    "analyze_workflow_impact",
    "annotate_node",
    "compute_scc",
    "explore_graph",
    "find_callers_of_endpoint",
    "find_path_between_module",
    "find_paths",
    "find_screen_workflows",
    "find_workflows_containing",
    "get_api_call_chain",
    "get_endpoints",
    "get_framework_context",
    "get_ipc_message",
    "get_module_architecture_summary",
    "get_node_details",
    "get_project_modules",
    "get_project_special_files",
    "get_public_apis",
    "get_symbol",
    "inspect_parser_capabilities",
    "list_databases",
    "list_mcp_functions",
    "list_parsers",
    "list_possible_calls",
    "list_qdrant_collections",
    "list_up_entrypoint",
    "listup_class_matching_path",
    "listup_symbols_matching_file_path",
    "plan_dependency_order",
    "plan_file_dependency_order",
    "plan_function_dependency_order",
    "query_subgraph",
    "reconstruct_flow",
    "search_by_code",
    "search_functions",
    "semantic_search",
    "topological_sort",
    "trace_flow",
    "trace_flow_between_module",
]

# Required-parameter sentinels from each catalog entry's own example, used to
# fill required params for the parser-gate probes.
REQUIRED_SENTINELS: Dict[str, Dict[str, Any]] = {
    "search_functions": {"query": "handleClick|onClick"},
    "search_by_code": {"query": "malloc|calloc"},
    "get_symbol": {"node_id": "func_12345"},
    "get_node_details": {"node_ids": ["func_1", "func_2"]},
    "query_subgraph": {"function_id": "func_main"},
    "find_paths": {"start_function_id": "main", "end_function_id": "malloc"},
    "find_path_between_module": {
        "source_modules": ["sample_module"],
        "target_modules": ["sample_target"],
    },
    "listup_symbols_matching_file_path": {"modules": ["sample_module.c"]},
    "listup_class_matching_path": {"class_names": ["SampleClass", "SampleHandler"]},
    "list_up_entrypoint": {"modules": ["src/api/"]},
    "trace_flow": {"start_id": "sample_function"},
    "trace_flow_between_module": {
        "source_modules": ["sample_module"],
        "target_modules": ["target_module"],
    },
    "find_screen_workflows": {"node_a": "RewardHome"},
    "explore_graph": {"query": "function xử lý thanh toán"},
    "semantic_search": {"query": "allocate memory safely"},
    "annotate_node": {"node_id": "func_123", "note": "phase11 contract probe"},
    "inspect_parser_capabilities": {},
    "get_ipc_message": {"sender": "Activity"},
    "list_workflows_placeholder": {},
    "find_callers_of_endpoint": {"endpoint_path": "/api/users/:id"},
    "get_api_call_chain": {"component_name": "UserProfileScreen"},
    "analyze_workflow_impact": {"function_id": "func_123"},
    "find_workflows_containing": {"function_id": "func_123"},
    "reconstruct_flow": {"entry_context_json": "{}", "paths_json": "[]"},
}

# The one parser alias used by parser-gate probes. Unregistered on purpose:
# the response is the `unsupported_parser` error envelope, produced before
# any graph access.
BOGUS_PARSER = "phase11_bogus_parser"

# An unregistered project id for the project_not_registered fixture. The
# fixture compares the Python contract layer (ProjectNotRegisteredError →
# normalize_error) against the Rust server envelope.
UNREGISTERED_PROJECT = "phase11_unregistered_project"

# The only registered project in this harness config (`.cortext-harness/
# config/`): used by the capability_unavailable stub probe so the Rust
# dispatch passes project resolution and reaches the phase-11 graph stub.
REGISTERED_PROJECT = "cortext"

STUB_TOOL = "search_functions"
STUB_TOOL_ARGUMENTS = {
    "query": "handleClick|onClick",
    "parser_type": "cplus",
    "project_id": REGISTERED_PROJECT,
}

# The legacy payload documented for the phase-11 graph-tool stub; the Python
# contract layer normalizes it exactly like the Rust `dispatch` does.
STUB_LEGACY_PAYLOAD: Dict[str, Any] = {
    "ok": False,
    "error": {
        "type": "capability_unavailable",
        "tool": STUB_TOOL,
        "message": (
            f"Tool '{STUB_TOOL}' requires the graph query engine, which is not "
            "part of the phase-11 MCP framework build; graph tools land in phase 12."
        ),
    },
}


def missing_required_cases() -> List[Dict[str, Any]]:
    """`{}` for every tool that has catalog-required parameters."""
    # Required params per the hand-written catalog (phase-11 Rust catalog).
    required_by_tool = {
        "search_functions": ["query"],
        "search_by_code": ["query"],
        "get_symbol": ["node_id"],
        "get_node_details": ["node_ids"],
        "query_subgraph": ["function_id"],
        "find_paths": ["start_function_id", "end_function_id"],
        "find_path_between_module": ["source_modules", "target_modules"],
        "listup_symbols_matching_file_path": ["modules"],
        "listup_class_matching_path": ["class_names"],
        "list_up_entrypoint": ["modules"],
        "trace_flow": ["start_id"],
        "trace_flow_between_module": ["source_modules", "target_modules"],
        "plan_dependency_order": ["modules"],
        "plan_file_dependency_order": ["modules"],
        "plan_function_dependency_order": ["modules"],
        "find_screen_workflows": ["node_a"],
        "explore_graph": ["query"],
        "semantic_search": ["query"],
        "annotate_node": ["node_id"],
        "reconstruct_flow": ["entry_context_json", "paths_json"],
        "find_callers_of_endpoint": ["endpoint_path"],
        "analyze_workflow_impact": ["function_id"],
        "find_workflows_containing": ["function_id"],
    }
    cases: List[Dict[str, Any]] = []
    for tool in UNIFIED_TOOL_NAMES:
        required = required_by_tool.get(tool)
        if not required:
            continue
        cases.append(
            {
                "id": f"{tool}.missing_required",
                "tool": tool,
                "arguments": {},
                "recorded_via": "live-python-server",
                "notes": f"catalog-required: {required}",
            }
        )
    return cases


def parser_gate_cases() -> List[Dict[str, Any]]:
    """Required params filled + unregistered parser → `unsupported_parser`.

    Covers every tool whose dispatch path gates the parser before graph
    access (the 21 proxied tools + the unified-registered tools that call
    `_resolve_direct_capability_context` / their own gate).
    """
    no_parser_gate = {
        "list_parsers",
        "list_mcp_functions",
        "compute_scc",
        "topological_sort",
        "plan_dependency_order",
        "plan_file_dependency_order",
        "plan_function_dependency_order",
        "reconstruct_flow",
    }
    # analyze_workflow_impact is excluded: with a filled function_id the
    # Python tool treats an unknown parser as *degraded data* (risk score +
    # embedded subgraph_error) instead of a fatal gate — its internals are
    # ported with the graph tools in phase 12.
    excluded = {"analyze_workflow_impact"}
    extra_args: Dict[str, Dict[str, Any]] = {
        "inspect_parser_capabilities": {"project_id": ""},
        "get_ipc_message": {},
        "list_databases": {},
        "list_qdrant_collections": {},
        "list_possible_calls": {},
        "get_api_call_chain": {},
    }
    cases: List[Dict[str, Any]] = []
    for tool in UNIFIED_TOOL_NAMES:
        if tool in no_parser_gate or tool in excluded:
            continue
        arguments = dict(REQUIRED_SENTINELS.get(tool, {}))
        arguments.update(extra_args.get(tool, {}))
        arguments["parser_type"] = BOGUS_PARSER
        cases.append(
            {
                "id": f"{tool}.unsupported_parser",
                "tool": tool,
                "arguments": arguments,
                "recorded_via": "live-python-server",
                "notes": "parser gate fires before graph access",
            }
        )
    return cases


def success_cases() -> List[Dict[str, Any]]:
    cases: List[Dict[str, Any]] = [
        {
            "id": "list_parsers.summary",
            "tool": "list_parsers",
            "arguments": {"detail_level": "summary"},
            "recorded_via": "live-python-server",
        },
        {
            "id": "list_parsers.full",
            "tool": "list_parsers",
            "arguments": {"detail_level": "full"},
            "recorded_via": "live-python-server",
        },
        {
            "id": "list_mcp_functions.catalog",
            "tool": "list_mcp_functions",
            "arguments": {},
            "recorded_via": "contract-layer",
            "notes": (
                "catalog listing from tool_metadata.build_catalog; the live "
                "unified server re-syncs inputs from Python signatures "
                "(documented divergence)"
            ),
        },
        {
            "id": "compute_scc.chain",
            "tool": "compute_scc",
            "arguments": {
                "nodes": ["auth", "db", "api"],
                "edges": [
                    {"from": "auth", "to": "db"},
                    {"from": "db", "to": "api"},
                ],
            },
            "recorded_via": "live-python-server",
        },
        {
            "id": "topological_sort.chain",
            "tool": "topological_sort",
            "arguments": {
                "nodes": ["auth", "db", "api"],
                "edges": [
                    {"from": "db", "to": "api"},
                    {"from": "api", "to": "auth"},
                ],
            },
            "recorded_via": "live-python-server",
        },
        {
            "id": "topological_sort.cycle_condense",
            "tool": "topological_sort",
            "arguments": {
                "nodes": ["A", "B"],
                "edges": [
                    {"from": "A", "to": "B"},
                    {"from": "B", "to": "A"},
                ],
                "on_cycle": "auto_condense_scc",
            },
            "recorded_via": "live-python-server",
        },
    ]
    return cases


def error_literal_cases() -> List[Dict[str, Any]]:
    cases: List[Dict[str, Any]] = [
        {
            "id": "compute_scc.empty",
            "tool": "compute_scc",
            "arguments": {},
            "recorded_via": "live-python-server",
        },
        {
            "id": "topological_sort.empty",
            "tool": "topological_sort",
            "arguments": {},
            "recorded_via": "live-python-server",
        },
        {
            "id": "topological_sort.cycle_error",
            "tool": "topological_sort",
            "arguments": {
                "nodes": ["A", "B"],
                "edges": [
                    {"from": "A", "to": "B"},
                    {"from": "B", "to": "A"},
                ],
                "on_cycle": "error",
            },
            "recorded_via": "live-python-server",
        },
        {
            "id": "list_parsers.invalid_detail_level",
            "tool": "list_parsers",
            "arguments": {"detail_level": "bogus"},
            "recorded_via": "live-python-server",
        },
    ]
    return cases


def contract_layer_cases() -> List[Dict[str, Any]]:
    cases: List[Dict[str, Any]] = [
        {
            "id": "get_symbol.project_not_registered",
            "tool": "get_symbol",
            "arguments": {"node_id": "n1", "project_id": UNREGISTERED_PROJECT},
            # Phase-12 contract: unregistered project ids resolve via the
            # naming convention (`code_graph == project_id`) and fan out —
            # recorded live like every other graph case.
            "recorded_via": "live-python-server",
            "notes": (
                "Phase-11 recorded the ProjectNotRegisteredError contract "
                "layer; the unified search contract now treats an unknown "
                "project_id as an out-of-band shard (raw db fallback), so "
                "this case replays live like the phase-12 graph fixtures."
            ),
        },
        {
            "id": f"{STUB_TOOL}.capability_unavailable_stub",
            "tool": STUB_TOOL,
            "arguments": STUB_TOOL_ARGUMENTS,
            # Phase-12 wired the real graph engine — the stub envelope no
            # longer exists on either server, so record live.
            "recorded_via": "live-python-server",
            "notes": (
                "phase-11 stub probe; phase-12 replaced the stub with the "
                "real graph query engine, so the live payload is recorded"
            ),
        },
        {
            "id": "get_project_modules.project_not_registered",
            "tool": "get_project_modules",
            "arguments": {
                "project_id": UNREGISTERED_PROJECT,
                "parser_type": "cplus",
            },
            "recorded_via": "live-python-server",
            "notes": (
                "same phase-12 fanout contract as get_symbol."
                "project_not_registered — unregistered id resolves to the "
                "raw db and the project-context fanout returns empty results"
            ),
        },
    ]
    return cases


def all_cases() -> List[Dict[str, Any]]:
    cases: List[Dict[str, Any]] = []
    cases.extend(missing_required_cases())
    cases.extend(parser_gate_cases())
    cases.extend(success_cases())
    cases.extend(error_literal_cases())
    cases.extend(contract_layer_cases())
    return cases


def live_cases() -> List[Dict[str, Any]]:
    """Cases executed against the live Python server (record mode)."""
    return [case for case in all_cases() if case["recorded_via"] == "live-python-server"]


def tool_names_for_wire() -> List[str]:
    return UNIFIED_TOOL_NAMES


def server_metadata() -> Dict[str, Optional[str]]:
    return {
        "server_name": "graph_mcp",
        "server_version": "1.2.0",
    }


if __name__ == "__main__":
    cases = all_cases()
    print(f"{len(cases)} cases across {len(UNIFIED_TOOL_NAMES)} tools")
    live = [case for case in cases if case["recorded_via"] == "live-python-server"]
    print(f"  live-python-server: {len(live)}")
    print(f"  contract-layer: {len(cases) - len(live)}")
    covered = {case["tool"] for case in cases}
    missing = [tool for tool in UNIFIED_TOOL_NAMES if tool not in covered]
    print(f"  tools without a case: {missing or 'none'}")
