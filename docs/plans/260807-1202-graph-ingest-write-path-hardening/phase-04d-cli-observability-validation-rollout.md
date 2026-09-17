# Phase 04D: CLI, observability, validation, and rollout

## Context

Durable recovery is operationally useful only when users can see whether a run
is new, resumed, waiting, retrying, reconciling, blocked, drained, or
incompatible. Rollout must prove that journaling does not create unbounded disk,
latency, or hidden poison-job loops.

## Requirements

- Compatible incomplete runs resume automatically in normal `dev sync code`.
- Status and summaries expose queue state without opening graph payloads.
- Cleanup is explicit, retention-bounded, scope-safe, and never removes an
  active or leased journal/artifact.
- Rollout starts with C++ canaries, expands to shared writers, then custom paths.
- Provider parity, crash recovery, disk behavior, and large-scale overhead are
  acceptance gates.

## Architecture

Extend the existing sync summary and CLI rather than adding a second daemon.
Default journal retention is seven days after successful drain; blocked and
incompatible runs remain visible for the configured retention window and
require an explicit safe purge/acknowledgement path. The future StoreGateway
may own execution, but it consumes the same journal contract.

Required lifecycle events:

```text
run_opened run_resumed batch_enqueued batch_leased barrier_reached
batch_acked retry_scheduled batch_reconciling batch_blocked
batch_dead_lettered queue_drained run_quarantined journal_purged
```

Every event includes run/job IDs, parser, phase, operation, attempt, rows,
pending count, elapsed time, and typed error; logs never include source payload.

## Related Files

- `code-tiny/tools/sync/incremental_sync.py`
- `code-tiny/tools/common/incremental_sync_state.py`
- `cortex_harness/dev.py`
- Sync summary/status and doctor tests
- Journal and writer integration/fault suites
- `plans/260807-0929-mcp-ingest-query-concurrency/`

## Implementation Steps

1. Add validated configuration for journal directory, auto-resume policy,
   limits, lease, retry attempts/backoff, retention, and rollout mode.
2. Extend analyzer commands/environment with stable run metadata and journal
   configuration; preserve it across outer retries.
3. Extend sync summaries with run status, resumed flag, produced/ACKed/pending/
   retrying/reconciling/blocked counts, bytes, oldest age, and next action.
4. Add status/doctor output and explicit scope-safe purge/quarantine handling.
   Destructive removal requires an inactive run and exact resolved target.
5. Emit one structured event sink and assert no duplicate/conflicting progress
   lines from analyzer, writer, and wrapper layers.
6. Add unit/integration tests for schema migration, enqueue atomicity, lease
   exclusion, fencing, crash points, commit-before-ACK, backoff, dead-letter,
   incompatible fingerprints, corruption, disk full, retention, and cleanup.
7. Run provider parity and performance fixtures, including the 501-file include
   boundary and fixed 1k/10k/100k-node workloads.
8. Roll out `off -> cplus-canary -> shared-shadow -> shared-required -> all-required`.
   Shadow mode verifies operation serialization without claiming recovery.
9. Complete the approximately 20k-file disposable/staging canary. Measure
   enqueue/ACK latency, fsync cost, journal/artifact peak bytes, restart time,
   graph parity, and end-to-end overhead before Phase 06 publication.

## Todo

- [x] Configuration, summaries, status, and safe cleanup are implemented.
- [x] Structured lifecycle logs are complete and non-duplicated.
- [x] Crash, ambiguity, corruption, disk, and retention tests pass.
- [x] C++ and shared-writer canaries pass provider parity and performance gates.
- [x] Full-source canary evidence is recorded with the explicit original-source waiver.
- [x] Required mode is enabled only after every claimed mutation path migrates.

## Risks

The journal can contain source-derived data and may grow quickly if consumers
are blocked. Status and cleanup must remain usable when the graph is unhealthy.
Outer retries can amplify load if they start new runs instead of attaching to
the same one. The full-source root remains unavailable in this workspace, so
required-mode rollout cannot be approved here.

## Success Criteria

Users can distinguish queue wait, execution, retry, reconciliation, blocked
work, and completion from logs and status alone. A compatible retry resumes in
seconds and performs only unfinished work. Journal overhead stays within the
recorded acceptance budget, storage remains bounded, and no failed/partial run
advances the baseline or active generation.
