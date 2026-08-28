# Phase 02 — `discover_falkordb_data_files` filtering and per-tool opt-in

## Context

The MCP boot paths in `cplus_mcp.py`, `java_mcp.py`, `android_mcp.py`,
`fastmcp_server.py`, `impact_service.py`, and `explore_service.py` all call
`discover_falkordb_data_files()` with no arguments, which today returns
every sibling's `data.rdb`. With the contract from Phase 01, those call
sites should keep the same line of code but get a filtered result.

## Goals

- Default MCP boot path owns only its own instance.
- A small allowlist of tools can opt back into cross-instance reads behind
  `CROSS_INSTANCE_QUERY=1`.
- `additional_paths` reaching `FalkorDBDriver` is empty by default and
  non-empty only when the gate is on *and* the tool is allowlisted.
- No regression in the legacy "discover every sibling" mode behind the
  `CORTEX_MCP_SCOPE_LEASES=0` flag.

## Related files

- `code-tiny/mcp/falkordb_discovery.py:22` (filter implementation).
- `code-tiny/mcp/cross_instance.py` (Phase 01 module).
- `code-tiny/mcp/cplus/cplus_mcp.py:211`,
  `code-tiny/mcp/java/java_mcp.py`,
  `code-tiny/mcp/android/android_mcp.py`,
  `code-tiny/mcp/fastmcp_server.py`,
  `code-tiny/mcp/services/impact_service.py`,
  `code-tiny/mcp/services/explore_service.py`.
- `tests/test_falkordb_driver_local.py`,
  `code-tiny/tests/mcp/cplus/test_cplus_mcp.py`.

## Implementation steps

1. Implement the filter in `discover_falkordb_data_files`:
   - Resolve `instances_root` and `current_instance_id` (default from
     `CORTEX_STORAGE_INSTANCE`).
   - When `include_siblings=False` and `exclude_self=True`, return the
     single primary file `<instances_root>/<current_instance_id>/falkordb/code/data.rdb`
     if it exists, else `[]`.
   - When `include_siblings=True`, return every sibling's file in the
     original sort order. Apply `exclude_self` to drop self from the list.
2. Replace the call sites: each MCP backend's `_get_graph_driver` calls
   `discover_falkordb_data_files()` with the new defaults. The semantics
   are now "self only" instead of "all siblings". The four-arg explicit
   form is documented but not yet used by any caller.
3. Wire the cross-instance gate at the *call site*: any tool that wants
   to query siblings calls a thin helper
   `_sibling_paths_if_allowed(tool_name)` which returns the result of
   `discover_falkordb_data_files(include_siblings=True, exclude_self=True)`
   if `cross_instance.is_allowed(tool_name)`, else `[]`. The Phase 01
   allowlist starts empty; Phase 02 fills in the small set of tools that
   need it (`impact_service.analyze_workflow_impact`,
   `explore_service.explore_graph` global scope, …) — keep the list
   short and reviewable.
4. Honor `CORTEX_MCP_SCOPE_LEASES=0`:
   - When set to `0`, `discover_falkordb_data_files()` returns the legacy
     "every sibling" list regardless of other args. This is the rollback
     path.
   - Logged once at MCP boot so the legacy mode is visible.
5. Tests:
   - `tests/test_falkordb_discovery_filter.py` (new): prove the filter
     excludes self by default, includes siblings when asked, and respects
     `CORTEX_MCP_SCOPE_LEASES=0`.
   - Update `code-tiny/tests/mcp/cplus/test_cplus_mcp.py` "discovery honors
     relocated data home" test to also assert that with the default call
     the returned list contains exactly one element when the current
     instance is one of two siblings.
   - Update `tests/test_falkordb_driver_local.py` to keep the existing
     `additional_paths=[sibling_path]` direct-driver tests working and add
     a negative test where the MCP boot path passes an empty list by
     default.

## Risks

- A tool that today relies on cross-instance reads without an allowlist
  will silently return empty. Mitigation: surface the gate value in
  `dev status`; log a warning at boot when the gate is off and a sibling
  was previously in use; document the rollout in the changelog.
- The legacy `include_siblings=True, exclude_self=False` call still
  exists for debugging tools; make sure they pass the args explicitly so
  the default does not silently change behavior for them.

## Success criteria

- All six MCP backends boot with `additional_paths=[]` (or a single
  self-path) by default.
- The cross-instance allowlist is small, documented, and covered by tests.
- The legacy behavior is reachable via
  `CORTEX_MCP_SCOPE_LEASES=0` and is exercised by a single regression
  test.
