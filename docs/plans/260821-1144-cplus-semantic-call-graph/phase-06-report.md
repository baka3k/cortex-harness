# Phase 06 Report: Guarded publication, incremental generation, and rollback

Date: 2026-08-21 · Branch: `feature/local-db` · Status: complete (Phase 07 pending)

## Delivered components

### 1. Guarded publication module — `code-tiny/tools/cplus/guarded_publication.py` (new)

`GUARDED_PUBLICATION_SCHEMA_VERSION = "1"`,
`SEMANTIC_PUBLICATION_POLICY_VERSION = "semantic-publication-v1"`.

- **Two-dimension gate composition.** `strong_edge_publication_decision`
  composes parse trust (parser-quality tier + `evidence_policy` owner rules)
  with semantic trust (call-evidence contract + Pro*C bundle state + strict
  map quality + generated-code class). Neither owner contract is weakened:
  quarantined/unknown tiers and `strong_relations_allowed=False` block
  exactly as before, weak evidence is never promoted, and a failure in
  either dimension downgrades rather than publishing.
- **Semantic publication policy.** `SemanticPublicationPolicy` with modes
  `gated | off | rollback` and environment switch
  `CORTEX_SEMANTIC_PUBLICATION_MODE`. `off`/`rollback` never stage strict
  rows — suppression always pairs with stale-edge removal, never with
  silently surviving old edges.
- **Pro*C two-sub-result split.** `build_proc_staged_sub_results` produces
  independently validated `sql_original_result` and
  `semantic_mapped_result` with balanced
  discovered/accepted/quarantined/rejected accounting: SQL facts publish
  while the semantic lane is missing or rejected; mapped calls may retain
  while SQL grammar enrichment failed; original-region integrity failure
  closes both lanes.
- **Staged replacement.** `build_staged_replacement` +
  `compute_stale_strong_edges` build deterministic delete+write sets: every
  baseline strong edge inside the affected scope (changed/deleted/renamed
  source, stale map, downgrade, policy change) that is not re-accepted in
  the same staged set is scheduled for typed deletion
  (`source_deleted`, `source_renamed`, `downgraded`, `stale_map`, …).
  `StagedReplacementSet.fingerprint()` is deterministic for identical
  inputs. `apply_stale_strong_edge_deletions` executes the deletes inside a
  journal mutation fence with exact count verification.
- **Publication validation.** `validate_staged_publication` proves exact
  strict-call/site/observation/vector counts and zero stale-edge survivors
  before publication; missing readbacks fail closed.
- **Atomic publication + rollback.** `publish_staged_generation` drives the
  concurrency owner's `publish(manifest, validate)` boundary only after
  queue-drain and validation gates; any failure returns a typed
  reliability-contract outcome (`RunOutcome` + `FailureRecord`) with the
  previous generation retained. `SemanticGenerationLedger` durably records
  the last valid semantic generations (bounded, atomic writes);
  `rollback_to_last_valid_generation` is one configuration switch that
  disables semantic publication, preserves weak evidence, and serves the
  last valid generation without reparsing source.
- **Typed status.** `publication_status` exposes queue, coverage,
  generation, revision, and semantic-policy state for operators.
- **Vector/report safety.** `vector_item_rejection_reason` /
  `sanitize_vector_items` keep generated code, precompiler
  wrapper/runtime/unmapped/generated-declaration classes, masked-origin
  content, and credential-bearing text out of embeddings and reports, with
  typed accounting for every exclusion.

### 2. Journaled evidence operations (owner-contract registration)

- `tools/graph/journal/operation.py`: staging node contracts registered for
  `call_evidence:sites` (`CallSite`/`site_id`), `call_evidence:configurations`
  (`BuildConfiguration`/`config_fingerprint`), `call_evidence:coverage`
  (`SemanticCoverage`/`fingerprint`), including row identity/properties
  fields; new `evidence_edge` reconciliation for `call_evidence:edges*`
  labels. Required journal mode no longer rejects the semantic staging
  plane with `INVALID_CONTRACT`.
- `tools/graph/writer/query_contract.py`: `EvidenceEdgeGroup` +
  `group_evidence_edges` + `compile_evidence_edge_upsert` +
  `compile_evidence_edge_readback` — schema-index-validated endpoint
  identities (`site_id`, `config_fingerprint`, `id`), optional edge identity
  property (`evidence_id`, `statement_id`, `declaration_id`) or endpoint
  pair as merge identity.
- `tools/graph/journal/executor.py` / `reconcile.py` / `runtime.py`:
  trusted replay compiler, deterministic readback, and endpoint-barrier
  contract for `evidence_edge` batches. No second journal or writer was
  added.

### 3. Journal-safe staging writers — `tools/graph/writer/language_writer.py`

- `write_call_evidence_sites` now writes the canonical node merge (matching
  the journal's replay compiler byte-for-byte) and derives
  `HAS_CALLSITE`/`RESOLVES_TO` as required-endpoint evidence edges.
- `write_call_evidence_observations` writes `OBSERVED_AS` edges only for
  resolved callees; dangling evidence is precomputed by the merge layer and
  persisted on the site props (`dangling_observation_ids`), replacing the
  non-idempotent in-mutation existence probe.
- `write_build_configurations` splits into the canonical
  `BuildConfiguration` node merge plus `IN_CONFIGURATION` evidence edges.
- `write_proc_evidence_joins` compiles `EXECUTES_SQL` and
  `RESOLVES_HOST_DECLARATION` through the same evidence-edge contract
  (declaration label derived from `target_label`/declaration kind).
- New generic `write_evidence_edges` groups self-describing rows by edge
  shape; every batch is journaled under its own per-shape state key.

### 4. Merge-layer dangling computation — `tools/cplus/evidence_merge.py`

`merge_call_evidence` accepts `accepted_function_ids` (the staged
generation's accepted `Function` identities);
`MergedCallSite.to_writer_rows`/`dangling_observation_ids`/
`observation_writer_rows` and `EvidenceMergeResult.site_writer_rows`/
`observation_writer_rows` expose writer-ready rows with dangling flags.

### 5. Concrete-label Pro*C validation — `tools/common/payload_validation.py`

- `proc_nodes` rows must declare one of the five concrete labels; missing or
  unknown labels are quarantined `INVALID_RECORD` instead of defaulting.
- The nine Pro*C relationship types are validated against approved endpoint
  label pairs (`DECLARES_STATEMENT: Function→SqlStatement`, …,
  `REFERENCES_TABLE: SqlStatement→DatabaseTable`) before any graph or
  vector effect.

### 6. Analyzer wiring — `code-tiny/tools/cplus/cplus_analyzer.py`

- Scan-wide preflight `collection_labels["proc_nodes"]` default aligned with
  the validation envelope (`SqlStatement`, not the legacy `ProcStatement`).
- `_iter_vector_items` routes Pro*C nodes through the vector sanitizer;
  generated/credential-bearing/masked-origin items never reach Qdrant.

### 7. Tests — `tests/test_cplus_guarded_publication.py` (new, 56 tests)

Gate composition (both dimensions, quarantine/policy/unknown tiers, weak
evidence, generated/map/bundle blocks, mode/env overrides), Pro*C sub-result
isolation and balanced accounting, staged replacement determinism and
stale-edge removal (downgrade, delete, rename, re-accept survival),
concrete-label payload validation (five labels, missing/unknown label,
nine-relation endpoints, accounting), publication validation (exact counts,
mismatches, missing readbacks, stale survivors, coverage), pipeline
boundaries (success + ledger, validation failure retains previous
generation, undrained queue retryable, publication exception ambiguous),
rollback (last-valid selection, containment without ledger, bounded durable
ledger, status surface), vector safety (generated classes, credentials,
masked origin, approved SQL, accounted exclusions), journal registration
(node contracts, evidence-edge replay/readback compilation, pattern-merge
edges, unsupported labels still fail closed), and required-mode writer round
trips for sites/observations/configurations/coverage, strict call rows, and
Pro*C evidence joins.

`tests/test_cplus_evidence_merge.py` staging tests updated to the
journal-safe contract: dangling evidence is flagged by the merge layer and
persisted on site props; host-declaration joins declare their graph label.

## Verification

- New: 56/56 pass (`tests/test_cplus_guarded_publication.py`).
- Updated lane: `tests/test_cplus_evidence_merge.py` 37/37.
- Adjacent lanes: `test_analyzer_payload_validation`,
  `test_cplus_call_evidence`, `test_graph_write_journal`,
  `test_storage_concurrency_contract`, `test_cplus_proc_sql`,
  `test_proc_semantic_manifest`, `test_parse_quality_contract`,
  `test_proc_source_map` — all pass.
- Whole suite: identical failure set with and without this change
  (35 pre-existing failures in unrelated lanes: cobol/dart/aspnet/flutter/
  framework fixtures, plus `test_graph_write_journal_runtime.py` which
  requires the `pytest-asyncio` plugin absent from this venv) — zero new
  failures.

## Coverage and gaps

- `publish_staged_generation` is exercised against a simulated concurrency
  owner boundary; driving it from the real `StoreGateway`/`GenerationManager`
  in the incremental-sync orchestrator is the Phase 07 pilot's wiring work,
  as is threading the typed status into CLI/MCP responses.
- Stale-strong-edge deletion runs pre-write under a journal mutation fence,
  mirroring the incremental-cleanup path; migrating cleanup deletes onto
  journaled delete operations remains with the graph-hardening owner.
- Boundary crash/cancel coverage composes this phase's typed outcomes with
  the journal runtime's existing kill/lease/ambiguity test cohort
  (`test_graph_write_journal_runtime.py`), which certifies
  exactly-once for the now-registered evidence operations.
- `REFERENCES_STATEMENT` remains declared-but-not-emitted by the lexical
  owner; its endpoint pair is validated when present.

## Security posture

- Generated, wrapper, runtime, and unmapped material is excluded from
  vectors/reports by class and origin, with accounted rejections.
- Credential markers (connect descriptors, passwords, user ids) are scrubbed
  fail-closed; no raw precompiler command text enters any persisted record.
- All evidence edges fail closed on unresolved required endpoints; nothing
  invents graph endpoints for dangling evidence.
