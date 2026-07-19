# Windows MCP Force-Restart Reliability — 2026-07-19

## Context

`dev mcp start --force-restart` depended on POSIX `pgrep`/`kill` behavior and printed Unicode headings that could fail on a Windows `cp1252` console. The result could be an apparently restarted command while an old MCP process still owned the port, undermining live verification in the [runtime-alignment plan](../../plans/260719-2150-parser-mcp-runtime-alignment/plan.md).

## Change

- Windows process discovery now queries Python command lines through PowerShell/CIM, while POSIX retains the existing `pgrep` path (`cortex_harness/dev.py:1338`).
- Windows shutdown uses `taskkill /T /F` for each discovered process tree and polls for termination for up to five seconds before replacement startup proceeds (`cortex_harness/dev.py:1375`).
- MCP service headings are ASCII-safe, and focused tests verify Windows PID discovery and process-tree termination arguments (`cortex_harness/dev.py:2339`, `tests/test_dev_lifecycle_commands.py:120`, `tests/test_dev_lifecycle_commands.py:133`).

## Impact

Force-restart can reliably replace stale MCP processes on Windows and avoids console-encoding failures in the service heading. **Risk level: medium** because forced process-tree termination is intentionally stronger than graceful shutdown, but it is restricted to command lines matching the configured MCP service pattern and only runs when force-restart is requested.

## Decision

Use platform-native process discovery and termination behind the existing lifecycle helpers instead of emulating POSIX utilities on Windows. Preserve the POSIX implementation unchanged, bound the Windows wait, and prefer ASCII for this operational output so restart behavior is deterministic across common Windows consoles.

## References

- Plan: [plans/260719-2150-parser-mcp-runtime-alignment/plan.md](../../plans/260719-2150-parser-mcp-runtime-alignment/plan.md)
- Lifecycle implementation: `cortex_harness/dev.py:1338`, `cortex_harness/dev.py:1375`
- Regression tests: `tests/test_dev_lifecycle_commands.py:120`, `tests/test_dev_lifecycle_commands.py:133`
- Base commit: `d9623f30811fafe2fe3e9bc47a0bba0640a5af09`
