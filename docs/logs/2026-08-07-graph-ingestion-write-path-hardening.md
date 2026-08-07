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

## Decision

The fix is automatic and tool-owned: schema readiness is a prerequisite of the
normal write path, not an operator runbook step. A provider-neutral manifest and
shared query compiler were chosen instead of hand-created indexes or a C++-only
patch because the unsafe query shape existed in multiple writers. Query timeouts
remain a circuit breaker rather than the performance fix because a timed-out
mutation can have an ambiguous commit outcome. Optional unresolved external
relationships are reported and skipped explicitly; required schema and identity
contract failures remain fail-closed.

## References

- Plan: [Graph ingestion write-path hardening](../../plans/260807-1202-graph-ingest-write-path-hardening/plan.md)
- Validation: [Graph ingest hardening validation](../../plans/260807-1202-graph-ingest-write-path-hardening/reports/validation-report.md)
- Commit: `6836cdb59d742cbafe99bc4934642a922a4ca4de`
- Commit: `74b55e335a65f3553ee76201c92c829e8c2805b2`
