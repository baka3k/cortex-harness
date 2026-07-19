# Phase 02: Runtime Capability Observability

## Context

Static support claims cannot tell a caller whether required graph facts have been
ingested into the active provider.

## Requirements

- Define a versioned dimensional schema contract.
- Inspect live node labels and relationship types.
- Report advertised, observed, and effective support plus missing evidence.
- Fail fast for unknown parsers and remain read-only.

## Related Files

- `code-tiny/mcp/framework_registry.py`
- `code-tiny/mcp/unified_mcp.py`
- `code-tiny/mcp/tool_metadata.py`
- `tests/test_framework_mcp_routing.py`
- `tests/test_unified_mcp_input_coercion.py`

## Todo

- [x] Add contract evaluator tests.
- [x] Add `inspect_parser_capabilities` tool and catalog metadata.
- [x] Add schema fingerprint and recommendations.

## Risks

- Graphs with custom labels may need future contract extension; unknown/custom
  facts must not be silently treated as full support.

## Success Criteria

- Missing endpoint/database schema is visible before a query is attempted.
- Identical schemas produce identical fingerprints.
