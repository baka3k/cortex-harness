# Guarded publication, staged replacement, and rollback (Phase 06) — 2026-08-21

## Context
Plan `260821-1144-cplus-semantic-call-graph` Phase 06 integrates the
Phase 01–05 evidence contract with the owning graph-journal, parser-quality,
and store-concurrency contracts. Semantic evidence is not useful to
consumers until it can be published without mixing baseline, repaired,
stale, weak, and strong facts. This phase intentionally consumes — rather
than re-implementing — the owning graph journal, generation manager, and
reliability envelope.

## Change
- New `code-tiny/tools/cplus/guarded_publication.py` (1302 lines):
  two-dimension gate composition (`strong_edge_publication_decision`
  composes parse trust with semantic trust without weakening either owner),
  `SemanticPublicationPolicy` (modes `gated|off|rollback` with
  `CORTEX_SEMANTIC_PUBLICATION_MODE` environment switch), Pro*C two-sub-result
  split (`sql_original_result` + `semantic_mapped_result` with independent
  discovered/accepted/quarantined/rejected accounting), deterministic staged
  replacement sets with stale-strong-edge removal, exact-count publication
  validation, atomic publication through the concurrency owner's
  `publish(manifest, validate)` boundary, `SemanticGenerationLedger`
  (bounded atomic history), `rollback_to_last_valid_generation`
  (containment without reparse), typed `PublicationOutcome`
  (`RunOutcome` + `FailureRecord`), operator-facing `publication_status`,
  and vector/report safety (`vector_item_rejection_reason`,
  `sanitize_vector_items`).
- `tools/graph/journal/operation.py`: staging node contracts registered
  for `call_evidence:sites`, `call_evidence:configurations`,
  `call_evidence:coverage`; new `evidence_edge` reconciliation.
- `tools/graph/writer/query_contract.py`: `EvidenceEdgeGroup` +
  `group_evidence_edges` + `compile_evidence_edge_upsert/readback`.
- `tools/graph/writer/language_writer.py`: journal-safe staging writers
  for sites, observations, configurations, Pro*C evidence joins, and
  generic evidence edges.
- `tools/graph/schema/manifest.py`: evidence staging label registrations.
- `tools/common/payload_validation.py`: concrete-label Pro*C validation
  (five labels, nine relationship endpoint pairs, fail-closed quarantine).
- `tools/cplus/cplus_analyzer.py`: `_iter_vector_items` routes Pro*C nodes
  through `vector_item_rejection_reason`; generated/credential-bearing/
  masked-origin items never reach Qdrant.
- `tests/test_cplus_guarded_publication.py` (new, 56 tests): gate
  composition, Pro*C sub-result isolation, staged replacement determinism,
  payload validation, publication validation, pipeline boundaries, rollback,
  vector safety, journal registration, and required-mode writer round trips.

## Impact
- **Consumers**: Phase 07 pilot orchestrator will drive staged replacement
  and atomic publication through this module. MCP/CLI wiring is Phase 07
  work.
- **Safety**: stale strong edges are removed on downgrade, deletion, or
  invalidation — suppressing new edges without removing old ones is
  forbidden. Generated code, credentials, and masked-origin content never
  enter embeddings or reports.
- **Risk**: low. The module is self-contained, all 56 tests pass, all 188
  cplus tests pass, and zero regressions in adjacent lanes.

## Decision
- Consume owning contracts rather than building a second writer, journal,
  scheduler, or generation manager. The `publish_staged_generation`
  interface accepts either a real `GenerationManager`/`StoreGateway` or a
  callable, keeping the concurrency owner's atomic flip intact.
- Pro*C publication carries two independently validated sub-results: SQL
  facts may publish while the semantic lane is missing; mapped calls may
  retain while SQL grammar enrichment fails. Original-region integrity
  failure closes both lanes.
- `ValueError` from the publication boundary is terminal (configuration
  error); any other exception is ambiguous (reconcile before retry).

## References
- plan: `plans/260821-1144-cplus-semantic-call-graph/phase-06-guarded-publication-integration.md`
- report: `plans/260821-1144-cplus-semantic-call-graph/phase-06-report.md`
- commit: `97fa69b` (implementation + tests)
- commit: `aaa104b` (report test count correction)
