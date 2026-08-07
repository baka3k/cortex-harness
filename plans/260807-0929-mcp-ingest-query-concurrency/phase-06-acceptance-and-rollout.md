# Phase 06: Mixed-load acceptance and guarded rollout

## Context

The report requires evidence before enabling no-downtime ingestion or raising
query concurrency. Functional tests alone cannot establish that the shared
synchronous drivers behave safely under mixed load.

## Requirements

- Exercise same-target and isolated-target workloads on temporary stores.
- Prove no partial-generation reads, no lease bypass, bounded queues, and
  successful rollback after failure.
- Establish SLOs and capacity defaults from measurements.
- Keep a reversible rollout and explicit server-mode escalation criteria.
- Validate smooth user journeys under warm/cold start, queue saturation,
  ingestion, cancellation, shutdown, restart, and degraded storage.

## Related files

- New: `tests/test_storage_concurrency_stress.py`,
  `tests/test_generation_faults.py`, and benchmark/fault helpers under
  `scripts/` or `tests/fixtures/`.
- Existing storage, graph-driver, Qdrant, incremental-sync, MCP flow,
  lifecycle, and retrieval validation suites.

## Implementation steps

1. Run reader matrix: 1/8/32/128 clients; record p50/p95/p99, queue delay,
   timeouts, event-loop lag, executor saturation, CPU, RSS, disk I/O,
   lock wait/hold, thread/process/native-thread count, and cold/warm latency.
2. Run ingestion matrix: 1/2/4 submissions, same physical target versus
   isolated targets; verify dedupe and writer fairness.
3. Run mixed reader/writer, generation swap, cancellation, crash, ambiguous
   commit, rollback, and restart scenarios.
4. Verify one-worker enforcement, no inherited store handles, lock order, no
   await/I/O under state locks, reserved health capacity, graceful drain, and
   recovery after forced exit at every job/publication stage.
5. Run user-flow acceptance: fast submit, duplicate submit, status/follow,
   bounded wait, cancel-before-run, cancel-during-sync-call, query while
   ingesting, `require_fresh`, overload with retry metadata, warmup, and
   zero-hit versus failed-query distinction.
6. Run live representative graph/vector probes after every publication and
   compare logical results across generations.
7. Run a mixed-load soak long enough to detect queue starvation, reference,
   file-descriptor, thread/process, model-cache, and RSS growth; document the
   chosen duration and dataset size in the report. Exercise both item-count
   and estimated-byte queue limits plus query-cost/result-size caps.
8. Enable the feature for one disposable/canary target; automatically fall
   back to pause/restart on health, manifest, lease, or SLO failure.
9. Promote `balanced` and enable by default only when acceptance evidence is
   recorded in the plan-scoped report; otherwise retain the `safe` profile
   and pause/restart fallback. Record the measured capacities and exact
   environment profile in the report.

## Risks

Passing a small fixture can hide storage-size, embedder, or disk-I/O effects.
Include a realistic fixture and report peak staging footprint, not only latency.

## Success criteria

All acceptance gates pass with zero lease bypasses, zero partial-generation
reads, bounded overload behavior, successful rollback, and documented SLOs.
The user-facing flows remain responsive and truthful under load: no hidden
hang, raw lock error, false empty result, silent cancellation, or manual lock
cleanup is required.
