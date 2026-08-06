# FalkorDBLite Runtime Cutover — 2026-08-06

## Context

The [graph migration plan](../../plans/neo4j-to-falkordb-migration/plan.md) required the code and document runtimes to preserve provider-neutral graph behavior while moving the supported local path from Neo4j/network FalkorDB to embedded FalkorDBLite. Direct Neo4j entry points, provider-specific Cypher, schema operations, and Living Docs sessions all had to cross the same driver boundary (`plans/neo4j-to-falkordb-migration/plan.md:68`, `plans/neo4j-to-falkordb-migration/plan.md:275`).

## Change

- Completed the FalkorDB driver path with embedded `.rdb` opening, portable Cypher normalization, normalized result shapes, storage leases, and provider selection through `GraphDriverFactory` (`code-tiny/tools/graph/driver/falkordb_driver.py:51`, `code-tiny/tools/graph/driver/falkordb_driver.py:135`, `code-tiny/tools/graph/core/factory.py:91`).
- Added a process-local shared-driver cache keyed by the physical provider target, preventing multiple logical graph views from reopening the same embedded store. Analyzer CLI preparation now derives the local owner path and retains Neo4j only as an explicitly selected compatibility provider (`code-tiny/tools/graph/core/shared_runtime.py:24`, `code-tiny/tools/graph/core/shared_runtime.py:44`, `code-tiny/tools/graph/cli.py:100`, `code-tiny/tools/graph/cli.py:135`).
- Moved Living Docs onto a provider-neutral session facade and made schema setup idempotent through FalkorDB-native indexes, full-text indexes, and asynchronously polled uniqueness constraints (`code-tiny/livingdoc/graph_runtime.py:31`, `code-tiny/livingdoc/graph_runtime.py:131`, `code-tiny/scripts/setup_constraints.py:611`, `code-tiny/scripts/setup_constraints.py:684`).

## Impact

Risk level: **high**. The default graph engine, schema path, analyzers, MCP backends, document graph adapter, and Living Docs now operate against local FalkorDBLite without requiring a Redis-compatible service. Risk is concentrated in Cypher compatibility, result normalization, schema convergence, and embedded-file ownership. Focused driver, schema, analyzer-wiring, Living Docs, restart, graph-isolation, and rollback-isolation tests passed; schema setup was exercised twice against the same embedded graph and reported 102 operational constraints, 143 range indexes, and 242 full-text indexes on each run (`plans/neo4j-to-falkordb-migration/plan.md:275`, `code-tiny/tests/test_falkordb_schema_migration.py:1`, `code-tiny/tests/test_livingdoc_local_graph.py:1`).

The repository-wide verification exception remains limited to the known pre-existing 13-failure COBOL fixture/runtime baseline; the migration-specific and non-COBOL gates passed and no production Neo4j dataset parity is claimed because none was supplied (`plans/neo4j-to-falkordb-migration/plan.md:282`).

## Decision

Keep one provider-neutral graph contract and make owner-scoped FalkorDBLite the supported local implementation. Backend-specific query rewrites and package import details stay inside the driver, while named graphs are selected per execution through shared physical drivers. An in-place rewrite to direct FalkorDB calls was rejected because it would duplicate connection ownership and schema behavior; Neo4j remains isolated as an explicit rollback provider instead of an implicit fallback.

## References

- Graph migration plan: [Neo4j to FalkorDB Migration](../../plans/neo4j-to-falkordb-migration/plan.md)
- Local storage plan: [Docker-Free Local Qdrant and FalkorDBLite Storage](../../plans/260806-1648-local-file-storage/plan.md)
- Unified contract plan: [Unified Ingest/Query Contract](../../plans/260728-0000-unified-ingest-query-contract/plan.md)
- Embedded driver: `code-tiny/tools/graph/driver/falkordb_driver.py:135`
- Shared runtime: `code-tiny/tools/graph/core/shared_runtime.py:44`
- Schema polling: `code-tiny/scripts/setup_constraints.py:611`
- Commit: `de7b6d2019545eb219134e00ba7f60a63826f850`
