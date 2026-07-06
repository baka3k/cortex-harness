# Make MCP Lifecycle Plan

status: completed
created: 2026-07-06
mode: hi-plan --full
scope: Makefile, scripts/mcp-lifecycle.ps1

## Objective

Add `make build`, `make start`, and `make stop` commands for local development:

- `make build` creates or reuses the root virtual environment and syncs project dependencies.
- `make start` starts MCP servers in separate terminal windows so logs remain visible.
- `make stop` stops the MCP terminal/processes started by `make start`.

## Existing Context

- The repo does not currently have a root `Makefile`.
- The root project uses Python packaging through `pyproject.toml` and dependencies in `requirements.txt`.
- `code-tiny/mcp.sh` starts the unified code MCP on `127.0.0.1:8788/mcp`.
- `doc-tiny/mcp.sh` starts the document GraphRAG MCP on `127.0.0.1:8789/mcp`.
- The existing Neo4j to FalkorDB migration plan is unrelated to this lifecycle work and should not be modified.

## Scope Challenge

1. Should `make start` run one unified MCP or every backend MCP mode separately?
   - Selected: run the existing script entrypoints by default: `code-tiny/mcp.sh` and `doc-tiny/mcp.sh`.
   - Reason: `code-tiny/mcp.sh` already starts `unified_mcp.py`, which loads the code backends behind one endpoint.

2. Should terminal windows remain open for log inspection?
   - Selected: yes.
   - Reason: the requested workflow explicitly wants separate terminals to inspect MCP logs.

3. Should lifecycle state live in the repo?
   - Selected: yes, under `.cache/mcp`.
   - Reason: PIDs and logs are runtime state and already fit the repo's cache directory pattern.

## Implementation

1. Add root `Makefile`.
   - `build` delegates to `scripts/mcp-lifecycle.ps1 build`.
   - `start` delegates to `scripts/mcp-lifecycle.ps1 start`.
   - `stop` delegates to `scripts/mcp-lifecycle.ps1 stop`.
   - `help` documents the targets.

2. Add `scripts/mcp-lifecycle.ps1`.
   - Resolve the repo root from the script path.
   - Build:
     - Create `.venv` if missing.
     - Upgrade `pip`.
     - Install root `requirements.txt`.
     - Install the root package editable.
     - Install `code-tiny/requirements.txt` and `doc-tiny/requirements.txt`.
   - Start:
     - Stop stale process records if needed.
     - Open one terminal per MCP script.
     - Run each script through Git Bash or WSL bash when available.
     - Save terminal process IDs in `.cache/mcp/pids.json`.
   - Stop:
     - Stop saved terminal PIDs.
     - Fallback to stopping process command lines containing the known MCP script paths.

3. Validate:
   - `make -n build`
   - `make -n start`
   - PowerShell parse check for `scripts/mcp-lifecycle.ps1`
   - Run `scripts/mcp-lifecycle.ps1 stop` safely when no processes are registered.

## Acceptance Criteria

- `make build`, `make start`, and `make stop` exist at the repo root.
- `make start` opens separate terminals for `code-tiny/mcp.sh` and `doc-tiny/mcp.sh`.
- `make stop` can stop processes started by `make start`.
- The implementation does not modify unrelated migration files.

## Validation

- `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\mcp-lifecycle.ps1 help`
- `make -n build`
- `make -n start`
- `make -n stop`
- `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\mcp-lifecycle.ps1 stop`
