# Phase 02: Parser-Aware Backend Dispatch and Query Defaults

## Goal

Make all unified graph operations consume the selected profile before calling
the physical backend.

## Implementation

1. Centralize backend resolution in `unified_mcp.py` using the capability
   registry, with Android-specific routing preserved.
2. Resolve default relationship types for `search`, `subgraph`, `find_paths`,
   `trace_flow`, endpoint chains, workflows, and impact analysis from the
   selected profile.
3. Resolve profile labels and searchable properties for semantic search and
   graph exploration.
4. Filter requested/default relationships against the active provider schema,
   retaining explicit diagnostics for omitted relationship types.
5. Ensure parser/framework context is propagated through nested service calls,
   not only top-level dispatch.
6. Add no-backend and unsupported-profile fallbacks that preserve current
   generic behavior without pretending to provide framework semantics.

## Acceptance

- ASP.NET, Spring, Struts, Flutter, and COBOL queries do not silently use only
  generic `CALLS` when their profile declares richer relationships.
- Android uses its existing backend and relationship defaults unchanged.
- Explicit user-provided relationship types continue to override defaults.
