# Phase 01 — Expose parser_type on the 8 unscoped tools

**Goal:** every `_FANOUT_SEARCH_TOOLS` tool accepts an optional `parser_type`
in the public JSON schema and the served catalog, so clients can scope calls
and bypass fan-out entirely.

## Why 8, not 13

Already exposing `parser_type` (signature + `_merge_payload`):
`query_subgraph` (`cplus_mcp.py:1852`), `find_paths` (1958),
`find_path_between_module` (2054), `trace_flow` (2359),
`trace_flow_between_module` (2505) — mirrors in `android_mcp.py:1766, 1874,
1950, 2398, 2531`.

Missing (this phase):

| Tool | cplus def | android def |
|---|---|---|
| get_symbol | 1696 | 1618 |
| list_possible_calls | 1743 | 1665 |
| get_node_details | 1803 | 1724 |
| listup_symbols_matching_file_path | 2169 | 2210 |
| listup_class_matching_path | 2232 | 2271 |
| list_up_entrypoint | 2294 | 2333 |
| search_functions | 2648 | 2663 |
| search_by_code | 2786 | 2737 |

## Steps

1. For each of the 8 tools in **both** backends, copy the
   `tool_query_subgraph` pattern:
   - add `parser_type: Optional[str] = None` to the signature;
   - add `"parser_type": parser_type` to the `_merge_payload(...)` dict so
     the payload path (used by `_fanout_dispatch` and unified routing)
     receives it.
2. Do NOT add validation logic — normalization stays in
   `unified_mcp._normalize_parser_type` / `_apply_unified_defaults`; the
   backend merely forwards.
3. `code-tiny/mcp/tool_metadata.py` (hand-maintained catalog, served by
   `tool_list_mcp_functions`): add to each of the 13 fan-out tool entries:
   `{"name": "parser_type", "type": "str", "required": False, "description":
   "Parser alias to scope this call (e.g. 'python', 'spring', 'android').
   Omit to fan out across query engines (results deduplicated by node id).
   See list_parsers."}`
4. Extend `tests/test_unified_mcp_wrapper_signatures.py`
   `test_proxied_tools_accept_result_formatting_options` (line 40) — or add
   a sibling test — asserting the live registered schema of all 13 proxied
   fan-out tools contains a `parser_type` property (schema-drift guard).
5. Add a catalog test: every `_FANOUT_SEARCH_TOOLS` name in
   `build_catalog` output lists `parser_type` in `inputs`.

## Verification

- `pytest tests/test_unified_mcp_wrapper_signatures.py -q` green.
- Manual: start unified MCP, call `list_mcp_functions`, confirm
  `parser_type` input appears for all 13.
- Manual: `search_functions(query='x', parser_type='python')` does NOT
  produce `query_engine=graph_fanout` (fan-out skipped — mirrors existing
  test at `tests/test_unified_mcp_input_coercion.py:337`).

## Out of scope

- `fastmcp_server.py` standalone defs (`--mode fast` deployment) — backlog
  note in Phase 04.
- `java_mcp.py` legacy standalone — not part of unified dispatch.
