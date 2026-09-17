# Phase 04: Acceptance and Live Verification

## Context

Unit contracts must be paired with a bounded live read-only check against the
active FalkorDB graph.

## Requirements

- Run targeted routing/search/catalog/capability tests.
- Compile changed Python modules and run diff checks.
- Call the live inspector against `cortext` without mutating graph/vector data.
- Document stale-index or timeout observations honestly.

## Related Files

- `tests/test_framework_mcp_search.py`
- `tests/test_unified_mcp_input_coercion.py`
- `tests/test_mcp_acceptance_matrix.py`
- `docs/MCP_CAPABILITY_ACCEPTANCE_MATRIX.md`

## Todo

- [x] Run focused regression suite.
- [x] Run live read-only capability inspection.
- [x] Update docs/log and finalize.

## Risks

- The active index predates current source. Live results validate diagnostics,
  not semantic completeness, until an incremental sync is run separately.

## Success Criteria

- Deterministic tests pass and live inspection returns structured evidence.
- No full sync, embedding run, or provider mutation occurs.
