# Unified embedding: Qwen/Qwen3-Embedding-0.6B for both pipelines — 2026-10-09

## Context

Plan `docs/plans/261009-1304-unified-qwen3-embedding/plan.md` (executed hi-craft --full; commit
`3006c8b33ed11a158a7777c2b4c3d0909262036c` on develop). Motivation: the code side (graph_mcp)
defaulted to `jinaai/jina-embeddings-v3` while the doc side (mind_mcp) defaulted to
`BAAI/bge-m3` — two incompatible vector spaces, so cross-space code↔doc vector comparison was
impossible (same dim ≠ same space); jina also carries a CC-BY-NC license. Chosen replacement:
`Qwen/Qwen3-Embedding-0.6B` (1024-dim, last-token pooling, query instruction prompt,
Apache-2.0). A prior bge-m3 swap on the doc side had failed silently (same dim, no guard) —
this plan adds hard guards so that class of failure cannot recur.

## Change

1. `code-tiny/tools/common/embed_runtime.py:45` — new `model_policy()`, the single
   model-family decision point: qwen3 → SentenceTransformer backend + `prompt_name="query"`
   (`embed_runtime.py:293`, `:403`) + normalize; jina → AutoModel legacy path; unknown → legacy
   query dispatch. `get_sentence_transformer` (`embed_runtime.py:501`) gained
   `local_files_only`/`cache_folder` (:505-:535) and a transformers>=4.51 fail-fast for Qwen3
   (`_check_qwen3_transformers_floor`, `embed_runtime.py:112-:135`). `effective_model_identity()`
   (`embed_runtime.py:87-:95`) applies `CODE_EMBEDDING_MODEL_PATH` so marker identity matches
   the query chains.
2. `code-tiny/tools/common/embedding_marker.py` (NEW) — per-collection embedding-model sentinel:
   private uuid5 namespace (`PRIVATE_NAMESPACE`, `embedding_marker.py:43`; never NAMESPACE_URL —
   real point ids are uuid5(NAMESPACE_URL)), `stamp`/`check`/`enforce`/`mismatch`
   (:93/:123/:162/:213), `EmbeddingModelMismatchError` (:70); `check` fails CLOSED on store
   errors (review finding, docstring at :132); query-side read cache TTL 60s
   (`DEFAULT_CACHE_TTL_SECONDS`, `embedding_marker.py:39`, overridable via
   `EMBEDDING_MARKER_CACHE_TTL` at :50).
3. `code-tiny/tools/common/local_qdrant.py:242` — `ensure_collection` is the writer chokepoint
   (12 analyzers' `LocalQdrantWriter`, primary sync, cobol, livingdoc all funnel here):
   model mismatch → `embedding_marker.enforce` (:259) raising EmbeddingModelMismatchError with
   reset hint; `{base}_mess` collections exempt (`MESSAGE_COLLECTION_SUFFIX`, :41); existing
   collections get stamped on create (:276); dim guard raises before marker enforcement.
4. All 12 `CodeEmbedder` copies (python/js/java/cplus/kotlin/php/ts/sql/delphi/csharp/plsql/
   android_kotlin; vb imports python's at
   `code-tiny/tools/vb/vb_analyzer_base.py:2466`) now load via the shared
   `embed_runtime.get_sentence_transformer` (e.g. `code-tiny/tools/python/python_analyzer.py:957`)
   and encode with `normalize_embeddings=True` (`python_analyzer.py:994`); chunk mean-pool is
   re-normalized; js/java/ts/android_kotlin keep the HF-cache-permission fallback via
   `cache_folder`.
5. Query paths: `code-tiny/tools/common/qdrant_query_support.py:53-:66` — `search_collection`
   (:70) applies a cloned `must_not` filter excluding the sentinel (:91), never mutating the
   caller's filter; `merge_collections` (:259) emits structured `embedding_model_mismatch`
   entries into `errors[]` (:285) consumed by all 4 MCP backends; doc
   `doc-tiny/mcp_graph_rag.py:205` `_query_encode` follows the policy and surfaces a
   mismatch note in the response (:178, :1004-:1006); `code-tiny/mcp/services/explore_service.py:181-:195`
   encode follows the policy (prompt_name + normalize under qwen3).
6. Doc side: `doc-tiny/embedding_utils.py:40-:49` env chain arg→`EMBEDDING_MODEL_PATH`→
   `EMBEDDING_MODEL`→`DOC_EMBEDDING_MODEL`→default (`DEFAULT_DOC_EMBEDDING_MODEL`, :9);
   device auto-detect MPS/CUDA/CPU (`resolve_embedding_device`, :59-:76); `cortex_harness/dev.py:769-:770`
   doc env now sets `EMBEDDING_DEVICE` (doc-tiny reads EMBEDDING_DEVICE, not EMBED_DEVICE);
   `doc-tiny/graphrag_ingest_langextract.py:633` `create_collection` gained a hard size guard
   (:651-:655) + marker via the code-tiny bridge with a legacy backfill check when >1 point
   (:657-:662); `doc-tiny/0_reset_all.py:77-:78` per-project reset deletes the sentinel
   unconditionally (its project filter never matches it — zero-match corner included).
7. Defaults sweep: every jina/bge literal converges on
   `code-tiny/tools/common/model_defaults.py:11` `DEFAULT_CODE_EMBEDDING_MODEL` /
   `doc-tiny/embedding_utils.py:9` `DEFAULT_DOC_EMBEDDING_MODEL` (twins, pinned by test);
   dependency floors in root `requirements.txt:39` `transformers>=4.51,<4.56` and
   `requirements.txt:15` / `doc-tiny/requirements.txt:13` `sentence-transformers>=2.7.0`;
   `TEXT_EMBEDDING_MODEL` removed (was never read).
8. New `code-tiny/scripts/reset_code_collection.py` — per-project collection drop;
   `--include-messages` (:76-:78) required to also drop the `_mess` hash-vector sibling
   (:56-:61); refuses silent no-op on a wrong backend.
9. Docs: `runbook.md` + `verification-report.md` in the plan dir; contract playbook section
   "Embedding-model swap playbook (Qwen3-Embedding era)" at `docs/UNIFIED_INGEST_QUERY_CONTRACT.md:280`;
   README/Design/examples swept. Rollback doctrine: `db_transfer` import + restart with old env,
   NEVER reset-after-import (would delete the restored vectors); `db_transfer` is
   local-lanes-only (remote needs a server-side export).

## Impact

HIGH — every ingest and query path in both pipelines (code + doc). Operators MUST re-index per
the runbook before Qwen3 benefits apply; old dev-init configs keep legacy models until re-init
(the marker hard-error catches the mismatch at first ingest). Suite: 1720 passed; the 15
failures are pre-existing on HEAD (stash-verified). Live A/B pilot (EN/VI/JA) is PENDING — the
model snapshot is not downloaded yet; the fleet re-index must NOT start before that gate passes
(verification-report.md §3).

## Decision

(a) Query dispatch only reroutes models that declare a query prompt — keeps existing
stub-tested behavior byte-compatible (suite green with zero test edits). (b) Marker enforced at
writer chokepoints, not per-caller — red-team F1 showed 12 legacy analyzers bypass the primary
sync, so caller-side checks would leak. (c) Drop+re-ingest doctrine with mandatory `db_transfer`
backup (D11) — the direct lesson from the silent bge-m3 swap. (d) One adversarial review-fix
cycle adopted: fail-closed marker checks, PATH-aware stamping, TTL-bounded sentinel read cache,
narrowed TypeError fallbacks; rejected: an encode error-guard in explore_service (acceptable
under the sentence-transformers 2.7 floor).

## Follow-up (same day) — build-time model prefetch

`make build` / `make install` previously guaranteed the library floors only;
the ~1.2 GB Qwen3 snapshot was a lazy first-load download. Added a best-effort
`prefetch_embedding_model()` step at the end of `invoke_build()`
(scripts/mcp-lifecycle.py:344): reuses `resolve_embedding_cache` completeness
semantics, prints `already complete` when cached, downloads otherwise, and on
failure WARNs with the runbook pointer instead of failing the build. Opt out
with `CORTEX_SKIP_MODEL_PREFETCH=1`. Verified: skip / warn(404, no raise) /
already-complete paths plus a real offline ST load (dim 1024, query prompt,
norm 1.0). Windows `mcp-lifecycle.ps1` keeps its pre-existing parity gap
(the ANTLR step was already .py-only).

## References

- plan: `./docs/plans/261009-1304-unified-qwen3-embedding/plan.md`
- runbook: `./docs/plans/261009-1304-unified-qwen3-embedding/runbook.md`
- verification: `./docs/plans/261009-1304-unified-qwen3-embedding/verification-report.md`
- commit: `3006c8b33ed11a158a7777c2b4c3d0909262036c`
- new tests: `tests/test_qwen3_query_policy.py`, `tests/test_qwen3_ingest_policy.py`,
  `tests/test_qwen3_embedding_marker.py`, `tests/test_qwen3_defaults.py`
