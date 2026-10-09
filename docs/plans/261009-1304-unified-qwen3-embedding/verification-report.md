# Verification report — unified Qwen3-Embedding-0.6B (261009-1304)

Date: 2026-10-09. Mode: `hi-craft --full`. Status: **code complete; live A/B pilot PENDING operator run** (§3).

## 1. Scriptable evidence (all green)

### 1.1 Test suite

Full `pytest tests/` (post-fix-cycle): **1720 passed, 15 failed — all 15 verified pre-existing on HEAD** via
`git stash` baseline before the sweep (cplus_pilot_rollout ×6, ladybug_driver ×5,
mcp_acceptance_matrix csharp ×1, dev_ignore ×2, dev_lifecycle ×1).
New suites written for this plan:

| Suite | Scope |
|---|---|
| `tests/test_qwen3_query_policy.py` | P1: policy table (4 input classes), embed_query ↔ ST parity (`prompt_name="query"` + normalize), explore_service + doc query kwargs, ST-aware CPU fallback (never mean-pool), `local_files_only` pass-through |
| `tests/test_qwen3_ingest_policy.py` | P2: 12 CodeEmbedder modules load via the shared ST runtime (policy trust), ingest normalizes, chunk/truncate unchanged, local-path override, primary-sync + cobol policy |
| `tests/test_qwen3_embedding_marker.py` | P3: mismatch hard-error at local_qdrant / primary sync / doc chokepoints, `_mess` exemption, fresh+legacy stamp/backfill, sentinel-only re-stamp, search exclusion (scoped+unscoped), merge mismatch warning, doc reset clears sentinel, reset script dry-run/force/`--include-messages` |
| `tests/test_qwen3_defaults.py` | P4: constants twin, dev/MCP default wiring by source, doc env chain (`DOC_EMBEDDING_MODEL` after `EMBEDDING_MODEL`), device auto-detect matrix, transformers floor fail-fast |

Contract tests updated for intentional new behavior (sentinel metadata write +
server-side exclusion): `test_primary_vector_sync` (batch filter skips
`_embed_meta` upserts), `test_qdrant_query_support` (retrieve count +1 for the
cached sentinel probe), `test_unified_contract_doc_paths` (reset deletes project
points then the sentinel), `test_qdrant_project_scope` (filters carry
`must_not _embed_meta`), `test_embedding_runtime` (snapshot resolution
parameterized jina+qwen3), `test_primary_analyzer_vector_contract` /
`test_explore_project_scope` (fixtures → Qwen3).

### 1.2 Residual grep gate

`rg "jina-embeddings-v3|bge-m3" -g '!*.bak' -g '!docs/plans/**' -g '!docs/logs/**'`:

* Production `.py`: only `code-tiny/tools/common/embed_runtime.py:435` — a comment
  explaining the jina CPU-fallback rationale (legacy path is policy-owned).
* Tests: intentional legacy fixtures (jina policy class, bge-m3 "no prompt"
  class) — they pin the legacy behavior the plan requires to keep working.
* Docs: zero hits outside `docs/plans` + `docs/logs` (historical records kept).
* `*.bak` files untouched (gate excludes them; `ts_analyzer.py.bak` intact).

### 1.3 Behavior checks (unit level)

* Query parity: `embed_query(qwen3)` → ST `encode([text], prompt_name="query",
  normalize_embeddings=True)` — last-token pooling comes from the model config.
* Ingest parity: all 12 CodeEmbedder copies + primary sync + cobol load through
  `get_sentence_transformer` and encode `normalize_embeddings=True`, bare text.
* Marker: mismatch ingest raises `EmbeddingModelMismatchError` naming
  `reset_code_collection.py` (code) / `0_reset_all.py` (doc); `{base}_mess`
  never checked; sentinel never returned by `search_collection`; mismatched
  query warnings surface in `errors[]` (code) / `embedding_model_mismatch`
  (doc) without failing.
* Reset: `0_reset_all --project-id` deletes the sentinel; sentinel-only
  collections re-stamp; `reset_code_collection.py` dry-run deletes nothing,
  default keeps `_mess`.
* Fail-fast: `transformers<4.51` → clear RuntimeError with the pip command.
* Env chain: `arg → EMBEDDING_MODEL_PATH → EMBEDDING_MODEL → DOC_EMBEDDING_MODEL
  → default(Qwen3)`; doc device auto-detect (MPS on darwin / CUDA / CPU), dev
  doc-process env now sets `EMBEDDING_DEVICE`.

## 2. Coverage of the plan's "done" conditions

| Condition | Status |
|---|---|
| (a) all defaults resolve to Qwen3 via env chain | ✅ (P4 + `test_qwen3_defaults`) |
| (b) 3 query paths parity with ST `encode(prompt_name="query")` | ✅ stub-level (P1 tests) |
| (c) marker hard-error from every writer chokepoint | ✅ (P3 tests, 4 paths) |
| (d) reset clears sentinel, `_mess` intact, sentinel-only re-stamp | ✅ (P3 tests) |
| (e) A/B pilot EN/VI/JA | ⏳ **PENDING** — needs the model snapshot + live Qdrant (§3) |
| (f) cross-space cosine demo | ⏳ **PENDING** (§3) |
| (g) residual grep gate | ✅ (§1.2) |

## 3. Pending: live A/B pilot (operator run)

The Qwen3 snapshot is **not downloaded yet** (`LocalEntryNotFoundError` from the
HF cache on 2026-10-09) and no pilot project has been re-indexed. The plan
deliberately stops here: the fleet must not re-index before the gate passes.

Run [runbook.md](runbook.md) §0–§3, then record in the table below:

| Metric | Before (jina / bge-m3) | After (Qwen3) | Verdict |
|---|---|---|---|
| EN hit ratio (≥10 queries) | | | |
| VI hit ratio (≥5 queries) | | | |
| JA hit ratio (≥5 queries) | | | |
| identifier/code hit ratio (≥5) | | | |
| cross-space cosine (function ↔ doc paragraph) | n/a (different spaces) | | |
| embed latency per query (MPS/CUDA, ms) | | | |
| doc ingest time (pilot project) | | | |
| backup export paths | | | |

Gate: no per-bucket regression; cross-space neighbors meaningful; latency inside
the accepted envelope. On fail → runbook §3 rollback (import backups, env back
to legacy models, reset, re-ingest).

## 4. Adversarial review (full mode) — fix cycle 1

An isolated reviewer (failure-modes + code lens) scored the craft **7/10, zero
critical**, with 3 High + 7 Medium findings. All High and the actionable Mediums
were fixed in one cycle and pinned by new regression tests:

| Finding | Fix |
|---|---|
| Marker check failed OPEN on store errors (transient failure could launder a stale marker) | `embedding_marker.check` now fails closed — only the query-side `mismatch()` degrades soft; regression test asserts a timeout error does NOT re-stamp |
| transformers floor check never fired on ingest loads | moved into `get_sentence_transformer` (every ingest + query load passes through); regression test on the loader |
| Marker identity diverged under `CODE_EMBEDDING_MODEL_PATH` (phantom warnings / silent re-embeds) | new `effective_model_identity()`; analyzers stamp `embedder.model_source` (PATH-aware), primary sync + cobol + livingdoc apply the same override; regression test stamps through a sync with PATH set |
| `0_reset_all` skipped the sentinel delete at zero matched points (F2 corner) | sentinel delete is now unconditional for scoped resets; regression test added |
| `reset_code_collection.py` silent no-op on remote backends / `_mess` without flag | refuses `_mess` targets without `--include-messages`; exits loudly when nothing was dropped, pointing at `--project-id` |
| Legacy backfill laundering half-migrated collections | loud one-shot `[embed-marker]` notice when stamping a pre-marker collection holding >1 point (code + doc) |
| ST CPU fallback evicted all device keys (reload storm on persistent MPS failure) | evicts only the failing `(model, device)` key |
| Rollback procedure destroyed the restored backup | runbook + contract rollback rewritten: import + restart with old env, never reset-after-import; remote-mode export limitation documented |
| Chunk mean-pool broke the normalized-ingest invariant | `_mean_pool_chunks` re-normalizes averaged chunk vectors (12 copies) |
| Cross-process sentinel cache staleness | read cache now TTL-bounded (`EMBEDDING_MARKER_CACHE_TTL`, default 60s) |
| `_with_sentinel_exclusion` mutated caller-owned filters | clones via `model_copy(deep=True)` |
| `TypeError` fallback silently dropped `normalize_embeddings` | narrowed to `unexpected keyword argument` only (12 copies + `encode_texts`) |
| Test gaps: `_mess` existing-branch, zero-match reset | both covered now |

Not adopted (with reason): explore_service encode error-guard (reviewer rated
acceptable given the ST 2.7 floor).

Post-fix suite: **1720 passed, 15 failed (same pre-existing HEAD set)**.

## 5. Files touched (summary)

* Policy/runtime: `code-tiny/tools/common/embed_runtime.py` (policy, ST query
  path, floor check, `cache_folder`), **new** `embedding_marker.py`, **new**
  `model_defaults.py`, `primary_vector_sync.py`, `local_qdrant.py`,
  `qdrant_query_support.py`.
* Ingest: 12 CodeEmbedder copies (+vb via python import), 4 MCP backends,
  `cobol/qdrant.py`, livingdoc ×2 writers.
* Query: `explore_service.py`, 4 backends' merge path, doc `mcp_graph_rag.py`,
  `graphrag_query_langextract.py`.
* Defaults/env: `cortex_harness/dev.py`, `doc-tiny/embedding_utils.py`,
  `graphrag_ingest_langextract.py`, livingdoc defaults ×4, both
  `.env.example`, requirements floors (root + doc-tiny).
* Ops: **new** `code-tiny/scripts/reset_code_collection.py`,
  `doc-tiny/0_reset_all.py` (sentinel deletion), contract playbook, runbook.
* Docs: README/Design/mcp Readme/vb README/livingdoc README/skill examples/
  doc Readme (legacy literals → Qwen3, `TEXT_EMBEDDING_MODEL` removed).
