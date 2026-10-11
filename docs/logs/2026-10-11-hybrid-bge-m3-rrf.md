# Hybrid retrieval: BGE-M3 dense + learned-sparse RRF (phase 1, flag-gated) — 2026-10-11

## Context

The unified embedding default had just been reverted to `BAAI/bge-m3`
(commit `86ab1e9f94fd9a49f61194e9479c4a427f436e7d`), which makes BGE-M3's
otherwise-wasted second output — learned lexical (sparse) weights from the
same forward pass — available to every corpus in the harness. Plan
`hi-plan --full` scoped phase 1 as dense+sparse fused server-side via the
Qdrant Query API (`Fusion.RRF`), explicitly deferring ColBERT multi-vector
and any model/dimension change: docs/plans/261011-hybrid-bge-m3/plan.md.
The prediction report flagged three High risks (missing head files in local
snapshots, sparse-math fidelity if hand-ported, half-migrated mixed state
across collections), which red-team pass F1–F3 sharpened into the design
constraints realized below (docs/plans/261011-hybrid-bge-m3/red-team.md).

## Change

Implemented in `03fa15626a75ffffe009a0723b016225754354ba` (43 files,
~4000 insertions), gated behind `EMBED_HYBRID`
(code-tiny/tools/common/embed_runtime.py:52, resolved by
`hybrid_enabled()` at code-tiny/tools/common/embed_runtime.py:64 — flag
AND bge-m3 family; non-bge family is a no-op with a one-time warning):

- **Encode (P1)** — `encode_sparse()` ports the FlagEmbedding sparse-linear
  math (`relu(W·h+b)`, keep `w>0`, drop cls/eos/pad/unk ids, max weight per
  token id, sorted indices) as a separate token-level forward through
  `model[0].auto_model` — the dense encode path is never touched
  (code-tiny/tools/common/embed_runtime.py:710). Query-side half comes via
  `query_sparse_only()` with its own cache
  (code-tiny/tools/common/embed_runtime.py:556); missing head with flag on
  is an actionable RuntimeError at load, zero network
  (code-tiny/tools/common/embed_runtime.py:641); `sparse_linear.pt`
  (~3.5 KB) is fetched through the audited HF session by
  code-tiny/scripts/prefetch_hybrid_heads.py.
- **Ingest (P2)** — NEW collections declare the named `sparse` vector at
  every creation point: `ensure_collection`
  (code-tiny/tools/common/local_qdrant.py:295, config at :346), doc
  provisioning (cortex_harness/storage/remote_probe.py:135). All writer
  chokepoints attach dual vectors `{"": dense, "sparse": ...}` — the
  analyzer `LocalQdrantWriter` funnel
  (code-tiny/tools/common/local_qdrant.py:500), `primary_vector_sync`,
  cobol sync, livingdoc x2, doc-tiny ingest — behind
  `collection_supports_sparse()` (code-tiny/tools/common/local_qdrant.py:270)
  which degrades to dense-only with a backfill pointer instead of failing
  the ingest. Sentinel gains a `hybrid` visibility field
  (code-tiny/tools/common/embedding_marker.py:38); mismatch logic is
  unchanged.
- **Query (P3)** — `search_collection` accepts `sparse_vector` + `fusion`
  (code-tiny/tools/common/qdrant_query_support.py:176); fusion kwargs are
  built by `hybrid_fusion_query()` — prefetch `using=""` + `using="sparse"`,
  `FusionQuery(Fusion.RRF)`, sentinel `must_not` on both channels, depth
  `max(4·top_k, 64)` overridable via `EMBED_FUSE_K`
  (code-tiny/tools/common/qdrant_query_support.py:99). Fan-out capability
  is decided once per fan-out by `fanout_supports_sparse()`
  (code-tiny/tools/common/qdrant_query_support.py:65) using the
  `qdrant_layout_cache` TTL cache; `merge_collections`
  (code-tiny/tools/common/qdrant_query_support.py:398) and all backends,
  explore_service/IntelligentRetrieval
  (code-tiny/mcp/services/explore_service.py:220, :671;
  code-tiny/tools/common/intelligent_retrieval.py:176) are wired through it.
- **Ops (P4)** — recreate-based backfill: scroll payloads → export backup
  → drop → recreate with sparse → re-encode → stamp `hybrid=true`, with
  `--yes`/`--force`/`--dry-run`/`--resume` guards
  (code-tiny/scripts/backfill_hybrid_sparse.py:146, guard at :265). A/B
  harness + smoke results committed at
  docs/plans/261011-hybrid-bge-m3/ab_harness.py / ab_results.json; runbook
  section "Hybrid retrieval enablement" added to
  docs/UNIFIED_INGEST_QUERY_CONTRACT.md.
- **Tests** — 4 new suites (test_hybrid_sparse_runtime / _ingest /
  _query_fusion / _backfill): 46 tests + 31 subtests green; full repo
  1800 passed with only the 15 pre-existing baseline failures (verified on
  HEAD). hi-craft review gate: 0 critical; 3 High + 2 Medium findings fixed
  with regression tests.

## Impact

**Level: med.** All ingestion and query paths in both code-tiny and doc-tiny
are touched, but the shipped default (`EMBED_HYBRID` off) keeps behavior
byte-parallel with the legacy path, so no operator action is required and
rollback is "don't set the flag". With the flag on: existing dense-only
collections silently keep dense-only queries (reader-soft), new collections
go hybrid, and fused latency measures ~2–3x dense at local-mode scale
(~1.4 ms absolute at smoke scale) with ~0.5 KB/point (~11%) extra storage
(docs/plans/261011-hybrid-bge-m3/verification-report.md §2). Response
shapes, sentinel exclusion, and embedding-model mismatch warnings are
unchanged in both modes.

## Decision

1. **DIY ~30-line sparse-head port instead of the FlagEmbedding
   dependency.** The golden fixture
   (tests/fixtures/bge_m3_sparse_golden.json) was generated from
   FlagEmbedding 1.4.2's *modeling* layer
   (`EncoderOnlyEmbedderM3ModelForInference._sparse_embedding` +
   `_process_token_weights`), because two reference traps were found:
   the high-level `BGEM3FlagModel.encode(return_sparse=True)`
   lexical-weights path is unreliable at 1.4.2 (shape quirk between the
   vocab-sized embedding and the per-token zip), and `get_model`
   random-initializes BOTH heads unless **both** `colbert_linear.pt` and
   `sparse_linear.pt` exist in the snapshot — so a naive reference
   comparison would have validated against random weights. The port
   matches the modeling-layer reference exactly (token-id sets equal,
   weights ≤ 1e-4 apart) while avoiding a new pinned dependency; the plan's
   phase-01 decision gate ("FlagEmbedding if light, else port") resolved to
   port.
2. **New collections only + recreate-based backfill + writer-side
   capability probe.** Local-mode Qdrant cannot add a sparse vector to an
   existing collection: `update_collection(sparse_vectors_config=...)`
   raises `ValueError`, and upserting a dict with a `sparse` slot into a
   dense-only collection raises `Wrong input: Not existing vector name
   error: sparse` (verified by execution; the second trap was found during
   the build and was not in the original digest). Hence: sparse config only
   on NEW collections, upgrade of existing ones exclusively via
   `backfill_hybrid_sparse.py` recreate, and `collection_supports_sparse()`
   degrading writers to dense-only (with a backfill-pointer warning)
   instead of failing ingestion (red-team F2/F5).
3. **All-or-none RRF fusion per fan-out.** `merge_hits` compares scores
   directly, so one fused (RRF) member next to one dense-only (cosine)
   member would mis-rank silently — there is no admissible mixed-scale
   state (red-team F1). `fanout_supports_sparse()` ANDs capability across
   the whole fan-out: any dense-only member (or `_mess` hash-vector
   collection, always not-hybrid per the existing name-exempt rule) drops
   the entire fan-out to plain dense with identical score semantics.
4. **Flag-off byte-parallelism via the `_embed_query` stub seam.** Rather
   than routing queries through a hybrid-aware encode API, backends keep
   their existing dense `_embed_query` call and fetch the sparse half
   separately via reader-soft `query_sparse_only()`
   (code-tiny/mcp/fastmcp_server.py:1230-1231) — flag off means the second
   call simply never happens and the legacy call shape is preserved
   bit-for-bit; sparse embedder absence is treated as "hybrid doesn't
   apply", never a gate (code-tiny/mcp/services/explore_service.py:220).
5. **`EMBED_HYBRID` ships OFF; flip is a post-A/B operator decision.** The
   smoke A/B (pilot-scale temp Qdrant, real bge-m3, EN/VI/JA/identifier
   query classes) shows no ground-truth regression — truth holds rank-1 in
   both modes, VI shows fused re-ranking with truth still rank-1 — but
   per plan AC7 that harness is not the full A/B against a real corpus.
   Shipping flag-off makes the enhancement zero-risk and reversible by
   unsetting an env var; the enablement runbook lives in
   docs/UNIFIED_INGEST_QUERY_CONTRACT.md.

## References

- plan: ./docs/plans/261011-hybrid-bge-m3/plan.md (phases 01–04, status:
  completed; red-team.md F1–F10 dispositions; verification-report.md §1–§5)
- commit: 03fa15626a75ffffe009a0723b016225754354ba (feat(hybrid))
- commit: 86ab1e9f94fd9a49f61194e9479c4a427f436e7d (prerequisite: revert
  embed default to BAAI/bge-m3)
- code: code-tiny/tools/common/embed_runtime.py:710 (`encode_sparse`),
  code-tiny/tools/common/embed_runtime.py:556 (`query_sparse_only`),
  code-tiny/tools/common/qdrant_query_support.py:65
  (`fanout_supports_sparse`), code-tiny/tools/common/qdrant_query_support.py:99
  (`hybrid_fusion_query`), code-tiny/tools/common/local_qdrant.py:270
  (`collection_supports_sparse`), code-tiny/scripts/backfill_hybrid_sparse.py
- fixture: tests/fixtures/bge_m3_sparse_golden.json
- related log: ./docs/logs/2026-10-09-unified-qwen3-embedding.md (the
  default the revert restored)
