# Phase 02: Provider Schema Gates

## Context

Existing diagnostics filter relationship defaults but do not prove required node
labels such as `ApiEndpoint` exist. Some endpoint tools therefore return empty
success on graphs incapable of answering the question.

## Requirements

- Inspect provider node labels and relationship types.
- Declare required schema per capability-sensitive tool.
- Return `capability_unavailable` with missing labels/relationships.
- Distinguish unavailable schema inspection from known-missing schema.

## Architecture

Extend the shared C++/generic query backend's schema probe, then pass label and
relationship requirements through Unified MCP's direct capability context.

## Related Files

- `code-tiny/mcp/cplus/cplus_mcp.py`
- `code-tiny/mcp/unified_mcp.py`
- provider-neutral MCP tests

## Implementation Steps

1. Add cached label discovery beside relationship discovery.
2. Extend capability diagnostics with requested/available/missing labels.
3. Gate endpoint caller and API-chain tools before Cypher execution.
4. Add deterministic Neo4j/Falkor-shaped test doubles.

## Todo

- [x] Write missing-label and missing-edge tests.
- [x] Implement schema probe/gate.
- [x] Verify supported schemas still execute.

## Risks

- Schema procedures differ by provider; retain query fallbacks and report
  inspection unavailability without inventing support.

## Success Criteria

- A graph without `ApiEndpoint` or `HANDLES` cannot produce a successful endpoint
  response.
