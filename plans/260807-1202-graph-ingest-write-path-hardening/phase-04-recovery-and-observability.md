# Phase 04: Restart safety and truthful observability

## Context

Progress is emitted only after an awaited query returns and is duplicated via
logger plus `print`. The final visible line therefore identifies completed
work, while the actual in-flight query can run silently and without a local
embedded-driver deadline. Existing resume data cannot safely survive a schema
or query-grouping change. The interim C++ fix also keeps deferred cross-buffer
relationships only in memory, so a process exit still loses unfinished
operation state.

## Requirements

- One structured progress sink with global counters and correlation IDs.
- A running query becomes visible before execution and remains visible through
  bounded heartbeats.
- Cancellation, timeout, process restart, and ambiguous mutations reconcile to
  explicit run state.
- Incomplete runs do not publish success or advance incremental baselines.
- Parser quality is reported separately from write health.
- Validated graph batches are durable before producer memory is released.
- Compatible retries resume pending work; incompatible runs fail closed and are
  quarantined.

## Architecture

Introduce run/batch envelopes containing run ID, phase, global totals, schema
fingerprint, query-shape version, attempt, and idempotency key. Persist
serializable operations and immutable payload artifacts through a writer-local
SQLite WAL journal. Producers publish node barriers; Phase 04E adds a persisted
run-level boundary that keeps every edge ineligible until node production is
closed and all node jobs drain. One leased/fenced consumer executes eligible
work and reconciles ambiguous outcomes before ACK.
The driver emits lifecycle events through the same observer and enforces
version-aware limits where FalkorDB supports write timeouts/memory caps.

Detailed delivery is split into:

- [Phase 04A — durable journal contract and storage](phase-04a-journal-contract-and-storage.md)
- [Phase 04B — serializable operations, producers, and barriers](phase-04b-producer-operations-and-barriers.md)
- [Phase 04C — leased consumer, reconciliation, and resume](phase-04c-consumer-reconciliation-and-resume.md)
- [Phase 04D — CLI, observability, validation, and rollout](phase-04d-cli-observability-validation-rollout.md)
- [Phase 04E — durable node-first staging and edge release](phase-04e-node-first-staging.md)

## Related files

- `code-tiny/tools/graph/writer/language_writer.py`
- `code-tiny/tools/graph/driver/falkordb_driver.py`
- `code-tiny/tools/cplus/cplus_analyzer.py`
- `code-tiny/tools/sync/incremental_sync.py`
- Run-state/checkpoint and logging helpers under `code-tiny/tools/common/`

## Implementation steps

1. Define stable phases and event fields for schema, parse, node write,
   relationship write, validation, publication, cancellation, and failure.
2. Emit `batch_started` before awaiting the driver; emit periodic
   `query_running` heartbeats with elapsed time and current label triple; emit
   exactly one terminal event.
3. Replace per-500-file progress denominators with full-run totals and explicit
   buffer/batch subtotals. Remove double logger/`print` emission.
4. Add capability/version discovery for FalkorDB timeout and memory controls.
   Bound calls when supported and expose configured/effective values.
5. Model a timeout as ambiguous for mutations until idempotent readback proves
   the outcome; do not retry blindly.
6. Persist incomplete/failed/cancelled status and prevent baseline/generation
   publication. Validate schema/query fingerprints before resume.
   Replace buffer-local offsets with absolute durable chunks keyed by project,
   root, revision, parser, schema, and query version; if this cannot be proven
   safe before staged generations land, disable resume explicitly rather than
   pretending the imported state path is active.
7. Add a supported doctor/status path that shows current query, elapsed time,
   DB health, index state, and the safe recovery action.
8. Split parse summary into `ERROR`, `MISSING`, encoding/fallback, alternate
   parser, and compile-command coverage without changing parser semantics.
9. Replace disabled/offset-only resume with the Phase 04A-04D durable journal.
   Do not persist opaque closures; require versioned operation specifications.
10. Gate relationships on drained endpoint-label barriers, then strengthen the
    invariant in Phase 04E so all edge families require the drained run-level
    node barrier. Gate baseline or generation publication on a drained,
    validated journal.

## Todo

- [x] Define run, batch, checkpoint, and lifecycle event contracts.
- [x] Emit single-sink global progress and in-flight heartbeats.
- [ ] Add version-aware database limits and ambiguous-write reconciliation.
- [x] Prevent incomplete runs from publishing or advancing baselines.
- [x] Reject incompatible resume fingerprints.
- [x] Replace false buffer-local resume offsets or disable resume fail-closed.
- [x] Separate parser-quality and graph-write health reporting.
- [ ] Implement Phase 04A durable journal storage and artifact contract.
- [ ] Implement Phase 04B serializable operations and producer barriers.
- [ ] Implement Phase 04C leased execution, reconciliation, and resume.
- [ ] Implement Phase 04D CLI/status, fault validation, and guarded rollout.
- [ ] Implement Phase 04E durable node-first staging and edge release.

## Risks

A client timeout does not prove a write was rolled back, and FalkorDB may need
additional time to roll back a timed-out mutation. Heartbeats must not submit
competing expensive database queries on the single embedded execution lane.

## Success criteria

An operator can always identify the active phase/query and elapsed time; no
active database call is silent beyond the heartbeat interval. Interrupted
compatible runs resume unfinished journal work without duplicating effects or
publishing partial state; incompatible or unreconciled work fails closed.
