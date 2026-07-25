# Phase 05: Unified MCP Context Tools

## Context

The graph will contain canonical module, API, descriptor, and endpoint facts
after Phases 02-04. Unified MCP needs bounded aggregate services that respect the
existing parser capability and provider schema contracts.

## Requirements

- Add the four requested tools to Unified MCP and shared metadata.
- Keep query logic in focused services, not large inline wrappers.
- Enforce project/module scoping, deterministic ordering, pagination, and
  capability diagnostics.
- Work against provider-neutral result shapes.
- Avoid N+1 graph queries for architecture summaries.

## Architecture

Add `code-tiny/mcp/services/project_context_service.py` with provider-neutral
query/normalization methods:

- `get_project_modules`
- `get_public_apis`
- `get_endpoints`
- `get_module_architecture_summary`

`unified_mcp.py` validates/coerces public inputs, resolves parser/provider
capability context, calls the service, and adds routing diagnostics.

Extend tool metadata and capability declarations with additive context features:

- `module_queries`
- `public_api_queries`
- `endpoint_inventory_queries`
- `architecture_summary_queries`

Profiles advertise only fixture-backed support. Missing required labels or
relationships returns `capability_unavailable`, not an empty success.

## Related Files

- New `code-tiny/mcp/services/project_context_service.py`
- `code-tiny/mcp/services/__init__.py`
- `code-tiny/mcp/unified_mcp.py`
- `code-tiny/mcp/tool_metadata.py`
- `code-tiny/mcp/framework_registry.py`
- Provider schema inspection helpers
- `code-tiny/mcp/Readme.md` or the repository's canonical MCP README path
- New `tests/test_project_context_service.py`
- New `tests/test_project_context_mcp_tools.py`
- `tests/test_unified_mcp_wrapper_signatures.py`
- `tests/test_mcp_acceptance_matrix.py`

## Implementation Steps

1. Add catalog metadata, examples, required/optional inputs, and outputs for all
   four tools.
2. Register thin Unified MCP wrappers and update wrapper-signature assertions.
3. Add capability requirements per tool:
   - modules: `ProjectModule` plus containment/dependency schema;
   - public APIs: module ownership plus visibility/export properties;
   - endpoints: supported endpoint labels plus module exposure/handler edges;
   - summary: module schema and whichever optional dimensions are observed.
4. Implement `get_project_modules` with internal/external dependency grouping,
   filters, stable sort, cursor/offset bounds, and diagnostics.
5. Implement `get_public_apis` with strict/inferred policy, symbol-kind/language
   filters, signatures, evidence, ownership, stable sort, and pagination.
6. Implement `get_endpoints` with normalized protocol/framework/method/path
   filters, handler/security evidence, deduplication, and pagination.
7. Implement `get_module_architecture_summary` as bounded aggregate queries:
   counts plus configurable samples, descriptor/stack summaries, dependencies,
   APIs, endpoints, specialized Android/framework/persistence facts, and
   ingestion provenance.
8. Normalize provider node/list/map values through existing record parsers.
9. Add recording-driver/service tests for query parameters, result shape,
   missing schema, empty project, ambiguous scope, provider errors, pagination,
   and maximum limits.
10. Add Unified MCP acceptance tests derived from the mixed fixture.

## Todo

- [ ] Four tools appear in `list_mcp_functions`.
- [ ] Public signatures and metadata match implementations.
- [ ] Project/module scoping is mandatory and normalized.
- [ ] Capability gaps are explicit.
- [ ] Result ordering and pagination are deterministic.
- [ ] Architecture summary uses bounded aggregate queries.

## Risks

- Direct Cypher can diverge between providers. Keep queries within the supported
  provider-neutral subset and test result normalization with both drivers.
- Architecture summaries can explode in size. Return counts and bounded samples
  by default; reject unsafe limits.
- Optional schema dimensions should not block an otherwise useful summary.
  Report partial sections and diagnostics rather than failing the whole response.
- Adding support dimensions can break clients that assume a fixed map. Prefer
  additive feature flags unless compatibility tests approve new dimensions.

## Success Criteria

- The four tool wrappers pass metadata/signature/routing tests.
- Fixture-backed service tests return exact expected normalized records.
- Unknown project/module scopes and missing schema produce structured errors.
- Default summaries are bounded and require no per-symbol query loop.
- Tool responses are equivalent across recording Neo4j/FalkorDB result shapes.

