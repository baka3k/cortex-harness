# Graph journal recovery hardening — 2026-08-28

## Context

Required-mode graph recovery must survive an owner restart without replaying an
ambiguous mutation, crossing physical targets, or publishing incomplete state.
The hardening plan remains in progress: leased recovery is implemented, while
the run-level node-first lifecycle and rollout canaries remain open
(`plans/260807-1202-graph-ingest-write-path-hardening/phase-04-recovery-and-observability.md:93`,
`plans/260807-1202-graph-ingest-write-path-hardening/phase-04e-node-first-staging.md:465`).

## Change

Journal runs now bind to the resolved credential-free graph target and canonical
schema fingerprint. Recovery performs canonical schema preflight before it may
recover leases, inspect receipts, or construct a consumer
(`code-tiny/tools/graph/journal/config.py:252`,
`code-tiny/tools/graph/journal/consumer.py:246`). Expired submitted work returns
as `RECONCILING`; transient receipt failures back off without making the
mutation executable, while invalid contracts or non-unit receipt cardinality
block the batch (`code-tiny/tools/graph/journal/consumer.py:82`,
`code-tiny/tools/graph/journal/sqlite_store.py:1816`).

Operators receive payload-free status in sync summaries and `dev journal
status`, plus retention- and exact-scope-locked purge
(`code-tiny/tools/sync/incremental_sync.py:1900`, `cortex_harness/dev.py:2830`,
`cortex_harness/dev.py:2868`). Review fixes make purge crash-retryable by
persisting `run_purge_started`, removing artifacts before deleting run metadata,
and reporting `retry_purge`; they also retain graph receipts instead of applying
unsafe age-based deletion (`code-tiny/tools/graph/journal/sqlite_store.py:81`,
`code-tiny/tools/graph/journal/sqlite_store.py:2051`,
`code-tiny/tools/graph/journal/guard.py:34`).

Focused regression cases cover schema-before-recovery, crash-retryable purge,
ambiguous receipt reconciliation, legacy schema-fingerprint quarantine, and
credential-free malformed endpoint errors
(`tests/test_graph_write_journal_runtime.py:340`,
`tests/test_graph_write_journal.py:570`,
`tests/test_storage_effective_targets.py:205`).

## Impact

**Risk level: high.** The changes affect graph mutation replay, receipt
integrity, journal retention, owner restart, and atomic generation selection.
They reduce false ACK, blind retry, cross-target resume, and secret disclosure,
but do not complete Phase 04E. Open blockers are the durable
run-level node-production lifecycle and edge-release boundary, the live
file/remote/mixed/force-local parity and failure matrix, and the approximately
20,186-file C/Pro*C canary whose source root is unavailable
(`plans/260807-1202-graph-ingest-write-path-hardening/phase-04e-node-first-staging.md:476`,
`plans/260807-1202-graph-ingest-write-path-hardening/phase-04e-node-first-staging.md:495`,
`plans/260807-1202-graph-ingest-write-path-hardening/reports/validation-report.md:64`).

## Decision

Keep recovery fail-closed and receipt-driven: an uncertain submit remains
ambiguous until exact readback proves ACK or safe retry. Bind journals to the
effective target and canonical schema, retain owner-local SQLite WAL plus
immutable artifacts, and publish only through a pinned generation boundary.
Do not infer success from aggregate counts, fall back from a failed remote
target to local storage, delete receipts by age, or promote required mode before
the node-first lifecycle and live canaries pass.

## References

- Plan: [Graph ingestion write-path hardening](../../plans/260807-1202-graph-ingest-write-path-hardening/plan.md)
- Phase 04E: [Durable node-first staging and edge release](../../plans/260807-1202-graph-ingest-write-path-hardening/phase-04e-node-first-staging.md)
- Committed target, recovery, CLI, and fault-test foundation: `ed557b9083bb2cf097e916b67f1eba334522c1de`
- Committed failure-path journal summaries: `a08d4331f648715ad52e6027bd5166f25194e79f`
- Supporting storage-target log: `f3332c39c96bc558a0d755ef7ab192004a05cc41`
- Supporting storage-adapter plan completion: `e121cac2b38cb3112a8ad7cd9cc9a8ef025b5aac`
