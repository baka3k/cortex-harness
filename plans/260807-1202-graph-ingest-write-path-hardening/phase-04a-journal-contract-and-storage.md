# Phase 04A: Durable journal contract and storage

## Context

The current offset callback cannot reconstruct an opaque write closure after a
process restart. Durable recovery first needs an explicit run/batch contract,
stable operation identity, and local storage whose state transitions survive a
crash.

## Requirements

- Use a local SQLite WAL journal with no new external service dependency.
- Scope runs by source snapshot, physical graph target/generation, parser,
  schema fingerprint, and query-shape version.
- Store large canonical payloads in immutable content-addressed artifacts.
- Make enqueue, claim, retry, ACK, barriers, and terminal states transactional.
- Fail closed on incompatible schema, corruption, disk exhaustion, unsafe
  filesystem placement, or artifact hash mismatch.
- Protect source-derived payloads with owner-only filesystem permissions and
  bounded retention.

## Architecture

Add a focused package under `code-tiny/tools/graph/journal/`:

```text
models.py       immutable run, batch, barrier, status and error contracts
sqlite_store.py schema migration and transactional repository
artifacts.py    canonical artifact creation, verification and cleanup
identity.py     run fingerprint and deterministic job/fencing identities
```

Use `runs`, `batches`, `barriers`, and `events` tables. Configure foreign keys,
WAL, bounded busy timeout, and full synchronous durability. Claims use
`BEGIN IMMEDIATE`; ACK/update statements include both `job_id` and the current
fencing token.

## Related Files

- New: `code-tiny/tools/graph/journal/`
- `code-tiny/tools/common/analyzer_cache.py`
- `code-tiny/tools/common/sync_scope.py`
- `code-tiny/tools/common/incremental_sync_state.py`
- New focused tests under `code-tiny/tests/` and `tests/`

## Implementation Steps

1. Define versioned enums and immutable contracts for run status, batch status,
   operation phase, barriers, retry class, and terminal error codes.
2. Define a stable run fingerprint and deterministic job ID from canonical
   metadata and payload hashes.
3. Implement the SQLite schema and transactional migrations using
   `PRAGMA user_version`.
4. Configure WAL, `synchronous=FULL`, foreign keys, busy timeout, and bounded
   checkpoint/size behavior; reject non-local or unsupported placements.
5. Implement artifact temp-write, permission, fsync, atomic rename, hash
   verification, reference counting, and retention-safe cleanup.
6. Implement atomic open/create, enqueue/dedupe, lease with fencing, ACK,
   retry, block, dead-letter, barrier, event, status, and reopen recovery APIs.
7. Add disk-headroom and item/byte admission limits before accepting a run.
8. Add schema migration, corruption, disk-full, permission, crash-reopen, and
   deterministic-dedupe tests.

## Todo

- [x] Journal contracts and schema are reviewed.
- [x] SQLite open/migration and durability policies are implemented.
- [x] Artifact persistence and hash verification are implemented.
- [x] Transactional state transitions and fencing are implemented.
- [x] Storage failure and crash-reopen tests pass.

## Risks

SQLite WAL requires local same-host filesystem semantics. Payloads may contain
source code or sensitive literals, so permissions, retention, and log redaction
are mandatory. A SQLite commit cannot be atomic with a graph-store mutation;
Phase 04C must reconcile ambiguous outcomes rather than claim exactly-once
delivery.

## Success Criteria

A killed process can reopen a version-compatible journal without losing an
ACKed transition or exposing a partially written artifact. Duplicate enqueue
requests return the same job identity. No graph mutation is allowed when its
durable enqueue did not commit.
