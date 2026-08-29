# Phase 04 — Payload narrowing + metadata cache — report

Date: 2026-08-30

## What shipped

- New `code-tiny/tools/common/qdrant_query_support.py`: the one search
  pipeline all four backends now delegate to
  (`search_collection`, `merge_collections`, `merge_hits`,
  `lazy_full_payload`, `lazy_fetch_missing`,
  `select_content_with_fallback`, `filter_collections_for_vector`,
  `cached_collections_payload`). Key contract points:
  - `PAYLOAD_EXCLUDE_SELECTOR = PayloadSelectorExclude(exclude=["text"])`
    (Exclude, not Include — red team #5; kotlin `class_name`/
    `package_name` survive).
  - Every hit is tagged `_collection` at hit level inside
    `_qdrant_search` (red team #6 — point ids can collide across
    collections), so lazy fetch groups retrieve calls per source
    collection after merge.
  - `lazy_fetch_missing` runs after merge cuts to top_k: hits without
    any of summary/comment/code get one grouped `retrieve` per
    collection (≤ top_k points); `content_mode="name"` skips it.
  - `_select_content` fallback: when all three content fields are
    absent, content is a 400-char preview of `text` (+ "…"). Full text
    stays in the payload only when `include_raw_fields=true`; otherwise
    `text` is popped after decoration — the accepted output delta for
    primary-style collections (full text → preview).
- New `code-tiny/tools/common/qdrant_layout_cache.py`: TTL cache
  (default 300 s; `MCP_COLLECTION_META_CACHE` = seconds or "0" to
  disable; 64-entry bound). Errors are never cached (fixes the old
  `_VECTOR_LAYOUT_CACHE` caching `None` on failure), TTL fixes stale-
  after-recreate. `intelligent_retrieval._resolve_vector_layout`
  migrated onto it; `primary_vector_sync.sync_vector_documents` calls
  `invalidate(url)` at the end of every sync.
- `local_qdrant.query_points`: `with_payload` is now a pass-through
  parameter (default `True` — other callers unchanged). Adapter layer
  (`LocalQdrantStore`/`RemoteQdrantStore`) already forwarded
  `with_payload: Any` — selector models flow to qdrant-client 1.18.0
  for both `query_points` and `retrieve`.
- All four backends delegate `_qdrant_search`, `_merge_qdrant_results`,
  `_fetch_qdrant_collections` (cached list; `include_vectors=true` stays
  live), `_filter_collections_for_vector`, and `_select_content` to the
  shared module, keeping the module-level names for test seams.
  `tool_semantic_search` (all modes) does the lazy fetch + text prune.

## Gate evidence

- **G5 (metadata round-trips ≤ 1 cold / 0 cached)** — verified by unit
  test on a counting stub (`tests/test_qdrant_query_support.py::
  LayoutCacheTests.test_cold_then_cached_round_trips`,
  `test_filter_collections_uses_cache_and_matches_behavior`): 2nd read
  hits cache (0 round-trips), per-search filter reads ≤ 1/collection
  cold, 0 warm. Mode-independent (holds for remote too).
- **G4 (unscoped p50 −40%)** — on the local-mode fixture the non-embed
  stages were already ~0 ms (baseline: resolve 0.1 ms, qdrant 1–2 ms),
  so there is nothing measurable to save locally; unscoped wall
  delta phase03→phase04 is inside MPS embed noise (±6 ms between runs,
  embed stage itself). Focused micro-benchmark on the exact read-path
  change (200 pts × 16 KB `text`, local mode): with_payload=True
  0.08 ms vs Exclude 0.10 ms — cost-neutral locally; the 16 KB-per-hit
  saving materializes on **remote** (network transfer), consistent with
  red team #3 and the baseline re-anchoring. **Remote-side G4 number is
  deferred** until a Qdrant server is available (same clause as
  phase 05 acceptance).
- Repeat-query wall (unscoped repeat incl. merge): 46.1 ms → ~1.0 ms
  (embed-cache hit via phase 02; G1 still intact in
  `reports/phase04.json`).

## Correctness

- `scripts/validate_retrieval.py` runs the new read path without error;
  overall `ok:false` is pre-existing environment state — the default
  project (`cortext`) has zero indexed data (`qdrant_collections: []`,
  0 graph nodes, empty `indexed_commit`), so linkage/freshness fail on
  absence, not on search behavior.
- Output-shape changes, deliberately accepted per plan: primary-style
  hits return `content` = 400-char preview (was: full 16 KB text in the
  response payload) and no `text` key unless `include_raw_fields=true`;
  hits additionally carry a top-level `_collection` provenance field.

## Tests

`tests/test_qdrant_query_support.py` (21 tests): selector assertion +
tagging, cross-collection dedupe with provenance, per-backend content
fixtures (legacy unchanged / primary text preview / kotlin fields kept),
grouped lazy retrieve, name-mode skip, loader-only-when-needed, TTL
expiry (injected clock), never-cache-errors, disable-env, invalidate,
filter behavior parity + round-trip counts, local-mode selector smoke
through a real qdrant-client store, `QDRANT_HNSW_EF` plumbing (for
phase 05).
