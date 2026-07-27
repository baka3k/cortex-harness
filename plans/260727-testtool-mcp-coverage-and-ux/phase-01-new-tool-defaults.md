# Phase 01: New Tool Defaults & Categorization

## Context

`tool_defaults.py` covers 22 tools but the server registers 40. Nineteen newer
tools have no default payload, forcing testers to write JSON by hand. The same
file is the natural home for a category map the new UX needs.

## Requirements

- Add `TOOL_DEFAULTS` entries for all 19 new tools, using input shapes from
  `mcp/tool_metadata.py::_FULL_CATALOG` and signatures in `unified_mcp.py`.
- Add a `TOOL_CATEGORIES: Dict[str, List[str]]` mapping each category to its
  tool names. Every tool in `TOOL_DEFAULTS` must appear in exactly one category.
- Provide an `Other` bucket in rendering so tools missing from the map still
  display.
- Keep `get_default(tool_name)` priority unchanged: `input/{tool}.json` file →
  `TOOL_DEFAULTS` → `{}`.
- Use placeholder tokens (`"YOUR_PROJECT_ID"`, `"YOUR_NODE_ID"`) for
  project/node-specific fields, matching the existing convention for
  `get_symbol`/`query_subgraph`.

## New tools to add (19)

| Category | Tool |
|---|---|
| Session & Discovery | `inspect_parser_capabilities` |
| Planning & Dependency | `compute_scc`, `topological_sort`, `plan_dependency_order`, `plan_file_dependency_order`, `plan_function_dependency_order` |
| Fullstack & Workflow | `find_screen_workflows`, `reconstruct_flow`, `find_callers_of_endpoint`, `get_api_call_chain`, `analyze_workflow_impact`, `find_workflows_containing` |
| Search | `explore_graph` |
| Project Context | `get_project_modules`, `get_public_apis`, `get_endpoints`, `get_module_architecture_summary`, `get_project_special_files`, `get_framework_context` |

## Proposed categories (full assignment)

```
Session & Discovery:
  activate_project, list_databases, list_parsers,
  inspect_parser_capabilities, list_mcp_functions, list_qdrant_collections

Search:
  search_functions, search_by_code, semantic_search, explore_graph,
  get_symbol, get_node_details

Graph Traversal:
  query_subgraph, find_paths, find_path_between_module, trace_flow,
  trace_flow_between_module, list_possible_calls, list_up_entrypoint,
  listup_symbols_matching_file_path, listup_class_matching_path

Planning & Dependency:
  compute_scc, topological_sort, plan_dependency_order,
  plan_file_dependency_order, plan_function_dependency_order

Project Context:
  get_project_modules, get_public_apis, get_endpoints,
  get_module_architecture_summary, get_project_special_files,
  get_framework_context

Fullstack & Workflow:
  find_callers_of_endpoint, get_api_call_chain, analyze_workflow_impact,
  find_workflows_containing, find_screen_workflows, reconstruct_flow,
  get_ipc_message

Annotation:
  annotate_node
```

`get_id_by_name` is intentionally absent (not a real tool — removed in Phase 04).

## Default payload sketches (key fields only)

- `inspect_parser_capabilities`: `{"parser_type": "cplus", "db": ""}`
- `compute_scc` / `topological_sort`: `{"nodes": ["A","B"], "edges": [{"from":"A","to":"B"}], "edge_semantics": "depends_on"}` (+ `output_mode: "both"`, `on_cycle: "auto_condense_scc"` for topo sort)
- `plan_dependency_order`: `{"modules": ["src/auth"], "db": "neo4j", "on_cycle": "auto_condense_scc"}`
- `plan_file_dependency_order` / `plan_function_dependency_order`: same plus `include_cross_module: true`
- `find_screen_workflows`: `{"project_id": "YOUR_PROJECT_ID", "node_a": "HomeScreen", "node_b": "DetailScreen", "max_hops": 8, "max_paths": 100}`
- `explore_graph`: `{"query": "user login authentication flow", "mode": "hybrid", "top_k": 10}`
- `reconstruct_flow`: JSON-string fields per spec — `entry_context_json` and `paths_json` with the example from `tool_metadata.py`
- `get_project_modules` / `get_public_apis` / `get_endpoints` / `get_module_architecture_summary` / `get_project_special_files` / `get_framework_context`: `{"project_id": "YOUR_PROJECT_ID", "limit": 50}` (+ tool-specific filters where documented)
- `find_callers_of_endpoint`: `{"endpoint_path": "/api/users/:id", "http_method": "GET"}`
- `get_api_call_chain`: `{"component_name": "UserProfileScreen", "max_depth": 5}`
- `analyze_workflow_impact`: `{"function_id": "YOUR_NODE_ID", "direction": "downstream", "max_depth": 4}`
- `find_workflows_containing`: `{"function_id": "YOUR_NODE_ID", "include_indirect": true}`

Exact field names verified against `unified_mcp.py` signatures in this phase.

## Related Files

- `code-tiny/testtool/tool_defaults.py` (edit)
- `code-tiny/mcp/tool_metadata.py` (read-only source of truth)
- `code-tiny/mcp/unified_mcp.py` (read-only source of truth)

## Todo

- [ ] Add 19 `TOOL_DEFAULTS` entries.
- [ ] Add `TOOL_CATEGORIES` map covering all real tools.
- [ ] Add `categories()` (or `category_of(name)`) helper if needed by Phase 03.
- [ ] Verify every `TOOL_DEFAULTS` key appears in exactly one category.

## Risks

- If a tool signature changed since `tool_metadata.py` was written, a default
  could carry a stale field. Mitigation: cross-check each signature in
  `unified_mcp.py` while writing the entry; the MCP server ignores unknown
  kwargs in most dispatch paths anyway.

## Success Criteria

- `python -c "from testtool.tool_defaults import TOOL_DEFAULTS, TOOL_CATEGORIES; ..."`
  reports 40 defaults and every default is categorized.
- No default raises on import.
