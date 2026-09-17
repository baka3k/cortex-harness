# Phase 04: Validate Retrieval, Cleanup, and Regressions

## Context

Completion requires proof that vectors are written, stale points are removed safely, and framework facts remain discoverable without speculative collections.

## Requirements

- Validate each missing primary parser with deterministic fixtures.
- Verify full, repeat, incremental update, and deletion behavior.
- Test cross-project/parser/root isolation.
- Validate ASP.NET and other overlays through base-language semantic seeds plus graph expansion.
- Record all commands, counts, exclusions, and any direct-overlay fallback decision.

## Architecture

Use fake Qdrant/embedder tests for deterministic CI coverage and an optional local-Qdrant smoke suite for transport integration. Use existing framework fixtures and semantic-search expansion paths for overlay retrieval.

## Related Files

- `tests/fixtures/` language and framework fixtures
- New primary vector ingestion tests
- Existing COBOL, Dart, ASP.NET, framework MCP, incremental sync, and Qdrant tests
- `plans/260716-1615-primary-vector-ingestion-completion/reports/validation-report.md`

## Implementation Steps

1. Run focused unit/contract tests for all five newly completed primary writers.
2. Run existing COBOL and mature-analyzer vector regressions.
3. Exercise full, idempotent repeat, incremental edit, rename/delete, and failure recovery cases.
4. If local Qdrant is available, run a smoke scan and verify collection point counts and payload filters.
5. Run representative ASP.NET, Spring, Servlet/JSP, MyBatis, Struts, and Flutter semantic queries with graph expansion.
6. Only if a named query cannot reach an unanchored overlay fact, document the evidence and implement the smallest direct-overlay vector path by setting `writes_vectors=True` for that overlay and adding scoped tests.
7. Write the validation report and update blocked plans when their vector criteria are satisfied.

## Todo

- [x] Primary vector tests pass.
- [x] Incremental cleanup and cross-scope isolation pass.
- [x] COBOL and mature analyzers do not regress within the documented runtime exclusion.
- [x] Overlay retrieval acceptance passes; Struts uses the bounded graph-search fallback.
- [x] Validation report records reproducible evidence.

## Risks

- Live Qdrant may be unavailable; fake-backend coverage remains mandatory and live validation is reported as an explicit environment exclusion.
- Retrieval queries can pass accidentally through unrelated seeds; assert returned project, parser, path, and graph-expansion evidence.

## Success Criteria

- All required tests pass with no stale or cross-scope vectors.
- The implementation satisfies the plan-level success criteria.
- Flutter and Perl plans can remove this plan from `blockedBy` after their vector acceptance checks pass.
