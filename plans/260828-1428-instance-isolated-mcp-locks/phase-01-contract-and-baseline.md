# Phase 01 — Lease scoping contract, default flags, and baseline

## Context

Per-instance isolation depends on three small contracts that need to be
agreed up front: (1) the discovery filter signature, (2) the cross-instance
allowlist shape, and (3) the dev-lifecycle filter signature. Without these
contracts Phases 02 and 03 cannot be implemented without further re-work.

## Goals

- Define `discover_falkordb_data_files(include_siblings, exclude_self,
  data_home, current_instance)` with explicit defaults.
- Define `code-tiny/mcp/cross_instance.is_allowed(tool_name) -> bool` and the
  `CROSS_INSTANCE_QUERY` env contract.
- Define `_mcp_pids(pattern, *, instance_id=None)` and the pid-file sidecar
  format used by `_mcp_start_one`.
- Document the two rollout env flags:
  `CORTEX_MCP_SCOPE_LEASES` (default off until rollout) and
  `CORTEX_MCP_PAUSE_BY_INSTANCE` (default off until rollout).
- Capture the baseline: list every existing caller of
  `discover_falkordb_data_files` and `_mcp_stop_pattern`, and the test
  fixtures that exercise them.

## Related files

- `code-tiny/mcp/falkordb_discovery.py:22` (signature change).
- `code-tiny/mcp/cross_instance.py` (new).
- `cortex_harness/dev.py:1818` `_mcp_stop_pattern`,
  `cortex_harness/dev.py:1764` `_mcp_pids`,
  `cortex_harness/dev.py:1862` `_mcp_start_one`,
  `cortex_harness/dev.py:1915` `_pause_mcp_for_sync`.
- `docs/development-rules.md`, `docs/UNIFIED_INGEST_QUERY_CONTRACT.md`.

## Implementation steps

1. Write the new function signature for `discover_falkordb_data_files`
   with three keyword args:
   - `include_siblings: bool = False`
   - `exclude_self: bool = True`
   - `current_instance: Optional[str] = None` (default reads
     `CORTEX_STORAGE_INSTANCE`).
   Keep the existing positional behavior — `()` returns nothing — so
   callers that want the legacy "every sibling" can pass
   `include_siblings=True, exclude_self=False`. No caller change here.
2. Add `code-tiny/mcp/cross_instance.py` with:
   - `ALLOWLIST = frozenset({...})` (initial set: empty; populated in
     Phase 02 with the small list of tools that genuinely need it).
   - `def is_allowed(tool_name: str) -> bool` returning
     `os.environ.get("CROSS_INSTANCE_QUERY") == "1" and tool_name in ALLOWLIST`.
   - `def enabled() -> bool` for callers that want a single switch.
3. Add `_mcp_pids(pattern, *, instance_id=None)` — accept the new kwarg
   without changing the legacy default behavior. Add a helper
   `_pid_instance_id(pid) -> Optional[str]` that reads the pid-file sidecar
   first (new format) and falls back to `ps eww` / `/proc/<pid>/environ` /
   Win32 env block.
4. Update `_mcp_start_one` to write the pid-file sidecar in the new format:
   `dev-mcp-{name}-{instance_id}.pid` containing
   `<pid>\n<instance_id>\n`. Keep the legacy `dev-mcp-{name}.pid` file as a
   compatibility pointer.
5. Document the rollout flags in `docs/development-rules.md` and add a
   paragraph on `CROSS_INSTANCE_QUERY` to
   `docs/UNIFIED_INGEST_QUERY_CONTRACT.md`.
6. Run `git grep -nE "discover_falkordb_data_files\(|_mcp_stop_pattern\(|_mcp_pids\("` and capture the output as
   `plans/260828-1428-instance-isolated-mcp-locks/reports/baseline-callers.md`.

## Risks

- The pid-file sidecar rename could break an external operator script that
  reads `dev-mcp-{name}.pid` directly. Mitigation: keep the legacy file as
  a symlink/hardlink or as a redirect file pointing to the new sidecar.
- Adding kwargs to `discover_falkordb_data_files` is forward-compatible but
  callers with explicit positional args break. Mitigation: all current
  callers use zero args; baseline report proves this.

## Success criteria

- The new signatures exist with backward-compatible defaults.
- The baseline caller report lists every current call site and its argument
  shape.
- `dev doctor` prints both rollout flags as `off` (legacy).
