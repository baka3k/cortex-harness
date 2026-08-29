# Phase 02 — Shared embedding runtime — report

Date: 2026-08-30

## What shipped

- New module `code-tiny/tools/common/embed_runtime.py`:
  process-wide embedder cache keyed `(model, device)` with a lock,
  query-vector LRU (`MCP_QUERY_EMBED_CACHE`, default 512, "0" disables,
  hits return copies), unified CPU-retry classifier
  `_is_accelerator_runtime_error` (CUDA markers + MPS/Metal markers),
  `get_sentence_transformer` cache (for phase 06), `reset_caches()` test
  helper. Torch import guarded — a torch-less environment raises a
  contextual error from `get_embedder` instead of breaking imports.
- All 4 MCP backends (`fastmcp_server`, `cplus_mcp`, `android_mcp`,
  `java_mcp`) converted to thin delegates keeping the module-level names
  (`_embed_query`, `_get_embedder`, `_resolve_embed_device`,
  `_embed_query_with_model`, `_mean_pool`, `_encode_texts`) so existing
  `patch.object(module, "_embed_query")` seams keep working. The four
  private `_embedder_cache` dicts are gone; unused `transformers`/`torch`
  imports removed from the backends.
- `tools/common/embedding_runtime.py` (HF audit module) untouched; guard
  test asserts `resolve_embedding_cache` is still exported.

## Gate evidence

Benchmark: synthetic store, real jina-v3 (1024-dim), unified dispatch →
`reports/phase02.json` / `phase02.md` vs `baseline.json`:

| Scenario | Baseline wall p50 | Phase 02 wall p50 | Δ |
|----------|-------------------|-------------------|---|
| scoped-repeat | 46.1 ms | **0.9 ms** | −98% (**G1 achieved**, embed cache hit < 5 ms) |
| scoped-cold | 45.9 ms | 45.7 ms | unchanged (no regression) |
| unscoped-multi | 48.9 ms | 47.5 ms | unchanged |
| expand-graph | 49.2 ms | 47.3 ms | unchanged |

## Tests

`tests/test_embed_runtime.py` (15 tests): cache hit/miss, LRU bound,
disable-env, copy-on-hit, CUDA + MPS retry, non-accelerator error
propagation, fallback disable via `EMBED_FALLBACK_TO_CPU=0`,
model-load MPS/CUDA→CPU fallback, alias/real module dedupe (Scope
decision #6), `explore_service` fallback resolves the delegate,
`embedding_runtime` guard, delegate names patchable.
`tests/test_qdrant_project_scope.py` (existing seam) passes unchanged.
