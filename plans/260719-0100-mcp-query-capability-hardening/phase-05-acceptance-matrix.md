# Phase 05: Fixture-Backed Acceptance Matrix

## Context

Alias routing tests prove dispatch configuration but not whether source code creates
the graph facts required by queries.

## Requirements

- Maintain a canonical acceptance matrix for every advertised parser profile.
- Use real source fixtures for the modified web/database profiles.
- Reuse existing parser fixtures/regressions for unaffected profiles.
- Test extraction, graph rows, capability discovery, and query gates.

## Architecture

Store the matrix as test data with dimensions for symbols, calls, endpoints, and
database facts. Parametrized tests verify registry claims and link every non-`none`
claim to a fixture-backed evidence assertion.

## Related Files

- `tests/fixtures/`
- `tests/test_mcp_acceptance_matrix.py`
- focused existing parser/framework tests
- capability documentation

## Implementation Steps

1. Define matrix rows for every canonical profile.
2. Add source fixtures and evidence adapters for target profiles.
3. Assert dimensional support matches fixture evidence.
4. Run focused and regression suites; document provider exclusions.

## Todo

- [x] Add failing matrix coverage test.
- [x] Connect fixture-backed evidence.
- [x] Run regression suite and compile checks.
- [x] Update docs/log and finalize.

## Risks

- Environment-dependent parser packages; fixture tests should use repository-pinned
  runtimes and skip only explicit unavailable external dependencies.

## Success Criteria

- No advertised profile exists without an acceptance row.
- Modified profile claims are proven by parsed source facts, not alias resolution.
