# Owner-Scoped Local Storage — 2026-08-06

## Context

The [local storage plan](../../plans/260806-1648-local-file-storage/plan.md) replaced Docker-managed database services and repository-local data directories with durable, machine-local Qdrant and FalkorDBLite storage. The contract had to keep physical process ownership separate from logical project scope while preserving existing data through an explicit migration path (`plans/260806-1648-local-file-storage/plan.md:75`, `plans/260806-1648-local-file-storage/plan.md:154`).

## Change

- Added one resolver for the centralized per-account data root, versioned instance tree, distinct code/document owners, and owner-specific Qdrant directories and FalkorDB `.rdb` files. Remote endpoint configuration now fails with migration guidance instead of silently opening an empty local store (`cortex_harness/storage/config.py:86`, `cortex_harness/storage/config.py:194`, `cortex_harness/storage/config.py:251`).
- Added immutable manifest validation, fail-fast cross-process leases, and a non-destructive legacy migration that holds source and target leases continuously through hashing, copy, reopen verification, and marker publication (`cortex_harness/storage/layout.py:54`, `cortex_harness/storage/lease.py:19`, `cortex_harness/storage/migration.py:91`).
- Centralized local Qdrant access behind one cached, leased client per physical owner path, and added Docker-free initialization, migration, verified backup, and doctor lifecycle commands (`cortex_harness/storage/qdrant.py:43`, `cortex_harness/storage/qdrant.py:84`, `scripts/mcp-lifecycle.py:296`, `scripts/mcp-lifecycle.py:335`).

## Impact

Risk level: **high**. Code and document processes now persist to separate embedded stores under one versioned harness instance, eliminating default dependencies on Docker and ports 6333/6379. The main risks are path drift, simultaneous opens, and accidental legacy-data replacement; canonical manifest checks, owner leases, copy-before-switch migration, content hashes, reopen verification, and verified backups make those failures explicit (`cortex_harness/storage/layout.py:103`, `cortex_harness/storage/migration.py:136`, `scripts/mcp-lifecycle.py:348`).

Verification covered real temporary Qdrant/FalkorDBLite persistence, owner isolation, migration, backup, lifecycle commands, and Docker-free smoke behavior (`tests/test_storage_layout.py:95`, `tests/test_storage_lifecycle.py:116`, `plans/260806-1648-local-file-storage/plan.md:291`). The non-COBOL repository gate reached 509 passed and 1 skipped, plus 224 subtests; the remaining 13 COBOL failures are the known pre-existing fixture/runtime baseline and are not claimed as part of this implementation.

## Decision

Use a centralized per-user data root partitioned by storage schema, harness instance, and stable process owner. Project IDs remain logical graph and collection names rather than physical directories. This preserves storage when source checkouts move, gives embedded clients an unambiguous lock boundary, and allows isolated deployments through an explicit instance or data-root override. Project-local automatic storage and implicit conversion of remote data were rejected because both make ownership and recovery ambiguous.

## References

- Local storage plan: [Docker-Free Local Qdrant and FalkorDBLite Storage](../../plans/260806-1648-local-file-storage/plan.md)
- Graph migration plan: [Neo4j to FalkorDB Migration](../../plans/neo4j-to-falkordb-migration/plan.md)
- Unified contract plan: [Unified Ingest/Query Contract](../../plans/260728-0000-unified-ingest-query-contract/plan.md)
- Storage contract: `cortex_harness/storage/config.py:194`
- Migration safety: `cortex_harness/storage/migration.py:91`
- Lifecycle acceptance: `tests/test_storage_lifecycle.py:116`
- Commit: `de7b6d2019545eb219134e00ba7f60a63826f850`
