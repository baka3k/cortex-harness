# Project-Scoped MCP Instances — 2026-07-20

## Context

The default `dev start` and `make start` workflow launched both MCP services on fixed ports with shared environment defaults, which prevented developers from keeping separate code-tiny or doc-tiny instances running for multiple projects and graph databases.

## Change

Parameterized starts now accept a server selection, instance name, project, database, collection, provider, host, route, and shared or service-specific ports through the global CLI (`cortex_harness/dev.py:1589`) and Make passthrough variables (`Makefile:31`). The POSIX lifecycle resolves per-service environment overrides, validates names and ports, records instance-specific endpoints, and supports stopping one instance without removing other PID records (`scripts/mcp-lifecycle.py:383`, `scripts/mcp-lifecycle.py:455`, `scripts/mcp-lifecycle.py:476`, `scripts/mcp-lifecycle.py:501`). PowerShell implements the equivalent custom/default split and runtime override precedence on Windows (`scripts/mcp-lifecycle.ps1:856`, `scripts/mcp-lifecycle.ps1:935`, `scripts/mcp-lifecycle.ps1:967`). Both MCP servers can expose instance-specific protocol names while retaining their existing defaults (`code-tiny/mcp/unified_mcp.py:120`, `doc-tiny/mcp_graph_rag.py:16`).

## Impact

Risk level: **medium**. Developers can run code-only, doc-only, or paired MCP instances concurrently with separate ports and default graph/vector targets, then stop one named instance independently. No-argument `dev start`, `make start`, and `dev stop` retain the previous all-services behavior. The main operational risks are port conflicts and accidental routing to an unintended database or collection; validation rejects duplicate or occupied ports, but these environment values select defaults and do not enforce hard per-database access isolation.

## Decision

Extend the existing lifecycle commands with optional parameters instead of adding a growing set of project- or service-specific commands. This keeps the zero-argument workflow unchanged, preserves one cross-platform command surface, and allows service-specific overrides only when the shared project/database defaults are insufficient. Named PID records were chosen so concurrent instances can coexist without broad stop operations affecting unrelated instances.

## References

- CLI option forwarding: `cortex_harness/dev.py:1503`, `cortex_harness/dev.py:1589`
- POSIX instance lifecycle: `scripts/mcp-lifecycle.py:383`, `scripts/mcp-lifecycle.py:476`, `scripts/mcp-lifecycle.py:501`
- Windows lifecycle parity: `scripts/mcp-lifecycle.ps1:856`, `scripts/mcp-lifecycle.ps1:935`, `scripts/mcp-lifecycle.ps1:967`
- Make passthrough and usage: `Makefile:31`, `ReadMe.md:81`
- Regression coverage: `tests/test_dev_lifecycle_commands.py:70`, `tests/test_make_lifecycle.py:121`, `tests/test_make_lifecycle.py:162`, `tests/test_make_lifecycle.py:192`
