# Per-instance MCP isolation — 2026-08-28

## Context
Per the plan `plans/260828-1428-instance-isolated-mcp-locks`, every code MCP backend must lease exactly one `CORTEX_STORAGE_INSTANCE`'s `data.rdb` instead of walking every sibling instance at boot. Previously the boot driver passed an `additional_paths=[]` that still triggered the discovery helper to enumerate all siblings, which meant two instances running side-by-side could read or collide on each other's leases and made the sync pause lifecycle blunt (it killed *every* running code MCP, regardless of which instance owned the target path). This change closes the default surface to a single instance and gates the few tools that legitimately need cross-instance reads behind an explicit opt-in plus an allowlist.

## Change
- code-tiny/mcp/cross_instance.py:1-86 — new module. `enabled()` checks `CROSS_INSTANCE_QUERY=1`; `is_allowed(tool)` combines that gate with a two-entry `ALLOWLIST` (`analyze_workflow_impact`, `explore_graph`); `sibling_paths_if_allowed` and `self_and_allowed_siblings_paths` return `[]` (not an error) when the gate is closed so callers degrade cleanly to self-only behavior.
- code-tiny/mcp/falkordb_discovery.py:1-115 — `discover_falkordb_data_files` now takes `include_siblings` (default `False`), `exclude_self`, `current_instance`, and `data_home`. The default returns just the current instance's `data.rdb`. Legacy escape hatch: `CORTEX_MCP_SCOPE_LEASES=0` restores the "lease every sibling" behavior.
- code-tiny/mcp/cplus.py, code-tiny/mcp/java.py, code-tiny/mcp/android.py, code-tiny/mcp/fastmcp_server.py — boot drivers pass `additional_paths=[]` so the helper returns only the resolved instance; the four paths no longer open sibling `data.rdb` files.
- code-tiny/mcp/services/impact_service.py and explore_service.py — route sibling discovery through `cross_instance.self_and_allowed_siblings_paths(tool_name)`; their `additional_paths` slot is now gate-driven instead of unconditional.
- cortex_harness/dev.py:1760-2110 — MCP process helpers rewritten for per-instance identity. Adds `_resolve_storage_instance`, `_mcp_pid_sidecar_path`, `_mcp_pid_sidecar_recorded_pid`, `_pid_instance_id`, `_read_instance_from_process_env` (POSIX `/proc/<pid>/environ` + `ps eww`; Win32 PowerShell `Get-CimInstance`), `_mcp_pids(pattern, instance_id=…)`, `_mcp_stop_pattern(pattern, instance_id=…)`, and `_legacy_pause_by_instance_disabled`. `_mcp_start_one` writes a per-instance sidecar pid file (`dev-mcp-<name>-<instance>.pid` carrying `pid=` and `instance_id=`). `_pause_mcp_for_sync` forwards `instance_id` so only the MCP process owning the syncing instance is paused. New `dev mcp-gates` command at dev.py:2574-2602 surfaces the active gate values.
- tests/test_mcp_lease_surface.py:1-200 — 7 new tests covering `cross_instance`, the updated `discover_falkordb_data_files` defaults, and the allowlist behavior.
- tests/test_dev_pause_by_instance.py:1-300 — 9 new tests covering the per-instance pause/stop helpers and sidecar pid round-trip.
- code-tiny/tests/mcp/cplus/test_cplus_mcp.py and tests/test_dev_lifecycle_commands.py — updated to assert the new boot contract (`additional_paths=[]`, gated `additional_paths` for services).

## Impact
Operators running a single `CORTEX_STORAGE_INSTANCE` see no behavior change. Operators running multiple instances benefit: each MCP process now leases only its own `data.rdb`, the sync pause lifecycle no longer kills siblings of the syncing instance, and cross-instance reads require an explicit `CROSS_INSTANCE_QUERY=1` plus a tool name on the allowlist. Risk: **low** — the legacy escape hatches (`CORTEX_MCP_SCOPE_LEASES=0`, `CORTEX_MCP_PAUSE_BY_INSTANCE=0`) remain wired so a regression can be undone by env var without code changes. New `dev mcp-gates` command makes the rollout state observable. Test surface: 69 related tests pass on `develop`.

## Decision
Two-stack gate (process-level `CROSS_INSTANCE_QUERY=1` AND allowlisted tool name) was chosen over a config-file allowlist so the opt-in is visible from `ps`/`/proc` and survives across restarts without re-editing YAML. A two-entry `ALLOWLIST` (impact analyzer + admin explore) keeps the attack surface for cross-instance reads minimal; both entries are justified in source comments. The `additional_paths=[]` boot contract was preferred over a runtime check because it eliminates the hidden sibling-open path entirely — siblings can only be opened when a service explicitly opts in. Legacy env-var escape hatches are kept (rather than removed) until Phase 04 rollout gates close, at which point the defaults can flip without a code rewrite.

## References
- plan: ./plans/260828-1428-instance-isolated-mcp-locks/plan.md
- commit: 1762772
- related: code-tiny/mcp/cross_instance.py:1-86
- related: code-tiny/mcp/falkordb_discovery.py:1-115
- related: cortex_harness/dev.py:1760-2110
- related: cortex_harness/dev.py:2574-2602 (mcp-gates command)
- related: reports/260828-1428-instance-isolated-mcp-locks/baseline-callers.md
- related: reports/260828-1428-instance-isolated-mcp-locks/rollout-gates.md
