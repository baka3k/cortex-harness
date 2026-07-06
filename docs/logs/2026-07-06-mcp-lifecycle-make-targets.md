# MCP Lifecycle Make Targets - 2026-07-06

## Context
The `plans/make-mcp-lifecycle/plan.md` plan requested root `make build`, `make start`, and `make stop` commands for local MCP development.

## Change
Added root lifecycle make targets in `Makefile:1`, backed by `scripts/mcp-lifecycle.ps1:1`. The lifecycle script defines the default `code-tiny` and `doc-tiny` MCP endpoints in `scripts/mcp-lifecycle.ps1:15`, builds a shared root virtual environment in `scripts/mcp-lifecycle.ps1:64`, launches separate visible terminal windows in `scripts/mcp-lifecycle.ps1:282`, and stops saved or marker-matched MCP processes in `scripts/mcp-lifecycle.ps1:173`. The shell entrypoints now tolerate Windows virtualenv activation and optional `.env` loading at `code-tiny/mcp.sh:11` and `doc-tiny/mcp.sh:4`.

## Impact
Impact level: low. Local developers can now use `make build`, `make start`, and `make stop` from the repo root while keeping MCP server logs visible in separate terminals. The stop path is bounded to saved terminal start times and known in-repo MCP command markers to reduce accidental process termination risk.

## Decision
The implementation uses a PowerShell lifecycle script rather than placing Windows process orchestration directly in the Makefile, keeping make targets small and preserving room to add more MCP server entries later. The build path syncs all dependency files into the root `.venv` to match the plan and avoid per-service environment drift.

## References
- plan: ./plans/make-mcp-lifecycle/plan.md
- commit: c433ec7331c21e4b32c047bc49de391a7448567f
