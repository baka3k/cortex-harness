---
type: validation
date: 2026-08-07
---
# Validation: Durable graph write journal plan

## Summary

**Result: conditionally valid for implementation.** The plan has a concrete
storage contract, producer/consumer boundaries, crash semantics, retry
taxonomy, observability, testing, and cross-plan ownership. Implementation may
start with Phase 04A, but required-mode rollout remains gated by idempotency
conversion, complete mutation-path inventory, staged-generation integration,
and the unavailable full-source canary.

## Scope validation

| Question | Result |
| --- | --- |
| Shared or analyzer-local? | Shared writer contract; C++ is first canary. |
| Queue level? | Writer mutation journal, distinct from StoreGateway request queue. |
| Persistence? | Local SQLite WAL metadata plus immutable content-addressed artifacts. |
| Delivery guarantee? | At-least-once queue delivery with effectively-once deterministic effects. |
| Resume compatibility? | Exact source/target/schema/query/operation fingerprint match. |
| Publication gate? | Journal drained + graph validation; generation publication remains separate. |
| Failure default? | Fail closed, mark dirty, expose blocked/DLQ status. |

## Phase completeness

- **04A** defines schema, migrations, filesystem durability, artifact ordering,
  limits, corruption, and security.
- **04B** replaces opaque closures, establishes deterministic operations and
  barriers, and migrates writers without overstating coverage.
- **04C** defines lease/fencing, reconciliation, retry classification, stable
  run identity, dirty/clean gates, and fault injection.
- **04D** defines configuration, CLI/status, lifecycle logs, retention, provider
  parity, performance evidence, and rollout stages.

## Cross-plan validation

- The graph hardening plan remains the owner of writer mutation durability.
- The concurrency plan is updated bidirectionally: it owns request admission
  and staged publication and consumes the journal's drained/validated state.
- Existing incremental sync state remains authoritative for scan scope and
  baseline cleanliness.
- Phase 06 is explicitly blocked on durable-journal crash/resume canary evidence.

## Contract checks

- Stable IDs include run and payload fingerprints; repeated stream groups cannot
  collide on a label-only state key.
- Relationship barrier dependencies align with endpoint labels.
- Ambiguous writes reconcile before retry and cannot ACK on transport outcome
  alone.
- Fencing prevents a stale lease holder from mutating current job state.
- Non-idempotent and unreconciled operations are explicit blockers.
- No baseline or generation can publish with pending, retrying, ambiguous,
  blocked, or dead-lettered required work.
- Logs contain identifiers/counters but not source payload.

## Test and evidence gates

- Unit: schema migration, deterministic identity, artifact hash, enqueue/dedupe,
  claim/fencing, barriers, retry/DLQ, cleanup.
- Fault: kill before execute, graph commit before ACK, lease expiry, stale ACK,
  corruption, disk full, permission failure, source/schema mismatch.
- Integration: C++ 501-file forward include, shared writer batches, provider
  parity, dirty/clean gating, outer retry attachment.
- Performance: enqueue/ACK latency, queue bytes, disk headroom, restart latency,
  end-to-end overhead, and 20k canary.

## Required implementation order

1. Phase 04A contract/storage and failing tests.
2. Phase 04B operation/idempotency conversion and producer barriers.
3. Phase 04C consumer/reconciliation/resume and orchestration gates.
4. Phase 04D CLI/observability/fault/performance rollout.
5. Phase 06 full canary and publication.

## Blocking conditions

- Do not enable retries for `CALLS` or custom mutations until deterministic
  replay/readback is proven.
- Do not claim all-writer support until the direct/custom inventory is complete.
- Do not publish a staging generation from queue state alone.
- Do not mark the plan complete without the original-scale canary or an explicit
  documented exclusion approved by the user.

## Recommendation

Proceed with `/hi-craft` on Phase 04A only, then advance phase by phase after
each gate passes. The plan is implementation-ready at the contract level and
truthfully records the remaining environmental blocker.
