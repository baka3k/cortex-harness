---
type: validation-report
date: 2026-08-07
status: implementation-validated
---

# Graph ingest hardening validation

## Root cause reproduced

The incident query used unlabeled endpoint matches. FalkorDB planned two all-node
scans beneath a Cartesian product, so the Python process waited while the
embedded database consumed a core. The displayed `READS_FROM 87/87` line was
the last completed batch, not the active query.

## Schema acceptance

A fresh disposable FalkorDBLite store created and verified all 156 canonical
identity indexes before mutation in 0.14 seconds. Reopening the same physical
store verified the manifest in 0.05 seconds and submitted no DDL. FalkorDB's
aggregated `db.indexes()` rows are expanded into independent property indexes
before exact readiness comparison.

## Query-plan and scale acceptance

A fixed 1,000-row `File -> Function` relationship batch was measured on a
disposable store. Each plan contained two endpoint index scans and contained no
`All Node Scan` or `Cartesian Product`.

| Total nodes | p95 seconds | Median seconds |
| ---: | ---: | ---: |
| 1,000 | 0.0421 | 0.0121 |
| 10,000 | 0.0127 | 0.0122 |
| 100,000 | 0.0142 | 0.0137 |
| 500,000 | 0.0176 | 0.0145 |

The 500k/100k p95 ratio was 1.242, within the required 2x ceiling. The 500k
p95 is more than 1,000x faster than the captured 18.6-34.1 second incident
range.

## Automated validation

- Final focused schema, writer, driver, framework, C++, topology, JP1, Shell,
  COBOL endpoint-closure, and static mutation reviews found no P0/P1 issues.
- `code-tiny/tests`: 61 tests, 24 subtests passed.
- Top-level non-COBOL suite: 486 tests, 185 subtests passed.
- Fresh-store index readiness, repeat verification, query-plan idempotency, and
  user-store isolation use disposable `.rdb` files.
- A subprocess terminal-deadline test proves a cancelled 15-second native call
  does not keep the process alive; a separate test proves the still-running call
  retains exclusive ownership of the embedded client.
- `--no-graph` is enforced before config normalization, at the CLI helper, and
  at the driver factory, so child project configuration cannot restore writes.
- Python compilation and `git diff --check` passed.

The repository-wide single `pytest` invocation still has unrelated collection
configuration failures: `doc-tiny` lacks its import path and duplicate
`test_falkordb_driver.py` module names collide. COBOL runtime/fixture tests also
fail independently because the bundled grammar returns no program tree. These
conditions predate and are outside this graph write-path change.

## Remaining environment gate

The original approximately 20,186-file C/Pro*C source tree is not present in
the current workspace and no running sync process exposes its root. Therefore
the full-source staging canary cannot be executed here. The disposable schema,
plan, scale, idempotency, and regression gates are complete; the source canary
requires the original repository path.
