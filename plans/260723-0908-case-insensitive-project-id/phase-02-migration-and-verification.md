# Phase 02: Backfill, Index, and Verify All MCP Paths

## Context

Changing the query contract before existing graph nodes and Qdrant points have `project_id_normalized` would hide legacy data. This phase upgrades existing records safely, adds the required indexes, and verifies equivalent behavior across provider and MCP modes.

## Requirements

- Provide an idempotent, bounded migration with dry-run and apply modes.
- Backfill only records with a non-empty raw `project_id`; do not invent scopes for missing values.
- Report case-collision groups, missing raw IDs, records needing updates, and records already correct.
- Add graph property indexes and a Qdrant keyword payload index for `project_id_normalized` where supported.
- Verify graph and Qdrant readiness before switching production MCP filters to the normalized field.
- Preserve rollback by leaving raw `project_id` untouched.

## Architecture

The migration reads each raw `project_id`, computes the shared Python comparison key, and writes only `project_id_normalized`. It performs no node merges, point-ID changes, deletions, collection moves, or project renames. Case-collision groups intentionally share a normalized key and are reported for operator visibility.

## Related Files

- New bounded migration utility under `code-tiny/scripts/` using the repository’s existing graph-provider and Qdrant configuration contracts.
- `code-tiny/scripts/setup_constraints.py` or the provider-neutral index setup seam selected by `neo4j-to-falkordb-migration`.
- `tests/test_qdrant_project_scope.py`
- `tests/test_explore_project_scope.py`
- Existing workflow, bridge-query, graph-driver, provider-parity, and unified-MCP tests discovered during Phase 01.
- Focused migration tests using fake/in-memory clients; live checks remain separately gated.

## Implementation Steps

1. Add a dry-run inventory that groups raw project IDs by normalized key and reports collisions such as `HIEP`, `hiep`, and `hiEp`.
2. Add idempotent graph backfill for Neo4j and FalkorDB through the provider abstraction stabilized by the dependency plan.
3. Add paginated Qdrant payload backfill that preserves vectors, point IDs, collection membership, and all existing payload fields.
4. Create provider-appropriate graph indexes and a Qdrant keyword payload index for the normalized field; make setup repeatable.
5. Add readiness checks comparing eligible raw-ID counts with normalized-field counts before rollout.
6. Extend tests to cover mixed-case legacy records, repeated migration runs, collision reporting, missing IDs, and partial failures.
7. Run focused project-scope, semantic retrieval, workflow, full-stack bridge, unified routing, and provider-parity regressions.
8. When services are available, run live smoke checks for graph-only, vector-only, combined, and frontend/backend-scoped queries using three case variants and record result-set equality.

## Todo

- [x] Implement dry-run/collision reporting.
- [x] Implement idempotent graph and Qdrant backfill.
- [x] Add normalized-field indexes and readiness checks.
- [x] Add legacy-data and provider-parity tests.
- [x] Run focused regressions; live smoke checks remain externally gated.

## Risks

- Large stores require pagination and bounded batches; expose progress and avoid loading all records into memory.
- A failed partial migration must be safely resumable because updates are deterministic and idempotent.
- Qdrant collections may use different vector layouts, but payload-only updates must not rewrite vector configuration or vector values.
- Live provider services may be unavailable; distinguish deterministic local test success from externally gated smoke checks.

## Success Criteria

- Dry-run and apply modes report deterministic counts and collision groups.
- Running the migration twice produces no additional changes on the second run.
- Indexed normalized-field counts match all eligible graph nodes and Qdrant points.
- Mixed-case input produces identical scoped results in every targeted MCP mode and on both graph providers.
- Raw stored IDs, identities, vectors, and non-project query behavior remain unchanged.
