# Phase 01 - Lifecycle Targets

status: completed

## Tasks

- Add a root Makefile with build/start/stop targets.
- Add a PowerShell lifecycle script to keep Windows terminal spawning and PID cleanup maintainable.
- Use existing MCP shell scripts as the default runtime entrypoints.
- Validate target wiring without starting long-running MCP servers.

## Notes

The default MCP set is intentionally small and aligned with existing repo scripts:

- `C:\ai\cortex-harness\code-tiny\mcp.sh`
- `C:\ai\cortex-harness\doc-tiny\mcp.sh`

Additional mode-specific MCP servers can be added later by extending the script's server table.
