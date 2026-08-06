# Unified Project Storage Contract — 2026-08-06

## Context

The [unified ingest/query plan](../../plans/260728-0000-unified-ingest-query-contract/plan.md) identified divergent graph and vector defaults between code and document workflows. The completed contract makes `project_id` the stateless routing key, uses one case-insensitive naming model, and preserves full search when callers omit project scope (`plans/260728-0000-unified-ingest-query-contract/plan.md:63`, `plans/260728-0000-unified-ingest-query-contract/plan.md:249`).

## Change

- Added registry-resolved `ProjectTargets` for code graph, code collection, document graph, and document collection, with casefolded lookup and duplicate-registration rejection. The document-side resolver mirrors the same naming and normalization rules (`code-tiny/tools/common/project_registry.py:113`, `code-tiny/tools/common/project_registry.py:226`, `doc-tiny/project_contract.py:53`, `doc-tiny/project_contract.py:193`).
- Scoped graph queries now resolve a registered project shard; unscoped bridge and backend queries enumerate all registered graphs through one shared physical driver. Document GraphRAG similarly enumerates all registered graph views and Qdrant collections for full search (`code-tiny/mcp/unified_mcp.py:1118`, `code-tiny/mcp/unified_mcp.py:1156`, `code-tiny/mcp/cplus/cplus_mcp.py:249`, `doc-tiny/mcp_graph_rag.py:118`, `doc-tiny/mcp_graph_rag.py:146`).
- Document ingest namespaces deterministic entity IDs and payloads with `project_id_normalized`, rejects unregistered project targets, and project-scoped reset deletes only matching graph nodes and Qdrant points instead of dropping a shared collection (`doc-tiny/graphrag_ingest_langextract.py:131`, `doc-tiny/graphrag_ingest_langextract.py:1174`, `doc-tiny/0_reset_all.py:16`, `doc-tiny/0_reset_all.py:40`).

## Impact

Risk level: **high**. Code and document ingestion, query, full-search, launcher, and reset behavior now agree on the same project shard. The contract prevents cross-project entity merges and destructive collection-wide resets while retaining bounded cross-project search when scope is omitted. Two-project fixtures exercise disjoint targets, normalized payloads, registered-collection aggregation, shared-driver graph aggregation, and scoped reset isolation (`scripts/smoke_unified_contract.py:46`, `tests/test_framework_mcp_flows.py:67`, `tests/test_unified_contract_doc_paths.py:72`, `tests/test_unified_contract_doc_paths.py:167`).

Focused and non-COBOL verification passed. The only repository-wide exception is the known pre-existing 13-failure COBOL fixture/runtime baseline; it is not attributed to or claimed fixed by the unified contract work.

## Decision

Use stateless per-call registry resolution and separate code/document shard names, with `project_id_normalized` as a defensive payload and graph predicate. Omitted scope means explicit aggregation across every registered shard, not fallback to one environment-selected default. Stateful activation and scattered environment-derived targets were rejected because they can make ingest, query, and reset address different projects within the same process.

## References

- Unified contract plan: [Unified Ingest/Query Contract](../../plans/260728-0000-unified-ingest-query-contract/plan.md)
- Local storage plan: [Docker-Free Local Qdrant and FalkorDBLite Storage](../../plans/260806-1648-local-file-storage/plan.md)
- Graph migration plan: [Neo4j to FalkorDB Migration](../../plans/neo4j-to-falkordb-migration/plan.md)
- Registry contract: `code-tiny/tools/common/project_registry.py:113`
- Full graph search: `code-tiny/mcp/unified_mcp.py:1156`
- Scoped document reset: `doc-tiny/0_reset_all.py:40`
- Two-project verification: `scripts/smoke_unified_contract.py:46`
- Commit: `de7b6d2019545eb219134e00ba7f60a63826f850`
