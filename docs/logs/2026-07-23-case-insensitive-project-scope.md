# Case-Insensitive Project Scope — 2026-07-23

## Context

Project-scoped MCP searches compared `project_id` values exactly, so identifiers such as `HIEP`, `hiep`, and `hiEp` could select different graph or vector records. The work follows the [case-insensitive project ID plan](../../plans/260723-0908-case-insensitive-project-id/plan.md) while preserving existing stored and returned identities.

## Change

- A shared scope contract derives Unicode-aware `casefold()` lookup keys, enriches nested write payloads, prepares normalized graph parameters, and builds exact Qdrant filters without changing raw `project_id` values (`code-tiny/tools/common/project_scope.py:29`, `code-tiny/tools/common/project_scope.py:35`, `code-tiny/tools/common/project_scope.py:53`, `code-tiny/tools/common/project_scope.py:70`).
- Neo4j and FalkorDB query preparation now supplies normalized sibling parameters, vector payloads persist `project_id_normalized`, and full-stack bridge predicates independently scope frontend and backend projects through the normalized field (`code-tiny/tools/graph/driver/neo4j_driver.py:203`, `code-tiny/tools/graph/driver/falkordb_driver.py:35`, `code-tiny/tools/common/primary_vector_sync.py:128`, `code-tiny/mcp/unified_mcp.py:2048`, `code-tiny/mcp/unified_mcp.py:2076`).
- A dry-run-by-default, `--apply` migration inventories collisions and missing IDs, idempotently backfills graph nodes and Qdrant payloads, and creates graph property indexes plus Qdrant keyword indexes; raw IDs, graph identities, point IDs, and vectors remain untouched (`code-tiny/scripts/backfill_project_scope_keys.py:1`, `code-tiny/scripts/backfill_project_scope_keys.py:49`, `code-tiny/scripts/backfill_project_scope_keys.py:81`, `code-tiny/scripts/backfill_project_scope_keys.py:134`, `code-tiny/scripts/backfill_project_scope_keys.py:179`, `code-tiny/scripts/backfill_project_scope_keys.py:197`, `code-tiny/scripts/backfill_project_scope_keys.py:352`).
- Regression coverage verifies mixed-case equivalence, strict rejection of different IDs, raw-value preservation, collision reporting, indexes, pagination, and repeatable migration runs (`tests/test_case_insensitive_project_scope.py:31`, `tests/test_case_insensitive_project_scope.py:178`, `tests/test_case_insensitive_project_scope.py:225`).

## Impact

Graph-only, vector-only, semantic expansion, workflow, and full-stack bridge searches now map case variants to the same exact indexed scope while responses and persisted raw project IDs remain unchanged. **Risk level: medium** because the contract spans ingestion, query preparation, and two storage providers; existing data must be migrated before normalized predicates can find every legacy record. Focused verification completed with 75 passed tests and 59 passed subtests. The repository-wide gate reached 273 passed tests, with 30 unrelated baseline or environment failures remaining (`plans/260723-0908-case-insensitive-project-id/plan.md:100`).

## Decision

Store a separate normalized `casefold()` key and continue using exact indexed provider predicates instead of lowercasing raw IDs, applying client-only comparisons, or changing identity formats. This preserves compatibility and provider performance, lets frontend and backend scopes normalize independently, and makes legacy upgrades observable and repeatable through dry-run inventories before mutation.

## References

- Plan: [plans/260723-0908-case-insensitive-project-id/plan.md](../../plans/260723-0908-case-insensitive-project-id/plan.md)
- Verification: `plans/260723-0908-case-insensitive-project-id/plan.md:100`
- Scope contract: `code-tiny/tools/common/project_scope.py:29`
- Migration: `code-tiny/scripts/backfill_project_scope_keys.py:81`
- Commit: `5969571d20314169d7ab2a46b4fe949e16d6a568`
