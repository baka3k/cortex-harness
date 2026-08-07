# Phase 04C: Leased consumer, reconciliation, and resume

## Context

The outer CLI currently restarts the entire analyzer. A durable journal must
attach retries to one compatible run, recover expired work safely, and avoid
blindly replaying a mutation whose database outcome is unknown.

## Requirements

- Run one journal consumer per resolved scan/physical-target scope.
- Use leases plus fencing so an expired worker cannot ACK a newly leased job.
- Classify retryable, ambiguous, integrity, incompatible, and terminal errors.
- Reconcile ambiguous writes by deterministic graph readback before retry.
- Automatically resume only a fingerprint-compatible incomplete run.
- Block baseline/generation publication while any required job is unfinished.

## Architecture

Add `consumer.py`, `retry.py`, and `reconcile.py` to the journal package. The
consumer transactionally claims one eligible batch, commits the lease, executes
graph I/O outside SQLite locks, and then transactionally ACKs, schedules retry,
or blocks using the lease fencing token.

Run compatibility requires equal project/root scope, parser, source revision,
inventory snapshot, graph target/generation, schema fingerprint, operation
versions, and query-shape version. Incompatible runs are quarantined and remain
visible to status/doctor; they are never silently deleted or replayed.

## Related Files

- New: `code-tiny/tools/graph/journal/consumer.py`
- New: `code-tiny/tools/graph/journal/retry.py`
- New: `code-tiny/tools/graph/journal/reconcile.py`
- `code-tiny/tools/graph/writer/language_writer.py`
- `code-tiny/tools/graph/driver/{base,falkordb,neo4j}_driver.py`
- `code-tiny/tools/sync/incremental_sync.py`
- `cortex_harness/dev.py`

## Implementation Steps

1. Implement eligibility queries using barrier state, `next_attempt_at`, lease
   expiry, and deterministic sequence/fairness rules.
2. Claim with `BEGIN IMMEDIATE`, unique fencing token, bounded lease, and a
   durable `batch_leased` event.
3. Execute outside the journal transaction and renew only at safe boundaries;
   never submit duplicate graph work from a heartbeat.
4. Reconcile timeout/cancel/connection-loss outcomes with operation-specific
   deterministic readback. ACK confirmed effects and retry only confirmed
   missing effects.
5. Implement bounded exponential backoff with injected clock/jitter for tests.
   Missing endpoints after drained barriers, invalid contracts, corruption, and
   exhausted attempts become blocked/dead-lettered.
6. Recover expired leases on open. Use fencing so a late old consumer cannot
   overwrite the recovered job state.
7. Derive or pass a stable sync/run identity across `dev.py` retry attempts and
   child analyzers. A later intentional scan creates a new run identity.
8. Make `incremental_sync.mark_clean()` require every parser journal drained,
   graph validation complete, and no blocked/dead-lettered work. Failures use
   the existing dirty-state contract.
9. Add process-kill fault injection before execute, after graph commit/before
   ACK, during reconciliation, after barrier close, and before clean publish.

## Todo

- [ ] Leased claim and fencing are implemented.
- [ ] Retry taxonomy and bounded scheduling are implemented.
- [ ] Ambiguous graph outcomes reconcile without blind mutation retry.
- [ ] Compatible startup resume and incompatible quarantine are implemented.
- [ ] Stable identity crosses outer CLI retries and analyzer subprocesses.
- [ ] Dirty/clean and generation-publication gates consume journal state.

## Risks

There is no distributed transaction between SQLite and FalkorDB/Neo4j. Readback
must be operation-specific and cannot infer success from a socket outcome.
Wall-clock leases can be affected by system time changes; fencing, bounded
leases, and single-scope ownership remain mandatory. Journal recovery does not
by itself prevent readers from seeing partial in-place writes; staged-generation
publication remains a separate dependency.

## Success Criteria

After a forced process exit at every batch boundary, the next compatible attempt
continues from durable pending/ambiguous work without reparsing or replaying
ACKed graph effects. A stale or incompatible attempt cannot claim or ACK current
work. Baselines and generations remain unpublished until the journal drains and
validation succeeds.
