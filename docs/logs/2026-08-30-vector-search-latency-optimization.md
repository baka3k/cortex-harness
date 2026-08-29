# Vector-search query latency optimization (phases 01-06) — 2026-08-30
## Context
semantic_search MCP tool was slow; plan 260829-2322 (hi-craft --full) implemented embed runtime, payload path, collection tuning, ingest reuse.
## Change
- New shared embed runtime code-tiny/tools/common/embed_runtime.py (process-wide embedder cache keyed (model,device), query-vector LRU gated MCP_QUERY_EMBED_CACHE, CUDA+MPS CPU-retry classifier, device auto-detect darwin→MPS); all 4 MCP backends (fastmcp_server, cplus/android/java_mcp) delegate keeping patchable names.
- New code-tiny/tools/common/qdrant_query_support.py + qdrant_layout_cache.py: PayloadSelectorExclude(["text"]) reads, hit-level _collection tags, grouped lazy payload fetch ≤top_k, 400-char preview content, TTL metadata cache (errors never cached, invalidate() on sync).
- code-tiny/tools/common/local_qdrant.py: with_payload pass-through; HNSW/quantization env opt-in (QDRANT_HNSW_M/EF_CONSTRUCT/SCALAR_QUANT) with local-mode inert warning; 4 payload indexes in primary_vector_sync; wait-on-last-batch upsert; default embedder via shared SentenceTransformer cache.
- New code-tiny/scripts/rebuild_vector_collection.py (copy-vector rebuild, --yes gated, two count validations).
- cortex_harness/dev.py: _normalize_embed_device resolves "auto" before analyzers see it; _embed_device_cli_arg choke point; dev-init defaults BATCH_SIZE=8, device=auto; 19 analyzer --batch-size fallbacks 4→8.
- New scripts/benchmark_semantic_search.py: stage-timed benchmark through unified_mcp._dispatch_tool (production surface), MCP_SEARCH_TIMING log instrumentation in the 4 backends.
## Impact
Repeat-query semantic_search wall 46ms→~1ms (-98%); cold embed 2.5x faster on MPS (numerically identical, cosine diff 1.1e-16); accepted output delta: primary-style collections return 400-char preview instead of full 16k text (raw text only with include_raw_fields=true, plus new _collection field); G6 ingest throughput settles at 2.3x (short-text bound); risk low-medium: output contract delta + caching behavior, mitigated by fixture tests; suite 1478 passed, only 2 pre-existing test_storage_lifecycle failures (fail on clean HEAD too).
## Decision
- Exclude-not-Include selector (no field-list drift for kotlin payloads); cache in tools.common so importlib-aliased backends share it; "auto" never leaves dev.py (analyzer crash guard).
- Upsert wait-on-last-batch because sync targets the live collection (self-heal via _delete_stale re-sync) — staging publication belongs to pending plan 260807-0929; G6 3x target re-anchored to 2.3x under the plan's short-text escape clause.
- Code review found 1 CRITICAL (rebuild script deleted stale temp without --yes) + 1 MAJOR (preload device "cpu" default diverged from auto query path → double model load) — both fixed in one cycle.
## References
- plan: ./plans/260829-2322-vector-search-query-optimization/plan.md
- reports: ./plans/260829-2322-vector-search-query-optimization/reports/ (baseline.md gates, phase-02..06.md, review.md, final.json)
- commit: dc9a32e
