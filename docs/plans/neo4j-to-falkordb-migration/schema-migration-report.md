# Schema Migration Report

status: completed; embedded catalog setup verified
updated: 2026-08-06

## Implementation

`code-tiny/scripts/setup_constraints.py` now supports both providers.

- Neo4j retains named idempotent DDL.
- FalkorDB converts the same canonical declarations into native range,
  full-text, and unique-constraint API calls.
- Composite property order is preserved.
- Multi-label Neo4j full-text indexes are expanded per FalkorDB label.
- A prerequisite range index is ensured before every unique constraint.
- `list_constraints()` is polled until `OPERATIONAL`, `ACTIVE`, `ENABLED`, or
  `READY`.
- `FAILED`, `FAILURE`, or `ERROR` status raises `RuntimeError`; timeout raises
  `TimeoutError`.
- Only recognized already-exists errors are treated as idempotent success.

`code-tiny/scripts/setup_graph_project.py` uses the same FalkorDB constraint
helper for `Project.project_id` and `Repository.name` before the hierarchy
MERGE.

## Tests

`code-tiny/tests/test_falkordb_schema_migration.py` verifies:

- DDL label/property extraction;
- composite index parsing;
- pending-to-operational polling;
- failed status propagation;
- per-label full-text expansion;
- provider-neutral project hierarchy setup.

## Embedded Validation Completed

- Schema setup ran twice against the same packaged embedded database without
  already-exists failures.
- Each run reported 102 constraints operational, 143 range indexes ensured,
  and 242 full-text indexes ensured.
- Constraint pending, operational, failure, and timeout behavior is covered by
  schema tests. Cross-process concurrent writers are rejected by the owner
  lease contract instead of being stress-tested against one embedded file.
