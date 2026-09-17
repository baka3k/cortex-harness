# Phase 09: MCP Special-File and Framework Context Queries

## Context

Low-level symbol and graph tools are insufficient for quickly understanding why
a project is classified as a framework, which files define its modules/config,
or which architecture dimensions are complete. Aggregate context must be
available without exposing secrets or generating oversized responses.

## Requirements

- Add `get_project_special_files` and `get_framework_context`.
- Extend the four existing planned context tools across all primaries/overlays.
- Report coverage depth, provenance, diagnostics, generated/canonical status,
  redaction, and freshness.
- Reuse provider capability gates and deterministic pagination.

## Architecture

Extend `mcp/services/project_context_service.py` with two bounded query methods.

`get_project_special_files` groups facts by role and owning module:

- identity/topology;
- dependency/lock;
- build/tooling;
- framework/runtime;
- interface/schema;
- resource/UI/localization;
- deployment;
- generated;
- secret-bearing/redacted.

`get_framework_context` returns one or more `FrameworkInstance` summaries with
dimension-specific support and bounded samples.

## Related Files

- `code-tiny/mcp/services/project_context_service.py`
- `code-tiny/mcp/services/__init__.py`
- `code-tiny/mcp/unified_mcp.py`
- `code-tiny/mcp/tool_metadata.py`
- `code-tiny/mcp/framework_registry.py`
- Provider schema/capability inspection
- `tests/test_project_context_service.py`
- New `tests/test_framework_context_mcp.py`
- New `tests/test_special_files_mcp.py`
- `tests/test_unified_mcp_wrapper_signatures.py`
- `tests/test_mcp_acceptance_matrix.py`

## Implementation Steps

1. Define request/response contracts, limits, filters, ordering, and capability
   requirements for both tools.
2. Register metadata and thin Unified MCP wrappers.
3. Implement special-file role/module/parser/framework/status filters, missing
   expectations, diagnostics, and safe summaries.
4. Implement framework filters plus entrypoint/config/endpoint/security/
   persistence/messaging-job/UI/deployment dimensions.
5. Extend `get_project_modules` with descriptors, targets, workspaces, and
   dependency manifests.
6. Extend `get_public_apis` with language-specific export evidence for every
   primary analyzer.
7. Extend `get_endpoints` across all endpoint-producing overlays and IDLs.
8. Extend `get_module_architecture_summary` with framework/special-file coverage
   counts and bounded samples.
9. Add `parse_depth`, `coverage_status`, `source_provenance`, `freshness`, and
   `redacted` response fields.
10. Add provider-neutral service and Unified MCP acceptance tests.

## Todo

- [ ] Two new tools appear in `list_mcp_functions`.
- [ ] Four existing planned tools cover all registered analyzers/overlays.
- [ ] Secret-bearing values cannot leave the service.
- [ ] Partial/missing/stale coverage is explicit.
- [ ] Responses are bounded, paginated, and deterministic.

## Risks

- A single architecture summary can duplicate the two detailed tools. Keep
  summary sections compact and linkable by IDs.
- Missing-file reporting can be noisy. Only infer expectations from strong module
  and framework evidence.
- Provider schemas may contain older facts without coverage metadata. Return
  `unknown` and a resync recommendation rather than false completeness.

## Success Criteria

- An AI client can identify project modules, frameworks, decisive config files,
  public APIs, endpoints, and architecture gaps with bounded tool calls.
- Fixture-derived responses are stable across supported providers.
- Capability diagnostics distinguish unsupported parser logic, missing graph
  schema, stale ingestion, and legitimately empty results.

