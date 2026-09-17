# Baseline: callers of cross-instance MCP surfaces

Captured 2026-08-28 as part of Phase 01 of
`plans/260828-1428-instance-isolated-mcp-locks/plan.md`.

This report is the contract baseline: every call site listed here is
covered by Phases 02 (lease scoping) and 03 (lifecycle scoping). New
call sites added after this date must follow the same contracts.

## `discover_falkordb_data_files()` call sites

All current call sites pass zero positional arguments — they all rely on
the default behavior, which (after Phase 02) is "self only".

| Caller | File:line | Notes |
|---|---|---|
| cplus MCP | `code-tiny/mcp/cplus/cplus_mcp.py:234` | Boot path. Phase 02 keeps the no-arg call. |
| java MCP | `code-tiny/mcp/java/java_mcp.py:200` | Boot path. |
| android MCP | `code-tiny/mcp/android/android_mcp.py:213` | Boot path. |
| unified MCP | `code-tiny/mcp/fastmcp_server.py:219` | Boot path. |
| explore service | `code-tiny/mcp/services/explore_service.py:265` | Allowed on cross-instance allowlist (`explore_graph`). Phase 02 routes through `cross_instance.sibling_paths_if_allowed("explore_graph")`. |
| impact service | `code-tiny/mcp/services/impact_service.py:55` | Allowed on cross-instance allowlist (`analyze_workflow_impact`). Phase 02 routes through `cross_instance.sibling_paths_if_allowed("analyze_workflow_impact")`. |
| tests | `code-tiny/tests/mcp/cplus/test_cplus_mcp.py` | Updated to assert the new default + legacy escape hatch. |
| cross_instance helper | `code-tiny/mcp/cross_instance.py:69` | New helper for opt-in callers. |

## `_mcp_stop_pattern` / `_mcp_pids` / `_pause_mcp_for_sync` call sites

`cortex_harness/dev.py` is the only consumer of these helpers.

| Caller | File:line | Notes |
|---|---|---|
| `_mcp_stop_pattern` | `cortex_harness/dev.py:1818` (def), `:1819, :1833` (internal), `:1953` (pause), `:3537` (legacy pause block) | Phase 03 adds `instance_id` kwarg. |
| `_mcp_pids` | `cortex_harness/dev.py:1764` (def), `:1819, :1833, :1943, :1958, :3526` (callers) | Phase 03 adds `instance_id` kwarg; `instance_id=None` preserves legacy behavior. |
| `_pause_mcp_for_sync` | `cortex_harness/dev.py:1915` (def), `:2008, :2127, :2146` (callers) | Phase 03 resolves the instance id and forwards it. |

## Rollout env flags

| Flag | Default | Effect |
|---|---|---|
| `CORTEX_MCP_SCOPE_LEASES` | unset (new behavior on) | When set to `"0"`, `discover_falkordb_data_files` returns every sibling regardless of other args. |
| `CORTEX_MCP_PAUSE_BY_INSTANCE` | unset (new behavior on) | When set to `"0"`, `_mcp_pids` skips the instance-id filter. |
| `CROSS_INSTANCE_QUERY` | unset (gate closed) | Per-process opt-in for cross-instance reads via `code-tiny/mcp/cross_instance.py`. |

## Backward-compatibility check

| Existing test | Status | Action |
|---|---|---|
| `test_discovery_honors_relocated_data_home` | Asserted the legacy "every sibling" default. | Updated to assert the new self-only default plus two extra cases (siblings-with-self, legacy escape hatch). |
| `test_graph_driver_receives_all_discovered_instance_files` | Patches `discover_falkordb_data_files` directly. | Stays valid (no change needed) — the test exercises the call site contract, not the default. |
