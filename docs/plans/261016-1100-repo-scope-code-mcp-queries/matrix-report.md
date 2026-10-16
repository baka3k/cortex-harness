# Real-MCP repo-scope matrix report (live servers)

**Kết quả: 98/98 checks PASS trên unified + 6/6 trên fastmcp standalone.**

- Server thật: `unified_mcp.py` (streamable-http :8799) + `fastmcp_server.py` standalone (:8798),
  chạy trên store FalkorDBLite riêng được seed bởi `seed_matrix_store.py` (22 nodes: Function/File/Class/CALLS/DECLARES/Message/Workflow
  phủ cả 2 repo flavor + decoy xs3 + node không repo + cross-repo caller f4→f1).
- Ma trận: toàn bộ 39 tools unified × {no-repo, suffix "s3", exact abspath, repo lạ, repo-trên-tool-excluded} + 3 workflow tools standalone.
- Script: [`real_mcp_repo_matrix.py`](./real_mcp_repo_matrix.py) (unified) + inline raw JSON-RPC (standalone).

## Bug thật mà matrix bắt được (đã fix)

**Payload-dispatch bug (CRITICAL, chỉ có trên server thật):** unified dispatch gọi backend
`fn(payload=merged)` — kwarg `repo` typed KHÔNG bao giờ được bind (mặc định None) ở các tool
kiểu payload-merge (cplus/android). Params Cypher nhận `repo=None→''` = no-op, trong khi
scope block đọc từ payload → `filter_applied: true` nhưng query KHÔNG lọc. Unit tests không
bắt được vì chúng gọi tool bằng kwargs trực tiếp. Fix: params dicts đọc `repo_value`
(derive từ payload) — 15 hàm cplus + 14 hàm android. Regression tests mới
(`CPlusPayloadDispatchRegressionTest` / `AndroidPayloadDispatchRegressionTest`) gọi tool
đúng kiểu dispatch thật (payload-only).

## Phát hiện pre-existing (ngoài scope plan, cần follow-up)

`WITH [t IN $param | toLower(t)] AS x` + `any(… IN x …)` trả **0 rows trên cả FalkorDBLite
và LadybugDB** (verify cả với literal list) → `find_path_between_module`, `trace_flow_between_module`
(cplus/android/java + `_find_module_paths_directed`) trả rỗng trên embedded stores BẤK KỂ repo
(repro được không cần repo → không do plan này). ~20 template sites dùng pattern này.
Matrix assert baseline-consistency (scoped == unscoped, scope block đúng) cho 2 tool đó.

## Ghi chú hành vi (đều là design, không phải bug)

- Excluded tools (annotate_node, list_databases, list_qdrant_collections, find_screen_workflows):
  `repo` không được advertise (gate) → transport drop argument, tool chạy bình thường.
- `reason`/`direction`/`rel_types` là per-parser diagnostics — fan-out merge không promote lên
  top-level; merged response báo kết quả qua `nodes` + `warnings`.
- Unscoped `search_functions("handler")` trả 67 results từ các store thật trên máy
  (fan-out cross-store là hành vi R1 đúng) → response bị truncate bởi middleware; matrix dùng
  unique term để tránh truncate.
- `semantic_search`/`explore_graph` scoped: pre_filter_hits=40/20 (over-fetch ×4), post 0 hits
  trên store matrix (không có vector index cho repo test data) + warning đầy đủ.

---

# Real-MCP repo-scope matrix report

- Server: http://127.0.0.1:8799/mcp (unified, streamable-http, fresh falkordblite store seeded by seed_matrix_store.py)
- Date: 2026-10-16 18:11:10
- Result: **98/98 checks passed** in 1.5s

| Tool | Variant | OK | Detail |
|---|---|---|---|
| inventory | search_functions:has-repo | ✅ | props=['content_mode', 'framework', 'include_raw_fields', 'kinds', 'limit', 'parser_type']… |
| inventory | search_by_code:has-repo | ✅ | props=['content_mode', 'include_raw_fields', 'limit', 'parser_type', 'payload', 'project_id']… |
| inventory | get_symbol:has-repo | ✅ | props=['content_mode', 'include_raw_fields', 'node_id', 'parser_type', 'payload', 'project_id']… |
| inventory | get_node_details:has-repo | ✅ | props=['content_mode', 'include_raw_fields', 'node_ids', 'parser_type', 'payload', 'project_id']… |
| inventory | query_subgraph:has-repo | ✅ | props=['content_mode', 'direction', 'function_id', 'include_fp', 'include_possible', 'include_raw_fields']… |
| inventory | find_paths:has-repo | ✅ | props=['content_mode', 'end_function_id', 'include_fp', 'include_possible', 'include_raw_fields', 'max_depth']… |
| inventory | find_path_between_module:has-repo | ✅ | props=['content_mode', 'direction', 'include_fp', 'include_possible', 'include_raw_fields', 'max_depth']… |
| inventory | listup_symbols_matching_file_path:has-repo | ✅ | props=['content_mode', 'include_raw_fields', 'max_depth', 'module', 'modules', 'node_types']… |
| inventory | listup_class_matching_path:has-repo | ✅ | props=['class_name', 'class_names', 'content_mode', 'include_raw_fields', 'parser_type', 'payload']… |
| inventory | list_up_entrypoint:has-repo | ✅ | props=['content_mode', 'include_raw_fields', 'limit', 'module', 'modules', 'parser_type']… |
| inventory | trace_flow:has-repo | ✅ | props=['content_mode', 'debug', 'direction', 'end_id', 'include_raw_fields', 'limit']… |
| inventory | trace_flow_between_module:has-repo | ✅ | props=['content_mode', 'direction', 'include_raw_fields', 'limit', 'max_depth', 'parser_type']… |
| inventory | list_possible_calls:has-repo | ✅ | props=['content_mode', 'include_raw_fields', 'limit', 'parser_type', 'payload', 'project_id']… |
| inventory | get_ipc_message:has-repo | ✅ | props=['payload', 'project_id', 'receiver', 'receivers', 'repo', 'sender']… |
| inventory | plan_file_dependency_order:has-repo | ✅ | props=['edge_semantics', 'include_cross_module', 'max_files_per_module', 'modules', 'on_cycle', 'parser_type']… |
| inventory | plan_function_dependency_order:has-repo | ✅ | props=['edge_semantics', 'include_cross_module', 'include_lambdas', 'max_functions_per_module', 'modules', 'on_cycle']… |
| inventory | find_callers_of_endpoint:has-repo | ✅ | props=['be_project_id', 'endpoint_path', 'fe_project_id', 'http_method', 'parser_type', 'project_id']… |
| inventory | get_api_call_chain:has-repo | ✅ | props=['be_project_id', 'component_name', 'endpoint_path', 'fe_project_id', 'max_depth', 'parser_type']… |
| inventory | semantic_search:has-repo | ✅ | props=['collection', 'collection_code', 'collection_comment', 'content_mode', 'expand_graph', 'graph_depth']… |
| inventory | explore_graph:has-repo | ✅ | props=['collection', 'debug', 'mode', 'parser_type', 'project_id', 'query']… |
| inventory | annotate_node:no-repo | ✅ | props=['content_mode', 'include_raw_fields', 'node_id', 'note', 'payload', 'project_id']… |
| inventory | list_databases:no-repo | ✅ | props=['payload']… |
| inventory | list_qdrant_collections:no-repo | ✅ | props=['include_vectors', 'payload', 'qdrant_url']… |
| inventory | list_parsers:no-repo | ✅ | props=['detail_level']… |
| inventory | inspect_parser_capabilities:no-repo | ✅ | props=['parser_type', 'project_id']… |
| inventory | list_mcp_functions:no-repo | ✅ | props=[]… |
| inventory | get_project_modules:no-repo | ✅ | props=['include_dependencies', 'limit', 'module_id', 'module_path', 'offset', 'parser_type']… |
| inventory | get_public_apis:no-repo | ✅ | props=['include_inferred', 'language', 'limit', 'module_id', 'offset', 'parser_type']… |
| inventory | get_endpoints:no-repo | ✅ | props=['framework', 'http_method', 'limit', 'module_id', 'offset', 'parser_type']… |
| inventory | get_module_architecture_summary:no-repo | ✅ | props=['all_modules', 'detail_level', 'item_limit', 'module_id', 'parser_type', 'project_id']… |
| inventory | get_project_special_files:no-repo | ✅ | props=['framework', 'include_generated', 'limit', 'module_id', 'offset', 'parse_depth']… |
| inventory | get_framework_context:no-repo | ✅ | props=['dimensions', 'framework', 'limit', 'module_id', 'offset', 'parser_type']… |
| inventory | compute_scc:no-repo | ✅ | props=['edge_semantics', 'edges', 'include_singletons', 'nodes', 'parser_type']… |
| inventory | topological_sort:no-repo | ✅ | props=['edge_semantics', 'edges', 'nodes', 'on_cycle', 'output_mode', 'parser_type']… |
| inventory | plan_dependency_order:no-repo | ✅ | props=['edge_semantics', 'modules', 'on_cycle', 'parser_type', 'project_id']… |
| inventory | reconstruct_flow:no-repo | ✅ | props=['entry_context_json', 'paths_json']… |
| inventory | find_screen_workflows:no-repo | ✅ | props=['direction', 'include_api_calls', 'include_entry_function', 'max_hops', 'max_paths', 'node_a']… |
| inventory | analyze_workflow_impact:no-repo | ✅ | props=['direction', 'function_id', 'max_depth', 'parser_type', 'project_id']… |
| inventory | find_workflows_containing:no-repo | ✅ | props=['function_id', 'include_indirect', 'max_depth', 'parser_type', 'project_id']… |
| search_functions | no-repo/unique-term | ✅ | ['f6'] |
| search_functions | suffix=s3 | ✅ | ['c1', 'f1', 'f2', 'file1', 'file2'] |
| search_functions | suffix=s3/xs3-decoy | ✅ | ids=[] warnings=["repo 's3' matched 0 nodes. Known repo values: ~/AI/cortex-harness, cortex-harness, cortext, cortext/cortex-harness, /Users/de |
| search_functions | unknown | ✅ | ["repo 'zz9x' matched 0 nodes. Known repo values: ~/AI/cortex-harness, cortex-harness, cortext, cortext/cortex-harness, /Users/dev/workspaces/s |
| search_by_code | suffix=s3/empty-graph-area | ✅ | ["repo 's3' matched 0 nodes. Known repo values: ~/AI/cortex-harness, cortex-harness, cortext, cortext/cortex-harness, /Users/dev/workspaces/s1, |
| get_symbol | no-repo | ✅ | f1 |
| get_symbol | repo-mismatch-miss | ✅ | node=None expected |
| get_symbol | exact-match | ✅ | f1 |
| get_node_details | suffix=both-flavors | ✅ | ['f1', 'f3'] |
| query_subgraph | cross-repo-caller-survives | ✅ | nodes=['f4', 'f1', 'file1', 'file4'] |
| find_paths | no-repo/path-found | ✅ | reason=None |
| find_paths | cross-repo-endpoints-filtered | ✅ | nodes=[] warnings=["repo 's3' matched 0 nodes. Known repo values: ~/AI/cortex-harness, cortex-harness, cortext, cortext/cortex-harness, /Users/ |
| find_paths | in-repo-path-found | ✅ | reason=None |
| find_path_between_module | xs3-source-filtered | ✅ | nodes=0 |
| find_path_between_module | baseline-consistency | ✅ | scoped=[] unscoped=[] |
| trace_flow | in-repo | ✅ | reason=None |
| trace_flow | cross-repo-filtered | ✅ | nodes=[] warnings=["repo 's3' matched 0 nodes. Known repo values: ~/AI/cortex-harness, cortex-harness, cortext, cortext/cortex-harness, /Users/ |
| trace_flow | single-anchor | ✅ | nodes=['f1', 'f2', 'f3'] |
| trace_flow_between_module | s-side-only+scope | ✅ | nodes=[] (t=cross-repo allowed by design) |
| list_possible_calls | suffix/empty-graph-area | ✅ | ["repo 's3' matched 0 nodes. Known repo values: ~/AI/cortex-harness, cortex-harness, cortext, cortext/cortex-harness, /Users/dev/workspaces/s1, |
| listup_symbols_matching_file_path | no-repo | ✅ | ['f1', 'f2', 'f3', 'file1', 'file2', 'file3'] |
| listup_symbols_matching_file_path | suffix=s3 | ✅ | ['f1', 'f2', 'f3', 'file1', 'file2', 'file3'] |
| listup_symbols_matching_file_path | exact=abspath | ✅ | ['f1', 'f2', 'file1', 'file2'] |
| listup_symbols_matching_file_path | suffix=xs3-decoy | ✅ | ['f4', 'file4'] |
| listup_class_matching_path | suffix=s3 | ✅ | [{'parser_type': 'android', 'id': 'c1', 'labels': [], 'properties': {'id': 'c1', 'name': 'HandlerClass', 'qualified_name': 'svc::HandlerClass', 'repo': '/Users/ |
| listup_class_matching_path | repo-mismatch-empty | ✅ | ["repo 's1' matched 0 nodes. Known repo values: ~/AI/cortex-harness, cortex-harness, cortext, cortext/cortex-harness, /Users/dev/workspaces/s1, |
| list_up_entrypoint | entrypoint-in-repo | ✅ | functions=['f1'] |
| get_ipc_message | no-repo | ✅ | ['ui_panel', 'bg_worker'] |
| get_ipc_message | suffix=both-flavors | ✅ | ['ui_panel', 'bg_worker'] |
| get_ipc_message | repo-mismatch-empty | ✅ | [] |
| plan_file_dependency_order | caller-side-s3 | ✅ | plans=1 |
| plan_function_dependency_order | caller-side-s3 | ✅ | plans=1 |
| find_callers_of_endpoint | empty+scope | ✅ | ["repo 'zz9x' matched 0 nodes. Known repo values: ~/AI/cortex-harness, cortex-harness, cortext, cortext/cortex-harness, /Users/dev/workspaces/s |
| get_api_call_chain | empty+scope | ✅ | {'repo': 's3', 'filter_applied': True, 'warnings': ["repo 's3' matched 0 nodes. Known repo values: ~/AI/cortex-harness, cortex-harness, cortext |
| annotate_node | repo-not-advertised | ✅ | accepted, arg dropped (schema excludes repo) |
| find_workflows_containing | repo-rejected | ✅ | {"contract": "cortex.mcp.tool-result", "contractVersion": "2.0", "tool": "find_workflows_containing" |
| analyze_workflow_impact | repo-rejected | ✅ | {"contract": "cortex.mcp.tool-result", "contractVersion": "2.0", "tool": "analyze_workflow_impact",  |
| list_databases | repo-not-advertised | ✅ | accepted, arg dropped (schema excludes repo) |
| list_qdrant_collections | repo-not-advertised | ✅ | accepted, arg dropped (schema excludes repo) |
| list_parsers | repo-rejected | ✅ | {"contract": "cortex.mcp.tool-result", "contractVersion": "2.0", "tool": "list_parsers", "message":  |
| inspect_parser_capabilities | repo-rejected | ✅ | {"contract": "cortex.mcp.tool-result", "contractVersion": "2.0", "tool": "inspect_parser_capabilitie |
| get_project_modules | repo-rejected | ✅ | {"contract": "cortex.mcp.tool-result", "contractVersion": "2.0", "tool": "get_project_modules", "mes |
| get_public_apis | repo-rejected | ✅ | {"contract": "cortex.mcp.tool-result", "contractVersion": "2.0", "tool": "get_public_apis", "message |
| get_endpoints | repo-rejected | ✅ | {"contract": "cortex.mcp.tool-result", "contractVersion": "2.0", "tool": "get_endpoints", "message": |
| get_module_architecture_summary | repo-rejected | ✅ | {"contract": "cortex.mcp.tool-result", "contractVersion": "2.0", "tool": "get_module_architecture_su |
| get_project_special_files | repo-rejected | ✅ | {"contract": "cortex.mcp.tool-result", "contractVersion": "2.0", "tool": "get_project_special_files" |
| get_framework_context | repo-rejected | ✅ | {"contract": "cortex.mcp.tool-result", "contractVersion": "2.0", "tool": "get_framework_context", "m |
| compute_scc | repo-rejected | ✅ | {"contract": "cortex.mcp.tool-result", "contractVersion": "2.0", "tool": "compute_scc", "message": " |
| topological_sort | repo-rejected | ✅ | {"contract": "cortex.mcp.tool-result", "contractVersion": "2.0", "tool": "topological_sort", "messag |
| plan_dependency_order | repo-rejected | ✅ | {"contract": "cortex.mcp.tool-result", "contractVersion": "2.0", "tool": "plan_dependency_order", "m |
| reconstruct_flow | repo-rejected | ✅ | {"contract": "cortex.mcp.tool-result", "contractVersion": "2.0", "tool": "reconstruct_flow", "messag |
| find_screen_workflows | repo-not-advertised | ✅ | accepted, arg dropped (schema excludes repo) |
| annotate_node | no-repo-works | ✅ | True |
| compute_scc | no-repo-works | ✅ |  |
| get_project_modules | no-repo-works | ✅ | envelope ok |
| semantic_search | scoped | ✅ | scope={'repo': 's3', 'filter_applied': True, 'pre_filter_hits': 40, 'post_filter_hits': 0, 'warnings': ["repo 's3' matched 0 of 40 vector hits"]} |
| semantic_search | no-repo | ✅ | scope={'repo': None, 'filter_applied': False, 'pre_filter_hits': 10, 'post_filter_hits': 10, 'warnings': []} |
| explore_graph | scoped | ✅ | scope={'repo': 's3', 'filter_applied': True, 'pre_filter_hits': 20, 'post_filter_hits': 0, 'warnings': ["repo 's3' matched 0 of 20 vector hits"]} |
| explore_graph | no-repo | ✅ | scope={'repo': None, 'filter_applied': False, 'pre_filter_hits': 10, 'post_filter_hits': 10, 'warnings': []} |