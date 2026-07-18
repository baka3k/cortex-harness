# Phase 01: Public Routing and Support Contract

## Context

Unified MCP currently uses internal backend key `cplus` for the shared query
implementation and silently dispatches unknown parser names to that default.

## Requirements

- Reject unknown non-empty parser values before backend dispatch.
- Preserve generic behavior only when the parser is omitted.
- Publicly expose `query_engine=graph_generic` / `android_graph`.
- Report support by symbols, calls, endpoints, and database dimensions.

## Architecture

Keep internal backend modules unchanged initially. Add a public engine-name map and
central parser validation. Extend `FrameworkQueryConfig` with an immutable support
mapping serialized by `list_parsers` and every routed response.

## Related Files

- `code-tiny/mcp/framework_registry.py`
- `code-tiny/mcp/unified_mcp.py`
- MCP routing and input-coercion tests

## Implementation Steps

1. Add dimensional support values and validation to the registry model.
2. Add parser-resolution result/error helpers.
3. Replace public `backend` response fields with `query_engine`.
4. Update tool discovery text and compatibility tests.

## Todo

- [x] Write failing contract tests.
- [x] Implement registry/public routing changes.
- [x] Run focused MCP tests.

## Risks

- External clients may consume `backend`; document the transition and keep the
  internal key out of public semantics.

## Success Criteria

- Typos cannot return generic query results.
- Discovery and normal tool responses expose the same engine/support schema.
