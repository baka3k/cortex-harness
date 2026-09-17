# Phase 06: Guarded publication, incremental generation, and rollback

## Context

Semantic evidence is not useful to consumers until it can be published without
mixing baseline, repaired, stale, weak, and strong facts. This phase intentionally
waits for the owning parser-quality guarded-publication contract, graph schema
and durable journal, and StoreGateway staged-generation lifecycle. It integrates
with those mechanisms rather than implementing another writer, queue, journal,
or publication manager.

## Requirements

- Enforce semantic and parse-quality gates before graph payload construction and
  again before publication.
- Replace all affected source/configuration observations atomically; no stale
  strong edge may survive a downgrade, deletion, or context change.
- Journal deterministic graph operations and reconcile ambiguous outcomes using
  the graph-hardening owner contract.
- Build only in staging and publish graph/vector generations atomically through
  the concurrency owner.
- Preserve the last valid semantic generation and Tree-sitter containment
  rollback path.
- Propagate typed validation, worker, integrity, storage, cancellation, and
  publication outcomes through the reliability contract.
- Expose queue, coverage, generation, revision, and semantic-policy status.
- Validate and publish the five concrete Pro*C labels and nine relationship
  types through canonical schema/journal contracts, not a generic
  `ProcStatement` fallback or analyzer-local semantic authority.
- Allow valid original SQL facts to publish when the Pro*C semantic lane is
  unavailable while atomically removing any stale mapped strong calls.
- Keep graph and vector cleanup aligned for original SQL nodes, call evidence,
  source bundles/maps, generated-only evidence, deletes, and downgrades.

## Architecture

The semantic scheduler writes immutable evidence artifacts. The owner process
validates them through the common payload envelope, registers deterministic
node/relationship operations with the graph journal, drains required endpoint
barriers, verifies graph/vector effects, computes coverage/integrity summaries,
and publishes only the complete staging generation.

Publication requires both dimensions:

- parse trust: quality tier permits the requested evidence;
- semantic trust: call resolution/provider/context/mapping permits the requested
  edge class.

Failure in either dimension downgrades or quarantines evidence before writes; it
never converts to an empty successful strict graph.

Pro*C publication uses two independently validated sub-results in one staged
replacement set: `sql_original_result` and `semantic_mapped_result`. SQL may be
accepted while semantic evidence is missing or rejected. The inverse may retain
valid mapped C calls when SQL grammar enrichment is unavailable, but original
region integrity and mapping must still pass. The active generation changes
only after graph/vector reconciliation proves both accepted subsets and stale
artifacts are consistent.

## Related files

- `code-tiny/tools/cplus/cplus_analyzer.py`
- `code-tiny/tools/common/payload_validation.py`
- `code-tiny/tools/common/parse_quality.py`
- `code-tiny/tools/graph/schema/`
- `code-tiny/tools/graph/writer/language_writer.py`
- `code-tiny/tools/graph/journal/`
- `code-tiny/tools/sync/incremental_sync.py`
- StoreGateway/generation modules from the concurrency plan
- `cortex_harness/dev.py`
- graph runtime, journal, generation, result-contract, and failure tests
- `code-tiny/tools/cplus/proc_analyzer.py`
- `code-tiny/tools/cplus/cplus_analyzer.py`
- `code-tiny/tools/common/analyzer_cache.py`
- [Pro*C component map](pro-c-component-map.md)

## Implementation steps

1. Consume the finalized Phase 01 call-evidence adapter inside the common
   validation envelope; retain exact discovered/accepted/quarantined/rejected
   accounting.
2. Combine semantic promotion rules with parser-quality strong-edge eligibility
   without weakening either owner contract.
3. Build deterministic staged replacement sets for every changed/deleted source,
   context, dependency, analyzer version, policy, and Pro*C map.
4. Compile evidence writes through the canonical schema/relationship contract
   and durable mutation journal; reject unresolved required endpoints.
5. Validate exact node/callsite/evidence/edge/coverage counts and representative
   strict/conservative queries before publication.
6. Publish graph/vector generation atomically through StoreGateway only after
   queue drain, reconciliation, integrity, coverage, and freshness gates pass.
7. Prove failure/cancellation/timeout/crash at worker, enqueue, lease, mutation,
   ACK, validation, and publication boundaries leaves the last generation and
   baseline unchanged.
8. Add one explicit rollback configuration that disables semantic publication,
   preserves weak evidence, and serves the last valid semantic generation.
9. Thread typed outcomes, status, safe action, and bounded artifacts through CLI
   and MCP without raw tracebacks for known failures.
10. Make payload validation concrete-label aware for `SqlStatement`,
    `SqlDirective`, `SqlCursor`, `SqlHostVariable`, and `DatabaseTable`; validate
    the nine relation endpoints, original spans, map quality, evidence class,
    and project/file ownership before graph or vector effects.
11. Compile Pro*C node/relation/evidence writes through the canonical manifest
    and mutation journal while retaining the graph-hardening plan's ownership;
    remove analyzer-local schema decisions after provider parity is proven.
12. Add staged failure tests where SQL succeeds but mapping/Clang fails, Clang
    succeeds but SQL grammar enrichment fails, a map becomes stale, a generated
    wrapper is reclassified, and `.pc`/`.pcc` is deleted or renamed.
13. Verify vector items contain approved original SQL text/summary only and that
    generated code, raw commands, credentials, and precompiler runtime wrappers
    never enter embeddings or reports.

## Todo

- [x] Integrate semantic evidence with common payload validation.
- [x] Compose parse-quality and semantic promotion gates.
- [x] Implement exact staged replacement and stale-edge removal.
- [x] Register journaled schema-safe evidence operations.
- [x] Validate graph/vector counts, coverage, and query behavior.
- [x] Integrate atomic StoreGateway publication.
- [x] Pass boundary crash/cancel/ambiguous-effect tests.
- [x] Implement and rehearse containment/last-generation rollback.
- [x] Expose typed status and operator artifacts.
- [x] Implement concrete-label Pro*C payload and relation validation.
- [x] Journal canonical Pro*C SQL/map/evidence operations with graph/vector
  replacement parity.
- [x] Prove independent SQL/semantic failure isolation and stale-call removal.
- [x] Exclude generated/runtime/secret material from vector and report output.

## Risks

- Suppressing new strong edges without removing old ones creates a falsely
  trusted graph.
- A partially drained semantic queue can mix configurations or revisions.
- Count-only storage acknowledgement can conceal unresolved call endpoints.
- Rollback that reparses source instead of selecting a known generation can fail
  during the incident it is meant to mitigate.

Mitigate with staged full replacement, deterministic identities, journal
reconciliation, exact readback/accounting, generation fingerprints, and a
prevalidated rollback generation.

## Success criteria

- Active consumers never observe mixed revisions, configurations, evidence
  policies, or partial semantic updates.
- Stale strong edges are removed on downgrade, deletion, or invalidation.
- Every publication passes parse, semantic, graph/vector integrity, coverage,
  and representative-query validation.
- Failure or cancellation at every boundary preserves the previous generation
  and incremental baseline.
- Rollback returns to containment plus the last valid semantic generation
  without rebuilding source.
- Pro*C SQL facts, mapped calls, generated-only evidence, graph nodes/relations,
  and vector items have exact discovered/accepted/quarantined/rejected/deleted
  accounting, and every partial failure preserves the last consistent result.
