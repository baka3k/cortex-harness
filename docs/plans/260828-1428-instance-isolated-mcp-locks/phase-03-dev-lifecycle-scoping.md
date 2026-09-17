# Phase 03 — Dev lifecycle pause-by-instance and pid-file sidecar

## Context

`cortex_harness/dev.py` launches MCPs and pauses them during sync. Today
the pause is pattern-based: `_mcp_stop_pattern("unified_mcp.py")` kills
every matching PID regardless of `CORTEX_STORAGE_INSTANCE`. With multiple
MCPs (one per instance) this is too coarse.

## Goals

- `_mcp_pids(pattern, *, instance_id=None)` returns the existing broad
  scan when `instance_id is None` (legacy callers) and an
  instance-filtered list otherwise.
- `_mcp_start_one` writes a pid-file sidecar
  `dev-mcp-{name}-{instance_id}.pid` that records both PID and instance id
  so filtering does not depend on reading process env.
- `_pause_mcp_for_sync` resolves the target instance and forwards it to
  `_mcp_stop_pattern`.
- `CORTEX_MCP_PAUSE_BY_INSTANCE=0` reverts to the legacy pattern-only
  pause.

## Related files

- `cortex_harness/dev.py:1764` `_mcp_pids`,
  `cortex_harness/dev.py:1818` `_mcp_stop_pattern`,
  `cortex_harness/dev.py:1862` `_mcp_start_one`,
  `cortex_harness/dev.py:1915` `_pause_mcp_for_sync`.
- `cortex_harness/dev.py:2602` `_resolve_storage_instance` (source of
  the instance id used to set the env).

## Implementation steps

1. Add `_pid_instance_id(pid: int) -> Optional[str]` that:
   - Reads `<MCP_LOG_DIR>/dev-mcp-{name}-{instance}.pid` lines for a
     matching PID first. (The sidecar is written in Phase 01.)
   - Falls back to inspecting process environment on Linux
     (`/proc/<pid>/environ`), macOS (`ps eww -p <pid>`), and Win32
     (PowerShell `Get-CimInstance Win32_Process | Select CommandLine` —
     pattern match `CORTEX_STORAGE_INSTANCE=([^ ]+)`).
   - Returns `None` when neither source yields the value.
2. Update `_mcp_pids(pattern, *, instance_id=None)`:
   - If `instance_id is None`, return the legacy list.
   - Else, run the legacy scan and filter by
     `(_pid_instance_id(pid) == instance_id)`. Log the dropped count at
     `info` so operators see why fewer PIDs are stopped.
3. Update `_mcp_stop_pattern(pattern, *, instance_id=None)` to forward
   `instance_id` to `_mcp_pids`. Keep the legacy default.
4. Update `_mcp_start_one` to write the pid-file sidecar:
   ```
   <MCP_LOG_DIR>/dev-mcp-{name}-{instance_id}.pid
   pid=<pid>
   instance_id=<instance_id>
   ```
   and a one-line legacy file
   `<MCP_LOG_DIR>/dev-mcp-{name}.pid` containing the same `<pid>` (still
   useful for tools that grep for the legacy filename).
5. Update `_pause_mcp_for_sync` to resolve the target instance id:
   - Add `instance_id: str | None = None` kwarg.
   - If not provided, derive from `process_env["CORTEX_STORAGE_INSTANCE"]`
     (already in `process_env` per Phase 01).
   - Forward the kwarg to `_mcp_stop_pattern(service["pattern"],
     instance_id=instance_id)`.
   - Log the resolved instance id at start and end.
6. Surface the rollout flag in `dev status` and `dev doctor`.
7. Tests:
   - `tests/test_dev_pause_by_instance.py` (new): spawn two mock MCP
     "processes" via pid files with different `instance_id` lines and
     assert `_mcp_stop_pattern("unified_mcp.py", instance_id="A")` only
     touches the A sidecar.
   - Add a small fake-`ps` test on Linux: write fake `/proc/<pid>/environ`
     files (only under a temp root) and exercise the env-read fallback.
   - Honor `CORTEX_MCP_PAUSE_BY_INSTANCE=0`: a regression test that with
     the flag set, `_mcp_stop_pattern` returns the legacy broad list.

## Risks

- Reading process env is platform-fragile. Mitigation: pid-file sidecar
  is the primary source; env read is the fallback; tests cover both.
- A leftover pid file from a previous instance id could lead to a no-op
  kill (we try to kill a stale PID). Mitigation: kill still uses the
  recorded PID; the fallback `ps` lookup confirms the process is alive
  before `kill -TERM`.
- `_mcp_pids` runs `ps`/`powershell` on every pause. Adding env
  inspection can add latency. Mitigation: pid-file first; only consult
  `ps` when the sidecar is missing.

## Success criteria

- `_pause_mcp_for_sync(instance_id="B")` does not kill MCP A's PID.
- `_mcp_pids("unified_mcp.py")` (no instance kwarg) returns the same
  list as before.
- `CORTEX_MCP_PAUSE_BY_INSTANCE=0` reverts to legacy behavior and is
  covered by a regression test.
