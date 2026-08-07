# Phase 03: Verified storage effects and provider conformance

## Context

Driver-returned counts are insufficient proof of graph state in the reproduced
FalkorDB workload. Relationship integrity is currently checked after mutation,
and a mismatch does not identify whether endpoints were absent, duplicate,
ambiguous, or silently under-written. This phase consumes the active graph
hardening plan rather than creating a second writer architecture.

## Requirements

- Use the canonical schema/identity manifest and label-qualified queries.
- Preflight required endpoints from the validated identity registry.
- Return structured expected/attempted/persisted/unresolved/ambiguous results.
- Verify node, relationship, and vector effects deterministically.
- Treat timeouts/cancellation after submission as ambiguous.
- Preserve Neo4j compatibility while making FalkorDB the primary local canary.
- Use temporary isolated stores for every test.

## Architecture

Extend owning writer/driver contracts with provider-neutral mutation operations
and reconciliation descriptors. Each operation declares identity keys,
idempotency, expected effect, readback query, required barriers, schema/query
fingerprints, and retry safety.

Write sequence:

1. schema/index readiness;
2. normalized unique node/vector operations;
3. mutation execution under journal operation identity;
4. indexed identity/effect readback;
5. node-label/vector barrier completion;
6. required relationship preflight;
7. relationship mutation and readback;
8. generation cardinality/cross-store verification.

Batch size is provider capability data established by conformance benchmarks.
Adaptive splitting is allowed only when reconciliation proves effects and does
not replace malformed-input validation.

## Related Files

- Active graph hardening plan's `tools/graph/schema/`, writer, driver, journal,
  reconciliation, and barrier modules
- `code-tiny/tools/graph/writer/language_writer.py`
- `code-tiny/tools/graph/writer/query_contract.py`
- `code-tiny/tools/graph/driver/falkordb_driver.py`
- Neo4j driver compatibility path
- `code-tiny/tools/common/local_qdrant.py`
- storage generation gateway owned by the concurrency plan

## Implementation Steps

1. Reconcile the Phase 01 result contract with existing graph journal operation
   and reconciliation models.
2. Add structured node/vector/relationship mutation results and stable failure
   codes without duplicating journal state.
3. Integrate validated identity registry preflight and label barriers.
4. Implement indexed readback for declared identities/effects and explicit
   ambiguous outcomes.
5. Add vector-store point-ID/count/payload verification and graph/vector source
   ownership reconciliation.
6. Build provider conformance tests for duplicates, Unicode/control-containing
   parameters, missing endpoints, partial counts, timeouts, restart, and replay.
7. Benchmark safe batch item/byte limits and reject unsupported configurations.
8. Add a repository guard against mutation paths that bypass canonical
   operation/reconciliation contracts in required mode.

## Todo

- [ ] Count-only success cannot satisfy required-mode verification.
- [ ] Required endpoint preflight runs before relation mutation.
- [ ] Ambiguous writes reconcile by deterministic operation identity.
- [ ] Graph and vector effect results use the common phase-result contract.
- [ ] FalkorDB and Neo4j pass shared semantics and provider-specific limits.
- [ ] Direct/custom mutation paths are migrated or explicitly uncertified.
- [ ] Performance/readback overhead is measured against active plan gates.

## Risks

- Readback doubles query load. Require indexes, bounded identity batches, and
  measured provider strategies.
- Provider semantics differ. Keep one semantic contract with explicit
  capability/limit declarations, not branches hidden in analyzers.
- Replaying an ambiguous mutation duplicates effects. Retry only after
  deterministic reconciliation.

## Success Criteria

- Successful results prove intended persisted identities/effects.
- The `1000/985` fixture identifies exact unresolved/ambiguous rows and cannot
  publish a partial graph.
- Second identical execution is idempotent and yields the same fingerprints.
- Provider conformance detects the reproduced FalkorDB behavior before release.
- No required relation executes before its endpoint barriers are verified.
