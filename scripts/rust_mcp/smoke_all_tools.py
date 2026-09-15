#!/usr/bin/env python3
"""Smoke-test EVERY tool on both live MCP servers.

A tool "passes" when the server returns a well-formed tool result — success or
a structured error envelope (validation/project errors are expected without
arguments). A tool FAILS on transport errors, timeouts, malformed envelopes,
or is_error with a non-contract shape.

Usage: python scripts/rust_mcp/smoke_all_tools.py [--graph 8788] [--mind 8789]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from compare_graph import _call_with_timeout  # noqa: E402
from record_contract import live_tools_list  # noqa: E402

# Hand-picked minimal arguments per tool (everything else is called no-args,
# where a structured validation error is the expected healthy response).
TOOL_ARGS: dict[str, list[dict]] = {
    # graph server — read paths over the live instance
    "get_symbol": [{"query": "GraphRuntime"}],
    "search_functions": [{"query": "boot"}],
    "semantic_search": [{"query": "parse configuration file and load settings"}],
    "explore_graph": [{"query": "parse configuration file", "mode": "semantic"}],
    "search_by_code": [{"query": "execute_query"}],
    "list_qdrant_collections": [{}],
    "list_databases": [{}],
    "list_parsers": [{}],
    "list_mcp_functions": [{}],
    "get_project_modules": [{}],
    "get_public_apis": [{}],
    "get_endpoints": [{}],
    "get_framework_context": [{}],
    "get_project_special_files": [{}],
    "inspect_parser_capabilities": [{}],
    "get_ipc_message": [{}],
    "find_callers_of_endpoint": [{"endpoint_path": "/api/users"}],
    "get_node_details": [{"node_ids": ["nonexistent-id"]}],
    "get_paragraph_text": [{"source_id": "x", "paragraph_id": 0}],
    "get_symbol_details": [{"query": "GraphRuntime"}],
    "find_workflows_containing": [{"function_id": "nonexistent"}],
    "analyze_workflow_impact": [{"function_id": "nonexistent"}],
    "find_screen_workflows": [{"node_a": "nonexistent"}],
    "compute_scc": [{"nodes": ["a", "b"], "edges": [{"from": "a", "to": "b"}]}],
    "topological_sort": [{"nodes": ["a", "b"], "edges": [{"from": "a", "to": "b"}]}],
    "plan_dependency_order": [{"modules": ["m"]}],
    "plan_file_dependency_order": [{"modules": ["m"]}],
    "plan_function_dependency_order": [{"modules": ["m"]}],
    "listup_symbols_matching_file_path": [{"modules": ["m"]}],
    "listup_class_matching_path": [{"class_names": ["C"]}],
    "list_up_entrypoint": [{"modules": ["m"]}],
    "trace_flow": [{"start_id": "nonexistent"}],
    "trace_flow_between_module": [{"source_modules": ["a"], "target_modules": ["b"]}],
    "find_path_between_module": [{"source_modules": ["a"], "target_modules": ["b"]}],
    "find_paths": [],
    "reconstruct_flow": [],
    "annotate_node": [{"node_id": "nonexistent"}],
    "get_api_call_chain": [{}],
    "semantic_graph_expansion": [{"query": "parse config"}],
    # mind server
    "list_source_ids": [{"limit": 3}],
    "query_graph_rag_langextract": [{"query": "security baseline"}],
}

SKIP_NO_ARGS = {"find_paths", "reconstruct_flow"}  # called with curated args only


def classify(result: dict) -> str:
    if result.get("is_error"):
        structured = result.get("structured_content") or {}
        error = structured.get("error") if isinstance(structured, dict) else None
        if isinstance(error, dict) and error.get("code"):
            return "EXPECTED_ERROR"
        return "FAIL:non-contract-error"
    structured = result.get("structured_content")
    if structured is None:
        return "OK:no-structured" if result.get("content_text") else "FAIL:empty"
    return "OK"


def smoke(server: str, port: int, tools: list[dict], project_hint: str) -> list[dict]:
    rows = []
    for tool in tools:
        name = tool.get("name", "")
        arg_sets = TOOL_ARGS.get(name)
        if arg_sets is None:
            if name in SKIP_NO_ARGS:
                rows.append({"server": server, "tool": name, "status": "SKIP"})
                continue
            arg_sets = [{}]
        worst = "OK"
        detail = ""
        for args in arg_sets:
            try:
                result = _call_with_timeout(port, name, args, timeout_s=180.0)
            except Exception as exc:  # noqa: BLE001
                worst = "FAIL:transport"
                detail = repr(exc)[:200]
                break
            status = classify(result)
            if status.startswith("FAIL"):
                worst = status
                detail = json.dumps(result.get("structured_content"), ensure_ascii=False)[:200]
                break
            if status == "EXPECTED_ERROR" and worst == "OK":
                worst = "EXPECTED_ERROR"
                detail = json.dumps(
                    (result.get("structured_content") or {}).get("error", {}),
                    ensure_ascii=False,
                )[:160]
        rows.append(
            {"server": server, "tool": name, "status": worst, "detail": detail}
        )
        marker = "ok" if worst in ("OK", "EXPECTED_ERROR") else "FAIL"
        print(f"[smoke:{server}] {name}: {worst} {detail if marker == 'FAIL' else ''}")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph", type=int, default=8788)
    parser.add_argument("--mind", type=int, default=8789)
    args = parser.parse_args()

    all_rows: list[dict] = []
    for server, port in (("graph", args.graph), ("mind", args.mind)):
        try:
            listing = live_tools_list(port)
        except Exception as exc:  # noqa: BLE001
            print(f"[smoke:{server}] server unreachable on :{port}: {exc!r}")
            all_rows.append({"server": server, "tool": "<server>", "status": "FAIL:unreachable", "detail": ""})
            continue
        tools = listing.get("tools", [])
        print(f"[smoke:{server}] {len(tools)} tools on :{port}")
        all_rows.extend(smoke(server, port, tools, "cortext"))

    ok = sum(1 for row in all_rows if row["status"] in ("OK", "EXPECTED_ERROR", "SKIP"))
    fails = [row for row in all_rows if row["status"].startswith("FAIL")]
    print()
    print(f"SMOKE TOTAL: {len(all_rows)} tools — {ok} ok, {len(fails)} fail")
    for row in fails:
        print(f"  FAIL {row['server']}.{row['tool']}: {row['status']} {row.get('detail','')}")
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
