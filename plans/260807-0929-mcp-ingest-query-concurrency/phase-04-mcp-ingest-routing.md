# Phase 04: MCP query and ingestion routing

## Context

The live query paths call the shared graph driver and local Qdrant through
several backend wrappers. Code incremental sync has a separate project/root
lock, while document ingestion opens its graph/vector stores directly and has
no cross-store commit boundary. Both owners must submit work to the
physical-target scheduler without rewriting parser or extraction semantics.

## Requirements

- Keep MCP transport available during build, validation, and publication.
- Pin every query to one generation and include freshness/status metadata.
- Accept concurrent ingestion requests as jobs but serialize same-target
  mutation; allow isolated targets to proceed only when capacity permits.
- Preserve existing tool names and project-scope contracts.
- Make submit/status/follow/cancel, freshness, warmup, overload, and
  maintenance behavior consistent across graph_mcp and mind_mcp.
- Do not convert retrieval timeout, overload, cancellation, or storage failure
  into an empty successful result.

## Related files

- `code-tiny/mcp/unified_mcp.py` (`_run_bridge_query` and dispatch helpers).
- `code-tiny/mcp/services/explore_service.py` retrieval/thread boundary.
- Backend wrappers in `code-tiny/mcp/cplus/`, `android/`, `java/`, and
  `fastmcp_server.py`.
- `code-tiny/tools/sync/incremental_sync.py`, `tools/common/sync_scope.py`,
  and `tools/common/project_registry.py`.
- `doc-tiny/graph_store.py`, `doc-tiny/graphrag_ingest_langextract.py`, and
  `doc-tiny/mcp_graph_rag.py` — owner routing and generation-aware handles;
  preserve extraction and query shaping.
- `cortex_harness/dev.py` and `scripts/mcp-lifecycle.py`.

## Implementation steps

1. Resolve a target through the existing registry and ask the gateway for a
   generation-pinned query handle.
2. Replace direct shared-driver/local-Qdrant calls with gateway operations;
   keep backend-specific query shaping above the boundary.
3. Replace store-related `asyncio.to_thread()`/default-executor calls with
   gateway lanes. Keep query understanding and model work within their own
   bounded budgets.
4. Add optional freshness controls only where compatible with existing MCP
   tool schemas; always return served-generation metadata.
5. Convert sync submissions to idempotent gateway jobs with queue position,
   state, cancellation, and source revision in status responses.
6. Expose fast submit plus status, follow/streamed progress, bounded wait, and
   cancel. Duplicate submissions return the existing job and stable phase
   counters rather than restarting work.
7. Propagate typed timeout/overload/storage/cancellation failures with
   `retry_after_ms`, capacity, generation, and correlation ID. Reserve an
   empty success for a completed zero-hit query.
   Reject excessive `top_k`, graph depth/path count, result size, or embed
   batch bytes before expensive work and explain the accepted limit.
8. Route document ingestion/query through the document owner gateway as well;
   keep the pause/restart lifecycle path working and add a feature flag for
   the new owner path.
9. Make `WARMING`, `DRAINING`, maintenance, stale generation, and active
   ingestion visible without requiring users to inspect logs or lock files.
10. Update testtool expectations after structured errors and status payloads
   stabilize; do not modify its protocol layer unnecessarily.

## Risks

The default thread executor currently hides contention. Do not replace it
with a larger unbounded pool; make store admission the controlling boundary.

## Success criteria

Concurrent MCP clients receive bounded, structured responses instead of raw
lease/thread failures, and concurrent same-target ingests never mutate the
active generation or duplicate a committed job. Submission/status meet their
latency budgets, freshness is visible, and infrastructure failures cannot look
like successful empty searches.
