# Phase 03: Qdrant Local Client Migration

## Context

Local Qdrant mode is activated by `QdrantClient(path=...)` and has no REST
server. The repository currently mixes direct clients with many raw HTTP
helpers, so all vector operations must cross one application-owned interface.

## Requirements

- Store all vector data below `./local_qdrant_db`.
- Avoid exclusive-lock failures when code and document MCP servers run
  together.
- Cover every operation used by ingestion, retrieval, cleanup, reset,
  backfill, Living Docs, and validation.
- Preserve collection naming and project-scope filter semantics.
- Remove active Qdrant URL construction and raw REST calls.

## Architecture

Implement `LocalQdrantStore`/factory modules under
`cortex_harness/storage/`. A storage role selects either the code or document
subdirectory. Each process owns one cached `QdrantClient` per resolved path.
The adapter accepts current plain dictionaries where useful but translates them
to official `qdrant_client.models` at the boundary.

Minimum adapter surface:

- collection list/existence/info/create/recreate/delete;
- payload index creation where supported in local mode;
- upsert/upload points;
- query/search with named and unnamed vectors;
- scroll/retrieve/count;
- delete by IDs/filter;
- set/overwrite payload;
- deterministic close.

## Related Files

- new `cortex_harness/storage/qdrant.py`
- `doc-tiny/0_reset_all.py`
- `doc-tiny/graphrag_ingest_langextract.py`
- `doc-tiny/graphrag_query_langextract.py`
- `doc-tiny/mcp_graph_rag.py`
- `doc-tiny/neo4j_loader.py`
- `code-tiny/tools/common/primary_vector_sync.py`
- `code-tiny/tools/common/intelligent_retrieval.py`
- `code-tiny/tools/common/incremental_cleanup.py`
- `code-tiny/tools/common/message_scan.py`
- `code-tiny/tools/cobol/qdrant.py`
- analyzer-local vector writer classes
- `code-tiny/mcp/fastmcp_server.py` and language-specific MCP servers
- `code-tiny/livingdoc/*.py`
- `code-tiny/scripts/backfill_project_scope_keys.py`
- `scripts/validate_retrieval.py`
- Qdrant tests under `tests/` and `code-tiny/tests/`

## Implementation Steps

1. Inventory every REST verb/path and map it to a `qdrant-client` operation;
   add a contract test row before changing each call site.
2. Implement the local store/factory with explicit code/doc roles, project-root
   path resolution, singleton lifecycle, and dependency injection.
3. Migrate shared vector ingest/cleanup/retrieval modules first so analyzers can
   reuse them instead of retaining per-language HTTP writer classes.
4. Migrate document reset, ingest, query, MCP, and legacy loader construction to
   the shared factory.
5. Migrate unified and language MCP collection/search tools, including named
   vectors and current response-shape normalization.
6. Migrate Living Docs, project-scope backfill, message scanning, and retrieval
   validation.
7. Remove raw `/collections` and `/points` URL construction from active runtime
   code and delete redundant HTTP helper implementations.
8. Close clients on MCP shutdown and short-lived script completion so locks are
   released predictably.
9. Add persistence, filter, named-vector, payload-index, batch, reset, restart,
   and simultaneous code/doc server tests using temporary role directories.
10. Validate cross-domain operations. If a code process needs document vectors,
    route through the owning document service or a graph link instead of opening
    the document path concurrently.

## Todo

- [ ] Build the REST-to-client operation matrix.
- [ ] Implement the shared local adapter.
- [ ] Migrate shared code ingestion/retrieval paths.
- [ ] Migrate document paths.
- [ ] Migrate MCP, Living Docs, backfill, and validation paths.
- [ ] Delete active raw Qdrant HTTP helpers.
- [ ] Add concurrency and persistence tests.

## Risks

- Local mode intentionally rejects a second process opening the same directory.
- Some server-only API behavior or payload index operations may be no-ops or
  unsupported locally; tests must define a safe equivalent.
- Copy-pasted analyzer vector writers make incomplete migration likely unless
  an exact-search gate is enforced.
- Local mode may have different performance characteristics on large datasets;
  this plan targets functional local startup, not server-scale throughput.

## Success Criteria

- All vector operations use the shared local adapter and persist below
  `./local_qdrant_db`.
- Code and document MCP servers run concurrently using separate role paths.
- An exact source search finds no active runtime assembly of Qdrant REST
  collection/point endpoints.
- Project filters, collection names, named vectors, resets, and restart
  persistence pass contract tests.
