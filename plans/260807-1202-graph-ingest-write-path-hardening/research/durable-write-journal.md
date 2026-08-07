---
type: research
date: 2026-08-07
---
# Research: Durable graph write journal

## Summary

The current C++ fix delays cross-buffer `File:INCLUDES:File` relationships
until all file nodes exist, but the delayed rows remain process memory. Graph
resume is disabled, so a crash causes the next analyzer attempt to replay the
whole graph. This is fail-closed and idempotent for the repaired edge, but it
is not an efficient durable recovery mechanism for a large scan.

The recommended replacement is a writer-local durable journal backed by
SQLite WAL. It records serializable graph mutation batches before their source
memory is released, enforces node-production barriers before relationships are
eligible, leases work to one consumer, reconciles ambiguous database outcomes,
and acknowledges a batch only after both graph integrity and journal state are
durable.

## Repository evidence

- `LanguageCodeWriter.write_batches()` accepts an opaque `write_fn` closure and
  optional in-memory offset state. It persists an offset only after a database
  call returns, but the operation itself cannot be reconstructed after process
  death.
- `LanguageCodeWriter.write_all()` defines the common node-before-relationship
  order, but many analyzers call it repeatedly for bounded stream buffers.
- `RelationshipGroup.state_key` contains the endpoint labels and relationship
  type, not a run fingerprint or payload identity; repeated stream buffers can
  therefore share an unsafe resume key.
- C++ explicitly disables graph resume and currently keeps deferred includes
  in memory after the final file-buffer flush.
- `incremental_sync.py` already owns project/root locking, immutable source
  inventory fingerprints, dirty/clean baseline publication, and child analyzer
  orchestration. A journal must extend these contracts instead of inventing a
  second scan-state authority.
- `cortex_harness/dev.py::_run_with_retry()` restarts the complete child command
  up to three attempts. A stable run identity must be passed across these
  attempts so they attach to one compatible incomplete journal.
- Python's bundled `sqlite3` is available in the workspace runtime. The
  inspected environment reports SQLite 3.51.0.

## Existing plan boundaries

- This plan owns writer-local mutation durability, operation identity, batch
  integrity, barriers, reconciliation, and journal recovery.
- `260807-0929-mcp-ingest-query-concurrency` owns request-level job admission,
  the single embedded-store owner, staging generations, and atomic publication.
  Its ingestion job queue must not duplicate writer mutation batches.
- `260718-2159-incremental-scan-reliability` remains the authority for scan
  scope, `ProjectRunLock`, source inventory, and dirty/clean baseline state.
- Parser-quality publication remains blocked while any required writer batch is
  pending, retrying, ambiguous, blocked, or dead-lettered.

## Proposed journal model

Use one local journal per scan scope under the resolved sync cache directory:

```text
<sync-cache>/graph-write-journal/<safe-project>_<scope-id>.sqlite3
<sync-cache>/graph-write-journal/artifacts/<run-id>/<sha256>.jsonl
```

SQLite stores metadata, state transitions, leases, barriers, and content
hashes. Large payloads live in immutable content-addressed artifacts so the
database does not grow with source-code blobs. An artifact is written to a
temporary file, flushed and fsynced, atomically renamed, and only then
referenced by an enqueued job transaction.

Core records:

| Record | Required fields |
| --- | --- |
| Run | run ID/key, scope, parser, source revision and snapshot, graph target/generation, schema fingerprint, query-shape version, status, timestamps, retention deadline |
| Batch | deterministic job ID, run ID, phase, operation key, sequence, artifact reference/hash, rows/bytes, expected count, status, attempt, fencing token, lease/retry timestamps, typed error |
| Barrier | run ID, barrier name, produced/drained counters, closed timestamp |
| Event | run/job IDs, event type, counters, attempt, elapsed time, typed error, timestamp |

The journal opens with foreign keys enabled, `journal_mode=WAL`, a bounded busy
timeout, and `synchronous=FULL` for enqueue and acknowledgement durability.
Schema upgrades use `PRAGMA user_version` and are transactional. Corrupt,
unreadable, disk-full, or unsupported journals fail closed before graph writes.

## Delivery and identity contract

Queue delivery is at-least-once. Graph effects are effectively-once only when
the mutation has deterministic identity and idempotent semantics.

```text
job_id = SHA-256(
  run_fingerprint,
  phase,
  operation_key,
  sequence,
  canonical_payload_sha256
)
```

The run fingerprint includes project/root scope, parser, source revision and
inventory snapshot, physical graph target or staging generation, schema
manifest fingerprint, and query-shape version. A mismatch quarantines the old
run as incompatible; it is never replayed into a new target or revision.

`MERGE`/deterministic `SET` operations are eligible. Increment-on-match writes,
opaque custom Cypher without reconciliation, and writes that report the input
length rather than matched database rows must be converted or blocked before
journal retries are enabled.

## Recovery state machine

```text
pending -> leased -> done
              |       ^
              v       |
          reconciling-+
              |
              +-> retry_wait -> pending
              +-> blocked -> dead_letter
```

- Claim uses `BEGIN IMMEDIATE`, a unique fencing token, and a bounded lease.
- Graph I/O happens outside the SQLite transaction.
- ACK succeeds only for the current fencing token.
- Expired leases are recovered to `pending` or `reconciling` on startup.
- A timeout after submission is ambiguous. Read back deterministic identities;
  ACK confirmed effects, retry only confirmed missing rows, otherwise block.
- Missing endpoints before the required node barrier wait. Missing endpoints
  after that barrier are integrity failures and do not enter an infinite retry.
- Transient connectivity/busy failures use bounded exponential backoff with
  jitter. Invalid contracts, corrupt payloads, incompatible fingerprints, and
  exhausted attempts are blocked/dead-lettered.

## Barrier contract

Producers open a run and durably enqueue node batches. They close label-specific
node production only after all possible nodes of that label, including inferred
and tail nodes, are enqueued. A barrier becomes drained only when every batch
covered by it is ACKed. Relationship jobs declare required source and target
barriers and are not claimable before those barriers drain.

For C++ includes, `File:INCLUDES:File` requires the `nodes:File:drained`
barrier. This replaces the memory-only deferred list and prevents the original
cross-buffer endpoint race without replaying all earlier work.

## Observability contract

Required events include `run_opened`, `run_resumed`, `batch_enqueued`,
`batch_leased`, `barrier_reached`, `batch_acked`, `retry_scheduled`,
`batch_reconciling`, `batch_blocked`, `batch_dead_lettered`, and
`queue_drained`. Logs include stable run/job IDs, parser, phase, operation,
attempt, rows, pending count, elapsed time, and typed error, but never payload
or source-code content.

## Primary references

- SQLite WAL: https://www.sqlite.org/wal.html
- SQLite transactions: https://www.sqlite.org/lang_transaction.html
- SQLite synchronous durability: https://www.sqlite.org/pragma.html#pragma_synchronous

## Unresolved validation gates

- The original approximately 20,186-file source root is not mounted in this
  workspace, so full-scale disk, fsync, resume, and retention evidence remains
  a rollout gate.
- The pending generation plan is required before journal recovery can also
  guarantee that readers never observe a partially built graph.
- Every direct/custom writer must be inventoried; enabling a global journal
  claim before bypass paths migrate would be misleading.
