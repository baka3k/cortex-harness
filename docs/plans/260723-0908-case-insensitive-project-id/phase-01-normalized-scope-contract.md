# Phase 01: Add One Normalized Project-Scope Contract

## Context

Current MCP retrieval paths trim `project_id` but compare it exactly. Fixing only a wrapper or only graph queries would leave Qdrant, Python post-filtering, backend-specific MCP modules, or full-stack bridge tools case-sensitive. This phase establishes one internal comparison contract and applies it at both persistence and query boundaries.

## Requirements

- Add a helper that returns a normalized lookup key using `str(value).strip().casefold()` while retaining the current helper for raw trimmed values.
- Use the field name `project_id_normalized` consistently in graph properties and Qdrant payloads.
- Preserve raw `project_id` values and all current blank/unscoped behavior.
- Apply the same normalization to `project_id`, `be_project_id`, and `fe_project_id` at MCP dispatch/service boundaries.
- Use exact comparisons against `project_id_normalized`; do not depend on provider-specific runtime lowercase functions for the steady-state query path.

## Architecture

```text
MCP project scope input (HIEP / hiep / hiEp)
                    |
                    v
project_scope comparison helper -> "hiep"
                    |
          +---------+---------+
          |                   |
          v                   v
graph.project_id_normalized   Qdrant project_id_normalized
exact indexed equality        exact keyword payload filter
          |                   |
          +---------+---------+
                    v
          raw project_id stays unchanged
```

## Related Files

Primary implementation seams:

- `code-tiny/tools/common/project_scope.py`
- `code-tiny/tools/common/intelligent_retrieval.py`
- `code-tiny/tools/common/graph_expander.py`
- `code-tiny/tools/common/primary_vector_sync.py`
- `code-tiny/mcp/fastmcp_server.py`
- `code-tiny/mcp/cplus/cplus_mcp.py`
- `code-tiny/mcp/android/android_mcp.py`
- `code-tiny/mcp/java/java_mcp.py`
- `code-tiny/mcp/services/explore_service.py`
- `code-tiny/mcp/services/workflow_service.py`
- `code-tiny/mcp/unified_mcp.py`
- `code-tiny/mcp/tool_metadata.py`
- `code-tiny/tools/graph/driver/neo4j_driver.py`
- `code-tiny/tools/graph/driver/falkordb_driver.py`

Additional backend query files should be changed only when the exact-project-scope inventory proves they bypass these shared seams.

## Implementation Steps

1. Extend the shared project-scope module with separate raw-value and comparison-key functions. Document that case-only variants intentionally share a logical scope.
2. Change Python candidate and driver post-filters to compare normalized keys, including candidates with missing or non-string values.
3. Stamp `project_id_normalized` into shared graph node write inputs without mutating `project_id`. Centralize this at the lowest common persistence boundary available after the provider migration; patch analyzer-specific writers only where they bypass that boundary.
4. Stamp `project_id_normalized` into shared Qdrant payload construction. Audit mature analyzer-specific vector writers and add the field only where they bypass `primary_vector_sync`.
5. Change Qdrant MCP filters to exact-match `project_id_normalized` and retain no-filter behavior for absent optional scope.
6. Replace exact raw project predicates in shared retrieval and expansion code with exact normalized-field predicates and normalized parameters.
7. Update backend-specific MCP query paths and unified bridge queries, including independent frontend/backend scope filters.
8. Update public MCP metadata descriptions from “exact project scope” to “case-insensitive project scope”; do not expose the internal normalized field as a new user argument.
9. Add focused unit and contract tests for helper behavior, payload enrichment, query parameters, Qdrant filters, raw-ID preservation, and backend routing.

## Todo

- [x] Inventory all generated MCP inputs named `project_id`, `be_project_id`, or `fe_project_id` from the runtime catalog.
- [x] Implement the shared normalized-key helper.
- [x] Apply the key at graph and vector persistence boundaries.
- [x] Apply the key to all scoped MCP query paths.
- [x] Update metadata and focused tests.

## Risks

- Some analyzers construct graph or vector payloads without shared helpers; the exact inventory must precede edits.
- Changing `normalize_project_id()` itself could alter point identity and cache semantics. Use a distinct comparison-key helper instead.
- Full-stack queries have two independent project filters and must not accidentally reuse the wrong normalized parameter.

## Success Criteria

- New graph nodes and Qdrant points contain the normalized field while preserving raw IDs.
- Every scoped MCP family sends a normalized exact-match parameter/filter.
- Unit tests prove all requested case variants match and nearby but different IDs do not.
- Existing identity and unscoped-query tests remain unchanged and pass.
