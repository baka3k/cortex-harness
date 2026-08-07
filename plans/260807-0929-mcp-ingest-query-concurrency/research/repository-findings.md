---
type: research
date: 2026-08-07
---
# Repository findings: MCP ingest/query concurrency

## Summary

The repository has strong ownership primitives but no operation-level
concurrency boundary. The embedded lease is physical-target scoped; code
ingest locking is project/root scoped; graph execution is synchronous behind
an async wrapper; local Qdrant locks client creation only; and document ingest
still opens graph/vector stores directly. No existing tests cover multi-client
load, writer queues, generation publication, query pinning, or mixed graph/vector
consistency.

## Exact implementation inventory

| Area | Files and symbols |
| --- | --- |
| Storage ownership | `cortex_harness/storage/lease.py`: `StorageLease`, `acquire`, `release`, `assert_owner_stopped`, `StorageLeaseConflictError` |
| Storage resolution/layout | `cortex_harness/storage/config.py`: `ResolvedStorage`, `resolve_storage`, `storage_overlay`; `layout.py`: `manifest_payload`, `load_manifest`, `ensure_layout` |
| Local Qdrant | `cortex_harness/storage/qdrant.py`: `get_client`, `reset_clients`, `LocalQdrantStore` |
| Code vector boundary | `code-tiny/tools/common/local_qdrant.py`: `_local_path`, `get_code_qdrant_store`, `query_points`, `scroll_points`, `delete_by_filter`, `LocalQdrantWriter` |
| Vector ingestion | `code-tiny/tools/common/primary_vector_sync.py`: `VectorDocument`, deterministic IDs, `sync_vector_documents`, `_delete_stale` |
| Shared graph runtime | `code-tiny/tools/graph/core/shared_runtime.py`: `_driver_key`, `get_shared_graph_driver`, `reset_shared_graph_drivers` |
| FalkorDB driver | `code-tiny/tools/graph/driver/falkordb_driver.py`: local open, constructor, `close`, `execute_query`, `execute_query_sync`, `_graph_for`, `create_indexes` |
| Driver construction | `code-tiny/tools/graph/core/factory.py`: `GraphDriverFactory.create_driver`, `create_from_env`; `tools/graph/cli.py`: `prepare_graph_args`, `create_graph_driver_from_args` |
| Code ingestion | `code-tiny/tools/sync/incremental_sync.py`: `_query_impacted_files`, `_ensure_project_repository_graph`, `_build_analyzer_cmd`, `_run_incremental`, `parse_args`, `main` |
| Run lock/state | `code-tiny/tools/common/sync_scope.py`: `ProjectRunLock`, `scan_scope_id`; `incremental_sync_state.py`: state load/save/dirty/clean |
| Unified MCP | `code-tiny/mcp/unified_mcp.py`: `_resolve_graph_database`, `_run_bridge_query`, `_run_project_context_tool` |
| Retrieval | `code-tiny/mcp/services/explore_service.py`: `ExploreService`, `explore`, `_resolve_search_targets`, `_run_retrieval`; `tools/common/intelligent_retrieval.py`: retrieval engine and Qdrant path |
| Document ingestion/query | `doc-tiny/graph_store.py`: graph store/session; `graphrag_ingest_langextract.py`: graph/Qdrant ingestion; `mcp_graph_rag.py`: Qdrant/Neo4j access and graph candidates |
| Lifecycle | `scripts/mcp-lifecycle.py`: storage/start/stop/doctor; `cortex_harness/dev.py`: pause/restart and code/doc sync orchestration |

## Current behavior

- `StorageLease.acquire()` takes an exclusive, non-blocking OS lock beside a
  physical target. A second process fails before opening the store. This is
  ownership protection, not a read/write gate.
- Local Qdrant caches one client per path and holds one lease per path;
  `_client_lock` protects initialization/cache mutation only.
- `FalkorDBDriver.execute_query()` immediately calls synchronous
  `execute_query_sync()`, which invokes `graph.query()` with no bounded
  executor, semaphore, or owner-level queue. It retries every failed query
  once, including mutations.
- The shared graph runtime prevents duplicate driver creation inside one
  process, but does not coordinate operations on that driver.
- Unified MCP reuses the process-global driver, while retrieval uses
  `asyncio.to_thread()`. Burst requests can therefore share synchronous graph
  and Qdrant clients across multiple worker threads.
- MCP backends install first-signal handlers but do not yet implement a
  gateway drain state, queue persistence, generation-safe publication grace,
  or dependency-ordered client/executor shutdown.
- The default executor is also used in other query helpers, while analyzers
  and ML libraries can create their own Python/native thread pools. There is no
  owner-wide thread/process budget or oversubscription gate.
- `ExploreService._run_retrieval()` currently catches retrieval exceptions and
  returns `[]`, so overload or storage failure can be indistinguishable from a
  valid zero-hit query.
- Code incremental sync has a `ProjectRunLock` keyed by project ID and source
  root, not resolved physical storage owner. `dev sync code` pauses MCP before
  child analyzers acquire leases.
- Document sync does not have the same pause/owner handoff. It opens document
  Qdrant/FalkorDB directly and has no cross-store commit boundary.
- Existing generation concepts are analyzer/framework-specific; there is no
  general graph/vector manifest, active-generation pointer, request pinning,
  or reference-counted retirement.

## Research conclusion

The prediction report is supported. The safe next boundary is one owner/gateway
per physical target with bounded readers, one queued writer per target,
immutable graph/vector staging generations, one active manifest, explicit
idempotency, named bounded executors, one lock order, graceful drain/recovery,
and mixed-load/fault/user-flow tests. Server mode should remain a measured
scale path rather than an immediate migration.
