---
type: red-team
date: 2026-08-07
---
# Red team: Durable graph write journal

## Summary

**Verdict: CAUTION, acceptable to implement only behind fail-closed rollout
gates.** SQLite WAL is appropriate for this single-host local runtime, but a
queue alone does not create exactly-once graph mutations or isolate readers
from an in-place partial build. The plan is viable because it explicitly uses
at-least-once delivery, fencing, operation-specific readback, and staged
generation publication as separate contracts.

## Critical findings

### 1. False exactly-once semantics

SQLite ACK and a FalkorDB/Neo4j mutation cannot commit in one transaction. A
crash after graph commit and before journal ACK will replay the job.

**Required mitigation:** deterministic operation identities, idempotent graph
semantics, and readback reconciliation. Increment-on-match `CALLS` behavior and
custom Cypher that cannot reconcile must be converted or blocked before
journal mode is enabled.

### 2. Opaque closures cannot be durable jobs

The current writer stores behavior in a Python closure. Serializing only rows
and a label does not prove which query or semantics should execute after an
upgrade.

**Required mitigation:** versioned allowlisted operation specifications and a
query-shape fingerprint. Never serialize arbitrary Cypher/code for replay.

### 3. Lease recovery without fencing corrupts state

An expired worker may return after a new worker claims the same job and then
ACK or fail the new lease.

**Required mitigation:** unique fencing token per claim; every ACK/retry/block
transition must match the current token. One project/root lock remains, but
fencing is still required for process-restart and future gateway ownership.

### 4. Queue drain does not equal publication safety

If ingestion mutates the active graph in place, users may still query partial
state while the journal is healthy.

**Required mitigation:** keep this plan's mutation durability separate from
the StoreGateway plan's staging-generation and atomic publication boundary.
`queue_drained` is necessary but not sufficient to publish.

## High findings

### 5. Barrier closure can recreate missing endpoints

C++ inferred/tail nodes are produced after normal file buffers. Closing a
global node barrier at the end of each `write_all()` call is incorrect.

**Mitigation:** analyzer-level produced/drained label barriers with explicit
closure only after every possible node producer completes.

### 6. Journal durability order can create dangling jobs or artifacts

A crash between artifact creation and job enqueue, or enqueue before artifact
durability, can leak data or create an unreplayable batch.

**Mitigation:** temp artifact -> fsync -> atomic rename -> enqueue transaction.
Orphan artifacts are collected only after journal/reference/age checks.

### 7. Poison jobs can create infinite retry and disk growth

Missing endpoints after node barriers, invalid query versions, corrupt
artifacts, or deterministic integrity mismatches are not transient.

**Mitigation:** typed retry classifier, bounded attempts/backoff, blocked/DLQ
states, item+byte limits, disk-headroom admission, and actionable status.

### 8. Retry identity can accidentally cross revisions or targets

The outer wrapper currently restarts a command, while analyzer run IDs may be
regenerated. Conversely, a too-broad deterministic ID could replay old work
into a new source revision or staging generation.

**Mitigation:** stable invocation identity across retry attempts plus strict run
fingerprint equality for source snapshot, parser, target/generation, schema,
and operation/query versions.

## Medium findings

### 9. Journal contains sensitive source-derived payloads

Artifacts may contain code, literals, paths, or configuration content.

**Mitigation:** local-only path, owner-only permissions, no payload logging,
bounded retention, exact-scope cleanup, and security tests.

### 10. WAL/filesystem assumptions may not hold on network storage

SQLite WAL depends on same-host shared-memory filesystem behavior. A user-
overridden cache on an unsupported network filesystem may be unsafe.

**Mitigation:** resolve a local application-owned path or fail closed when the
placement cannot meet the journal contract. Do not silently downgrade
durability.

### 11. Status/cleanup can race an active consumer

Removing WAL/artifacts while a worker holds a lease risks corruption.

**Mitigation:** status is read-only; purge requires exact scope, inactive run,
no current leases, and the existing ownership lock. Never glob-delete journals.

### 12. Claiming universal coverage before custom writers migrate

Several writer families and direct mutation paths bypass
`LanguageCodeWriter`.

**Mitigation:** inventory and static guard. Rollout modes must say C++ canary,
shared writer, or all writers accurately; required mode is blocked until every
claimed path is migrated or rejected.

## Acceptance conditions

- No P0/P1 finding is deferred past required-mode rollout.
- Crash injection covers graph-commit-before-ACK and stale-worker fencing.
- Non-idempotent operations fail tests before journal retries are enabled.
- Queue drain, graph validation, and generation publication remain distinct
  logged states.
- The full-source canary records journal bytes, disk headroom, fsync overhead,
  restart latency, and parity.

## Unresolved Questions

- The 20k source root is unavailable, so required-mode scale acceptance remains
  externally blocked.
- StoreGateway/generation implementation is pending; until it lands, the
  journal improves recovery but cannot promise consistent reads during an
  in-place build.
