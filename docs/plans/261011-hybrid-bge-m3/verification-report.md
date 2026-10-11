# Verification report — Hybrid Retrieval BGE-M3 pha 1 (dense + sparse RRF)

Date: 2026-10-11. Mode: hi-craft --full. Flag state at close: **`EMBED_HYBRID`
default OFF** (flip is a post-A/B operator decision, AC7).

## 1. Scriptable evidence (all green)

### 1.1 Test suites (new, this plan)

| Suite | Scope | Result |
|---|---|---|
| `tests/test_hybrid_sparse_runtime.py` | P1: flag×family table (11 cases incl. doc twin parity), golden fidelity vs reference FlagEmbedding output (4 texts: EN chunk / identifier / VI / JA — exact token-id sets + weights to 4 decimals), missing-head fail-fast (actionable RuntimeError, zero network), real-snapshot offline encode subprocess (`HF_ENDPOINT=http://127.0.0.1:9`), dense byte-parity under flag, query-pair caching | 11 passed + 15 subtests (golden/offline tests skip when the snapshot or head is not cached) |
| `tests/test_hybrid_ingest.py` | P2: schema on/off (sparse_vectors_config only when flag on), dual-vector upsert at the analyzer writer funnel + primary_vector_sync, legacy dense-only degradation on collections that never declared sparse (local mode hard-errors otherwise — smoke-tested), `_mess` exemption, marker `hybrid` stamp/flip/mismatch-unchanged, dev.py wiring, flag-default-off | 16 passed |
| `tests/test_hybrid_query_fusion.py` | P3: all-or-none dispatch matrix (8 rows incl. `_mess` + empty fan-out), fused query args (prefetch `using=""`/`"sparse"`, `FusionQuery(RRF)`, sentinel `must_not` on both channels, `EMBED_FUSE_K`), RRF merge dedupe, response-shape unchanged, encode-once | 11 passed + 8 subtests |
| `tests/test_hybrid_backfill.py` | P4: recreate round-trip on REAL local Qdrant (payloads verbatim, dense+sparse halves, `hybrid=true` stamp), second-run refusal without `--force`, `--dry-run` non-mutation, `--yes` guard | 4 passed |

Full-repo suite status: see §3.

### 1.2 Golden fidelity (AC1) — the fixture story

`tests/fixtures/bge_m3_sparse_golden.json` was generated from the **true
reference** (FlagEmbedding 1.4.2 wheel, its
`EncoderOnlyEmbedderM3ModelForInference._sparse_embedding(return_embedding=False)`
+ `_process_token_weights` semantics, real `sparse_linear.pt` head). Two
reference-implementation traps were found and documented during generation:

- FlagEmbedding's high-level `BGEM3FlagModel.encode(return_sparse=True)`
  lexical-weights path is unreliable at this version (shape quirk between the
  vocab-sized embedding and the per-token zip) — the modeling layer is the
  authoritative reference.
- `get_model` random-initializes BOTH heads unless **both** `colbert_linear.pt`
  and `sparse_linear.pt` exist in the snapshot — head files must be complete
  before any reference comparison (the runtime port only needs
  `sparse_linear.pt`).

The port (`embed_runtime.encode_sparse`) matches the reference exactly
(token-id sets equal; weights ≤ 1e-4 apart, fp32 math).

### 1.3 Local-mode constraints verified by execution

- `update_collection(sparse_vectors_config=...)` on a dense-only collection
  raises (`ValueError`) — recreate is the only upgrade (digest F4 confirmed).
- Upserting a dict vector with a `sparse` slot into a dense-only collection
  raises `Wrong input: Not existing vector name error: sparse` — hence the
  writer-side capability probe (`collection_supports_sparse`) that degrades
  to dense-only instead of failing the ingest (found during this build; not
  in the original digest).
- A plain-list sentinel upsert stays legal in a hybrid collection; legacy
  dense-only points remain matchable via the dense prefetch (digest F5).

## 2. A/B smoke results (AC7 input, pilot-scale)

Harness: `docs/plans/261011-hybrid-bge-m3/ab_harness.py` (temp local Qdrant,
real `BAAI/bge-m3` on CPU, 4 golden-fixture docs, top-3, prefetch depth 64).
Raw numbers: `ab_results.json` next to this file.

| Query | dense top-3 hits truth | fused top-3 hits truth | top-3 overlap | dense ms | fused ms |
|---|---|---|---|---|---|
| EN paraphrase 1 | Y | Y | 3/3 | 1.01 | 1.50 |
| EN paraphrase 2 | Y | Y | 3/3 | 0.54 | 1.41 |
| identifier set | Y | Y | 3/3 | 0.53 | 1.36 |
| single identifier | Y | Y | 3/3 | 0.54 | 1.36 |
| VI paraphrase | Y | Y | 2/3 | 0.52 | 1.30 |
| JA paraphrase | Y | Y | 3/3 | 0.54 | 1.32 |

Reads:

- **Quality: no regression at smoke scale** — ground truth holds rank-1 in
  both modes on every query class (EN/VI/JA/identifier). The VI query shows
  fused re-ranking (overlap 2/3) with truth still rank-1.
- **Latency**: fused ≈ 2–3× dense on local mode (two prefetch channels +
  RRF over brute-force scoring); absolute cost ~1.4 ms per query at this
  scale. Server-mode numbers will differ; refresh on the pilot corpus.
- **Storage**: sparse adds ~0.5 KB/point raw (~11% of a dense float32 1024
  vector), consistent with the research-digest measurement (F10).
- **Gate**: this is the smoke-scale harness, NOT the full A/B. The
  operator gate (same query sets against a real project corpus) must pass
  before considering `EMBED_HYBRID=1` as default — per plan AC7, the flag
  ships OFF.

## 3. Full suite

Run post-implementation: see the hi-log entry / commit message for the final
counts. Pre-existing failures (if any) are baselined against `git stash`
HEAD in the hi-craft run log — none were introduced by this plan.

## 4. Deliverables map

| AC | Evidence |
|---|---|
| AC1 (sparse encode parity) | golden fidelity test §1.1/§1.2; dense byte-parity test |
| AC2 (offline fail-fast) | missing-head RuntimeError test + offline subprocess test |
| AC3 (ingest dual-vector everywhere) | `test_hybrid_ingest.py` (16) + writer/cobol/livingdoc/doc chokepoints |
| AC4 (all-or-none dispatch) | `FusionDispatchTests` matrix; response shape unchanged |
| AC5 (RRF merge correct) | `FusionQueryArgsTests` + `merge_rrf_scores_dedupe` |
| AC6 (backfill recreate) | `test_hybrid_backfill.py` on real local mode |
| AC7 (runbook + flag off) | contract runbook section "Hybrid retrieval enablement"; default-off asserted by `DevEnvWiringTests` |

## 5. Known limitations / follow-ups

- ColBERT multi-vector deferred (per plan scope); `colbert_linear.pt` is NOT
  downloaded by `prefetch_hybrid_heads.py`.
- `EMBED_HYBRID` + non-bge family = silent no-op with a one-time warning
  (documented, tested).
- RemoteQdrantStore server-side add-sparse behavior remains unverified —
  provisioning creates sparse config only for NEW collections; recreate
  remains the upgrade path for existing ones in both modes.
- The backfill script's dense re-encode is byte-different from originals
  under MPS (low-bit); quality comparison is top-k overlap per runbook, and
  `--yes` guards the destructive drop (payload backup jsonl is kept).
