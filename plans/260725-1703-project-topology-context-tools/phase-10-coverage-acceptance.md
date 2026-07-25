# Phase 10: Full Parser and Framework Coverage Acceptance

## Context

Coverage claims will span 22 primary analyzers, 12 overlays, multiple descriptor
roles, and six aggregate MCP tools. Without one executable matrix, registry,
documentation, parser behavior, graph schema, and MCP capability metadata will
drift.

## Requirements

- Create one machine-readable acceptance matrix for all registered capabilities.
- Verify full/incremental parity and deletion ownership.
- Measure parser/framework detection cost and no-change behavior.
- Publish truthful support depth and gaps.

## Architecture

The acceptance matrix joins:

- analyzer/overlay registry entry;
- prerequisite parser/vector strategy;
- owned source extensions;
- special-file patterns and roles;
- parse depth and semantic dimensions;
- fixture path and expected graph facts;
- MCP profile/features;
- provider/schema requirements;
- full/incremental/deletion/security test IDs.

CI fails when an analyzer is registered without an acceptance row or when MCP
advertises deeper support than the fixture proves.

## Related Files

- New machine-readable matrix under `tests/fixtures/` or `docs/`
- `tests/test_common_analyzer_registry.py`
- `tests/test_mcp_acceptance_matrix.py`
- New `tests/test_special_file_acceptance_matrix.py`
- New `tests/test_framework_context_acceptance.py`
- Incremental/full-scan equivalence tests
- `code-tiny/README.md`
- `code-tiny/mcp/Readme.md`
- `docs/MCP_CAPABILITY_ACCEPTANCE_MATRIX.md`

## Implementation Steps

1. Define the acceptance matrix schema and validation loader.
2. Add rows for all 22 primary analyzers and 12 overlays.
3. Link every deep special-file adapter and framework dimension to a fixture and
   exact assertion.
4. Add registry-to-matrix, matrix-to-MCP-profile, and matrix-to-documentation
   consistency tests.
5. Run full/incremental/delete/rename equivalence across representative mixed
   workspaces.
6. Add malformed, oversized, secret-bearing, symlink/path, generated-file, and
   dynamic-build fixtures.
7. Benchmark detection-only, no-change, and affected-module scans; enforce
   bounded traversal and cache behavior.
8. Run recording-provider and live-provider acceptance according to the blocking
   migration state.
9. Update documentation from the executable matrix, including unsupported and
   partial entries.
10. Record final validation evidence and exclusions.

## Todo

- [ ] Every registered primary/overlay has an acceptance row.
- [ ] Every advertised deep capability has fixture evidence.
- [ ] Full and incremental final graphs are equivalent.
- [ ] Secret/security/resource-bound tests pass.
- [ ] MCP catalog and docs are generated or checked against the matrix.

## Risks

- A single giant fixture can be slow and hard to debug. Use small per-capability
  fixtures plus a bounded mixed-workspace integration fixture.
- Platform-specific project formats may not run on CI. Test pure extraction and
  recording writes cross-platform; gate native tool execution separately.
- Documentation generation can hide nuance. Preserve manual explanations while
  mechanically validating capability tables.

## Success Criteria

- No registry, parser capability, MCP tool, or documentation row can drift
  undetected.
- Every support claim is traceable to source fixtures and exact graph/MCP
  assertions.
- No-change scans remain cheap and affected-module scans avoid global reparsing.
- Final validation reports provider evidence and explicit environmental
  exclusions.

