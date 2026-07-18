# MCP Query Capability Hardening — 2026-07-19

## Context

MCP language routing could silently fall back to the generic C/C++ path and advertise endpoint or database queries that the indexed graph could not satisfy. The work follows the [MCP query capability hardening plan](../../plans/260719-0100-mcp-query-capability-hardening/plan.md) and replaces alias-only confidence with source-fixture evidence.

## Change

- The registry now publishes `query_engine`, declares separate `symbols`/`calls`/`endpoints`/`database` support, and gives SQL/PLSQL dedicated `Table`, `View`, and `Procedure` profiles (`code-tiny/mcp/framework_registry.py:43`, `code-tiny/mcp/framework_registry.py:61`, `code-tiny/mcp/framework_registry.py:175`).
- Unified dispatch rejects unknown parsers and fails closed with `capability_unavailable` when required labels or relationships are absent, including `ApiEndpoint`/`HANDLES` gates (`code-tiny/mcp/unified_mcp.py:298`, `code-tiny/mcp/unified_mcp.py:387`, `code-tiny/mcp/unified_mcp.py:504`, `code-tiny/mcp/unified_mcp.py:2075`).
- New ingestion overlays extract FastAPI, Django, Express, and Laravel endpoints and connect them to handlers; SQL/PLSQL extraction emits read, write, and table-reference lineage (`code-tiny/tools/web_framework/pipeline.py:141`, `code-tiny/tools/graph/writer/web_framework_writer.py:97`, `code-tiny/tools/database_schema/pipeline.py:73`, `code-tiny/tools/database_schema/pipeline.py:123`).
- Incremental sync routes framework and database sources to the overlays, while the acceptance matrix executes checked-in fixtures for endpoint/handler and schema/lineage facts (`code-tiny/tools/sync/incremental_sync.py:152`, `code-tiny/tools/sync/incremental_sync.py:179`, `tests/test_mcp_acceptance_matrix.py:83`, `tests/test_mcp_acceptance_matrix.py:97`).

## Impact

MCP callers now receive explicit parser and capability failures instead of plausible empty results, and supported web/database projects gain queryable semantic facts. **Risk level: medium** because regex-based overlays can still miss uncommon framework syntax or SQL dialect constructs, and graph-writer behavior affects ingestion output.

## Decision

Keep internal backend identifiers private and expose the stable `graph_generic` query-engine contract. Fail closed on unknown parser or missing provider schema, then earn advertised capability levels through additive overlays and fixture-backed acceptance tests. A universal C/C++ fallback and alias-route-only validation were rejected because both conceal incomplete language semantics.

## References

- Plan: [plans/260719-0100-mcp-query-capability-hardening/plan.md](../../plans/260719-0100-mcp-query-capability-hardening/plan.md)
- Commit: `09ebfa7a66fd33f0e7b495a1ec8b3a52abe2cbff`
- Acceptance contract: `docs/MCP_CAPABILITY_ACCEPTANCE_MATRIX.md:43`
