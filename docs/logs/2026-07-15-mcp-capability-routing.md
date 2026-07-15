# MCP Capability Routing Upgrade — 2026-07-15

## Context

Unified MCP exposed multiple parser and framework aliases, but discovery and routing were split between backend directories, module-local alias sets, and framework metadata. The plan called for a single capability contract that distinguishes aliases, graph profiles, and backend-specific runtime semantics while retaining Android's specialized backend and using `cplus` for compatible profiles (`plans/260715-2200-mcp-capability-routing/plan.md:14` and `plans/260715-2200-mcp-capability-routing/plan.md:28`). The implementation proceeded against the current provider-neutral contract; live Neo4j/FalkorDB parity remains gated by the in-progress migration (`plans/260715-2200-mcp-capability-routing/plan.md:7`).

## Change

The canonical registry now defines parser aliases, backend assignment, support level, labels, relationships, searchable properties, tool-specific query profiles, and feature flags in one immutable capability model (`code-tiny/mcp/framework_registry.py:44` and `code-tiny/mcp/framework_registry.py:137`). Unified MCP derives capability summaries and dispatch defaults from that registry, publishes the capability catalog through parser discovery, and resolves direct endpoint and workflow operations with explicit mandatory-relationship checks (`code-tiny/mcp/unified_mcp.py:258`, `code-tiny/mcp/unified_mcp.py:286`, `code-tiny/mcp/unified_mcp.py:490`, and `code-tiny/mcp/unified_mcp.py:584`).

The generic backend now filters requested relationship profiles against the active provider and returns supported, partial, or unsupported diagnostics (`code-tiny/mcp/cplus/cplus_mcp.py:1044`). Search applies capability labels and properties while merging full-text and property-fallback results (`code-tiny/mcp/cplus/cplus_mcp.py:2613` and `tests/test_framework_mcp_search.py:18`). Parser-aware relationship and label profiles are also threaded through graph exploration and workflow impact scoring (`code-tiny/mcp/services/explore_service.py:215`, `code-tiny/tools/common/intelligent_retrieval.py:518`, and `code-tiny/tools/common/workflow_impact_scorer.py:200`).

## Impact

Impact level: medium. Every advertised alias now resolves through one deterministic capability catalog, and search, path, flow, endpoint, workflow, semantic expansion, and impact operations can consume parser-specific defaults without requiring a separate MCP backend per framework. Provider schema omissions are visible as structured diagnostics, while missing mandatory edges produce `unsupported_capability` instead of silent empty success; regression coverage includes semantic-only exploration, endpoint requirements, profile-aware traversal, provider filtering, impact scoring, and discovery (`tests/test_unified_mcp_input_coercion.py:20`, `tests/test_unified_mcp_input_coercion.py:70`, and `tests/test_unified_mcp_input_coercion.py:173`).

Focused verification completed with 38 passing MCP/routing tests, plus deterministic provider query-shape runs of 6 passing tests under Neo4j configuration and 6 passing tests under FalkorDB configuration. Mandatory review approved the final implementation at 9.6/10 with zero critical findings. A live FalkorDB probe confirmed partial diagnostics and mandatory-edge rejection, but live Neo4j parity was unavailable and remains an external gate (`plans/260715-2200-mcp-capability-routing/plan.md:98`). The full repository suite reached 145 passes and 23 unrelated failures in COBOL grammar/runtime/platform coverage and Perl coverage requiring the unavailable `tree_sitter_perl` dependency; those failures are not claimed as resolved (`plans/260715-2200-mcp-capability-routing/plan.md:103`).

## Decision

Capability differences are represented as data in one registry; a new backend is reserved for materially different query or runtime semantics. Android therefore remains specialized, while Spring, Servlet/JSP, MyBatis, Struts, Flutter, ASP.NET, COBOL, Perl, and generic language profiles route through the compatible generic backend. Tool-specific relationship profiles separate flow edges from fixed bridge edges, and provider validation narrows only what the active schema can execute. Scoped implementation and deterministic query-shape compatibility are accepted as complete, while live cross-provider parity remains explicitly excluded until the migration gate is cleared.

## References

- plan: `plans/260715-2200-mcp-capability-routing/plan.md:1`
- capability registry: `code-tiny/mcp/framework_registry.py:44`
- unified routing: `code-tiny/mcp/unified_mcp.py:490`
- capability diagnostics: `code-tiny/mcp/cplus/cplus_mcp.py:1044`
- verification coverage: `tests/test_unified_mcp_input_coercion.py:20`
- commit: `2cad151459c35cf7afed4d606c636eacf5b21191`
