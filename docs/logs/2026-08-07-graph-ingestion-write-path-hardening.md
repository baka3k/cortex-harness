# Graph ingestion write-path hardening — 2026-08-07

## Context

`dev sync code` repeatedly appeared to stop after a completed relationship line
such as `relations:READS_FROM 87/87`. The process was actually waiting on a later
FalkorDB query whose unlabeled endpoint matches produced two all-node scans and a
Cartesian product. Required identity indexes were also created after streaming,
too late to protect those relationship writes. The corrective scope and rollout
gates are captured in the [implementation plan](../../plans/260807-1202-graph-ingest-write-path-hardening/plan.md).

## Change

- Added a validated, fingerprinted manifest for the 156 canonical graph identity
  indexes (`code-tiny/tools/graph/schema/manifest.py:15`,
  `code-tiny/tools/graph/schema/manifest.py:111`).
- Added fail-closed schema preflight that inspects current state, creates only
  missing indexes, waits for exact operational readiness, and bounds every
  inspection or DDL operation (`code-tiny/tools/graph/schema/preflight.py:105`,
  `code-tiny/tools/graph/schema/preflight.py:184`,
  `code-tiny/tools/graph/schema/preflight.py:241`). Writers invoke preflight
  before the first batch, so operators do not need to create indexes manually
  (`code-tiny/tools/graph/writer/language_writer.py:90`,
  `code-tiny/tools/graph/writer/language_writer.py:136`).
- Replaced generic endpoint lookup with validated, label-qualified relationship
  compilation, making both endpoint identity indexes usable
  (`code-tiny/tools/graph/writer/query_contract.py:12`,
  `code-tiny/tools/graph/writer/query_contract.py:66`).
- Added truthful in-flight progress, bounded cancellation reconciliation, typed
  ambiguous-write handling, and exclusive ownership while a cancelled native
  FalkorDB operation is still running
  (`code-tiny/tools/graph/writer/language_writer.py:140`,
  `code-tiny/tools/graph/writer/language_writer.py:175`,
  `code-tiny/tools/graph/driver/falkordb_driver.py:379`,
  `code-tiny/tools/graph/driver/falkordb_driver.py:416`).
- Enforced graphless operation before configuration normalization and again at
  driver construction, preventing child configuration from restoring graph
  writes (`code-tiny/tools/graph/cli.py:31`,
  `code-tiny/tools/graph/cli.py:149`,
  `code-tiny/tools/graph/core/factory.py:75`).
- Phase 04A added immutable compatibility metadata plus canonical run and job
  identities, so equivalent enqueue requests resolve to the same durable work
  item while incompatible active runs are quarantined
  (`code-tiny/tools/graph/journal/models.py:94`,
  `code-tiny/tools/graph/journal/identity.py:26`,
  `code-tiny/tools/graph/journal/identity.py:37`,
  `code-tiny/tools/graph/journal/sqlite_store.py:413`).
- Added a local SQLite journal using WAL, full synchronous durability, foreign
  keys, bounded storage, transactional barriers, leased claims, fenced ACKs,
  reconciliation, retries, and terminal states
  (`code-tiny/tools/graph/journal/sqlite_store.py:247`,
  `code-tiny/tools/graph/journal/sqlite_store.py:545`,
  `code-tiny/tools/graph/journal/sqlite_store.py:784`,
  `code-tiny/tools/graph/journal/sqlite_store.py:944`,
  `code-tiny/tools/graph/journal/sqlite_store.py:1014`,
  `code-tiny/tools/graph/journal/sqlite_store.py:1052`).
- Large payloads now stream to owner-only, content-addressed JSONL artifacts
  with fsync, atomic publication, and hash verification; focused tests cover
  deduplication, barrier ordering, stale fences, reconciliation recovery,
  fail-closed corruption handling, retention, and redacted events
  (`code-tiny/tools/graph/journal/artifacts.py:289`,
  `code-tiny/tools/graph/journal/artifacts.py:359`,
  `tests/test_graph_write_journal.py:176`,
  `tests/test_graph_write_journal.py:222`,
  `tests/test_graph_write_journal.py:263`,
  `tests/test_graph_write_journal.py:292`,
  `tests/test_graph_write_journal.py:359`,
  `tests/test_graph_write_journal.py:532`).

## Impact

**Risk level: high.** The shared graph ingestion path now prevents the original
full-graph relationship lookup, aborts before mutation when required schema is
not ready, preserves ambiguous timeout state for reconciliation, and keeps the
last visible progress line aligned with active database work. The change applies
across the shared language writer and migrated framework, topology, Android,
TypeScript, Shell, JP1, COBOL, and C/C++ paths, with Neo4j compatibility retained.

A fresh disposable FalkorDBLite store created and verified all 156 indexes in
0.14 seconds; a repeat preflight took 0.05 seconds and issued no DDL. A 1,000-row
relationship batch used two index scans with no all-node scan or Cartesian
product at every tested graph size. At 500,000 nodes, p95 was 0.0176 seconds,
the 500k/100k ratio was 1.242, and the result was more than 1,000 times faster
than the captured 18.6–34.1 second incident range.

Validation passed 61 tests plus 24 subtests in `code-tiny/tests` and 486 tests
plus 185 subtests in the top-level non-COBOL suite. Three final focused reviews
covering schema, runtime, and writer/query behavior approved the implementation
with no P0/P1 findings. The remaining rollout gate is the full canary against
the original approximately 20,186-file C/Pro*C repository, whose source root is
not mounted or discoverable in this workspace.

Phase 04A retains the high risk level because this state machine will sit ahead
of graph mutations once integrated. It provides crash-reopen durability,
idempotent enqueue, stale-worker rejection, and explicit ambiguous-outcome
preservation without yet changing production writers; writer integration and
backend readback reconciliation remain Phase 04B and Phase 04C work.

## Decision

The fix is automatic and tool-owned: schema readiness is a prerequisite of the
normal write path, not an operator runbook step. A provider-neutral manifest and
shared query compiler were chosen instead of hand-created indexes or a C++-only
patch because the unsafe query shape existed in multiple writers. Query timeouts
remain a circuit breaker rather than the performance fix because a timed-out
mutation can have an ambiguous commit outcome. Optional unresolved external
relationships are reported and skipped explicitly; required schema and identity
contract failures remain fail-closed.

For Phase 04A, a local SQLite WAL journal and immutable content-addressed
artifacts were chosen over a new external queue so metadata transitions stay
transactional without placing large source-derived payloads in the database.
Deterministic identities and fencing protect replay and ownership, while an
expired ambiguous write remains reconciling rather than being re-executed or
described as exactly-once (`code-tiny/tools/graph/journal/sqlite_store.py:1262`).
Cleanup consequently requires a terminal run, elapsed retention, and confirmed
exact-scope ownership (`code-tiny/tools/graph/journal/sqlite_store.py:1379`).

## References

- Plan: [Graph ingestion write-path hardening](../../plans/260807-1202-graph-ingest-write-path-hardening/plan.md)
- Phase 04A: [Journal contract and storage](../../plans/260807-1202-graph-ingest-write-path-hardening/phase-04a-journal-contract-and-storage.md)
- Validation: [Graph ingest hardening validation](../../plans/260807-1202-graph-ingest-write-path-hardening/reports/validation-report.md)
- Commit: `6836cdb59d742cbafe99bc4934642a922a4ca4de`
- Commit: `74b55e335a65f3553ee76201c92c829e8c2805b2`
- Commit: `d4abf38667aaacfbf29ce13264f616765f82c9f2`
