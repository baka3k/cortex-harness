# Research digest — BGE-M3 learned-sparse vectors + Qdrant hybrid RRF fusion

Created: 2026-10-11. Scope: add BAAI/bge-m3 sparse (learned lexical weights) vectors alongside the existing dense 1024-dim vectors, with dense+sparse RRF fusion, across code-tiny and doc-tiny.

Verification sources:
- Installed client: `.venv/lib/python3.12/site-packages/qdrant_client` **1.18.0** (matches `requirements.txt:1` pin). Model classes verified by import + signature; **local-mode behavior verified by an executed smoke test** (temp `QdrantClient(path=...)`: create mixed dense+sparse collection, upsert, RRF prefetch fusion, sparse-only query, plain-list-vector upsert, update_collection mutation attempt).
- FlagEmbedding: master sources fetched from GitHub (`inference/embedder/encoder_only/m3.py`, `finetune/embedder/encoder_only/m3/{modeling,runner}.py`) and the **PyPI 1.4.2 wheel inspected directly** (`/tmp/fe_extract`).
- `sparse_linear.pt` downloaded from HF `BAAI/bge-m3` and inspected with the repo's own venv torch.
- Repo snapshot dir listed to confirm artifact absence.

---

## Findings

### 1. Repo touchpoints confirmed (no surprises vs orchestrator brief)

- Code ingest chokepoint: `code-tiny/tools/common/local_qdrant.py:232-283` (`ensure_collection` — every code-side writer funnels here; `create_collection` at :270-274 passes `**_tuning_kwargs(store)` through, so a `sparse_vectors_config=` kwarg flows to the client).
- Query chokepoint: `code-tiny/tools/common/qdrant_query_support.py:70-120` (`search_collection` calls `store.query_points(collection, query=list(vector), limit, query_filter, with_payload=PAYLOAD_EXCLUDE_SELECTOR, with_vectors=False, using=vector_name?)` at :98-106) and `:123-136` (`merge_hits` — dedupe by point id keeping higher cosine score; **note: score-scale assumptions live here**).
- Collection-meta cache: `code-tiny/tools/common/qdrant_layout_cache.py:68-91` (`get_collection_meta` returns `Dict[name→size]` from `vector_sizes` only — see F7).
- Code query embed: `code-tiny/tools/common/embed_runtime.py:85-91` (bge-m3 policy: `backend=st`, `query_prompt=None`, `normalize=True`), `:468-505` (`embed_query` with LRU cache), `:580-628` (`get_sentence_transformer`, keyed `(model, device)`, offline-gated), `:508-527` (`_snapshot_complete` — the gate to extend for `sparse_linear.pt` presence), `:530-556` (`_offline_hub_load_guard`).
- Marker: `code-tiny/tools/common/embedding_marker.py` — `stamp` upserts the sentinel with a **plain list** vector (`"vector": [0.0]*vector_size`, :137); `enforce` (:186-234) blocks model mixing; sentinel hidden from search via `must_not` (:88-90).
- Doc ingest: `doc-tiny/graphrag_ingest_langextract.py:633-690` (`create_collection` — raw `client.create_collection(... vectors_config=qmodels.VectorParams(...))` at :677-679, marker stamp/enforce around it), paragraph encode at :706 (`embedder.encode([paragraph])[0]`), point built at :734-746 (`qmodels.PointStruct(..., vector=vector.tolist(), ...)` — plain list, one point per upsert call).
- Doc query: `doc-tiny/mcp_graph_rag.py:569` (`qdrant.query_points(collection_name, query=query_vector, limit=top_k, query_filter=...)` on `LocalQdrantStore`), `_query_encode` at :205-214 (ST encode with `st_query_encode_kwargs`), call sites :1215, :1334; `graphrag_query_langextract.py` loads ST the same way.
- Doc store: `doc-tiny/doc_local_qdrant.py:90-92` → the same `LocalQdrantStore(role=DOCUMENT)`; `doc-tiny/embedding_utils.py:9` (`DEFAULT_DOC_EMBEDDING_MODEL = "BAAI/bge-m3"`) and :17-33 (`st_query_encode_kwargs` — bge-m3 keeps bare encode; only qwen3 gets the prompt).
- Storage boundary: `cortex_harness/storage/qdrant.py:62` (`QdrantClient(path=path)` — **local mode**), `:128-139` (`create_collection(**kwargs)` passthrough → `sparse_vectors_config` supported with **no wrapper change**), `:159-170` (`upsert` accepts dict points → `qmodels.PointStruct(**point)`, vector passes through as dict/SparseVector), `:208-227` (`query_points(**kwargs)` passthrough → prefetch/fusion supported with **no wrapper change**).

### 2. Qdrant sparse-vector model classes (client 1.18.0, verified by import)

All available via the repo's existing `from qdrant_client.http import models as qmodels` import style:

- `qmodels.SparseVector(indices: list[int], values: list[float])` — exactly two fields.
- `qmodels.SparseVectorParams(index: SparseIndexParams | None, modifier: Modifier | None)`; `qmodels.SparseIndexParams(full_scan_threshold=8000, on_disk=False, datatype=None)`; `qmodels.Modifier` has `NONE` and `IDF`.
- `create_collection(..., vectors_config=..., sparse_vectors_config={"sparse": SparseVectorParams(...)})` — verified in the client signature.
- Point vectors in a mixed collection are dicts keyed by vector name; the unnamed dense default is key `""` (`DEFAULT_VECTOR_NAME`, `qdrant_client/local/local_collection.py:229,2340,2405`).

### 3. Local mode (the repo's only mode today) fully supports sparse + fusion — smoke-tested

Executed against a temp `QdrantClient(path=...)` in the repo venv:

- Collection with unnamed dense (`VectorParams` 4-dim COSINE) **+** named sparse `{"sparse": SparseVectorParams()}`; upsert of dict-shaped vectors `{"": dense, "sparse": SparseVector}` — OK.
- Hybrid query: `query_points(prefetch=[Prefetch(query=dense_list, using="", limit=10), Prefetch(query=SparseVector, using="sparse", limit=10)], query=FusionQuery(fusion=Fusion.RRF), limit=5)` — returned RRF scores (1/(2+rank) style). Local implementation: `qdrant_client/local/local_collection.py:707-761` (`query_points` resolves prefetches recursively), `:764-812` (`_prefetch`), `:802-860` (`_merge_sources` → `FusionQuery`/`RrfQuery`), using `qdrant_client/hybrid/fusion.py:7` (`reciprocal_rank_fusion`) and `:45` (`distribution_based_score_fusion`). `FusionQuery(fusion=Fusion.DBSF)` is also honored (`:827-830`).
- Sparse-only query: `query_points(query=SparseVector(...), using="sparse")` — OK (dot-product scoring, `local_collection.py:560-660`).
- IDF modifier exists in local mode too (`local_collection.py:569` rescores with per-vector doc frequencies); default `Modifier.NONE` needs no IDF state.

### 4. A sparse vector CANNOT be added to an existing collection in local mode — recreate required

`QdrantClient.update_collection(collection, sparse_vectors_config=...)` routes to `LocalCollection.update_sparse_vectors_config` (`qdrant_client/local/local_collection.py:2848-2852`), which **raises `ValueError: Vector sparse does not exist in the collection`** when the name was not declared at create time (smoke-test verified). Consequence: every existing bge-m3 collection must be recreated (drop + create with `sparse_vectors_config` + re-upsert) before sparse points/queries work. Recreate options:
- full re-ingest (text is in the payload on the doc side; code-side payloads also carry `text`), or
- a migration that scrolls old points with `with_vectors=True`, re-upserts the dense vectors into the new collection, and computes sparse only for points whose payload text can be re-encoded (see F10 for cost).
`RemoteQdrantStore` server-side behavior for update-with-sparse is **unverified** (not exercised here); treat as recreate-required for planning until proven otherwise (`unknown`).

### 5. Old points without a sparse vector are safe (dense keeps working)

`LocalCollection._add_point` (`local_collection.py:466-516`): for sparse slots missing in the point's vector dict it appends `empty_sparse_vector()` and marks the point deleted **per-vector** (`deleted_per_vector`), so a legacy dense-only point is invisible to sparse search but unaffected for dense. Smoke-tested: point `c` (dense only) never appeared in the sparse prefetch/fusion result. Also verified: a **plain-list** vector upsert (as used by the sentinel `embedding_marker.stamp:133-141` and `LocalQdrantWriter.upsert`) remains legal in a collection that has named sparse vectors — the list maps to `DEFAULT_VECTOR_NAME` (`local_collection.py:2405`), sparse slots get the empty/deleted treatment. So `ensure_collection`'s marker stamping needs **no change**.

### 6. RRF fusion is rank-based — no score calibration between dense and sparse

RRF score = Σ_sources 1/(k + rank_in_source), Qdrant default `k=2` (`docs https://qdrant.tech/documentation/search/hybrid-queries/`; client `models.Rrf` fields `k=None, weights=None`, weights available server v1.17+ and in local 1.18.0 `local_collection.py:814-822`). Dense cosine (0..1) and sparse dot (arbitrary magnitude, measured ~0.2-0.9 per matched token weight) never need to be comparable — only rankings matter. Prefetch `limit` per source defines candidate depth; Qdrant docs recommend prefetch limit ≥ final limit + offset. DBSF (score-based, z-normalized) is the alternative if RRF feels too coarse (`Fusion.DBSF`, implemented in local mode).

### 7. `vector_sizes` / layout cache / size-matching logic is untouched by sparse config

`local_qdrant.vector_sizes` (`local_qdrant.py:154-171`) reads only `info.config.params.vectors`; sparse config lives in `info.config.params.sparse_vectors` and is ignored — so `filter_collections_for_vector` (`qdrant_query_support.py:297-344`), `qdrant_layout_cache.get_collection_meta`, and `ensure_collection`'s dim guard all keep working unchanged for a mixed collection. `_mess` hash-vector collections and message_scan are out of scope (they never route through model policy; `local_qdrant.py:41`).

### 8. FlagEmbedding API (the reference implementation of the sparse head)

`pip install FlagEmbedding` would give 1.4.2, where **`BGEM3FlagModel` is an alias of `M3Embedder`** (`FlagEmbedding/inference/embedder/encoder_only/__init__.py`: `from .m3 import M3Embedder as BGEM3FlagModel`; also mapped in `inference/embedder/model_mapping.py:60-61` for `FlagAutoModel`). Usage:

```python
model = BGEM3FlagModel(model_name_or_path, use_fp16=True)
out = model.encode(sentences, batch_size=256, max_length=512,
                   return_dense=True, return_sparse=True, return_colbert_vecs=False)
# out["dense_vecs"]      -> np.ndarray [N, 1024], CLS-pooled, L2-normalized
# out["lexical_weights"] -> List[Dict[str(token_id), float]]  (str keys, w>0 only)
```

Source references (GitHub master, fetched 2026-10-11): `FlagEmbedding/inference/embedder/encoder_only/m3.py:312-460` (`encode_single_device`), `:443` (`token_weights = outputs['sparse_vecs'].squeeze(-1)`), `:354-372` (`_process_token_weights`).

### 9. Exact sparse math — reproducible without FlagEmbedding

Verified chain (`FlagEmbedding/finetune/embedder/encoder_only/m3/`):

1. `runner.py:82-86` — `sparse_linear = torch.nn.Linear(in_features=hidden_size(=1024), out_features=1)` (**bias default True**), loaded from `{model_dir}/sparse_linear.pt` via `torch.load(..., weights_only=True)` (`:88-95`).
2. HF artifact `sparse_linear.pt` (downloaded, inspected): **3,516 bytes**, state dict `{weight: [1, 1024] float16, bias: [1] float16}`.
3. `modeling.py:128` — `token_weights = torch.relu(self.sparse_linear(hidden_state))` applied to the **raw `last_hidden_state`** of the XLM-RoBERTa encoder (`modeling.py:557` in the inference class: `last_hidden_state = self.model(**text_input).last_hidden_state`) — no pooling, no L2 norm on the sparse path. So per token: `relu(h[1024] · Wᵀ + b)`.
4. Post-processing (`inference/m3.py:354-372` `_process_token_weights`): build `{str(input_id): max(weight)}` keeping only `w > 0`, excluding special-token ids (`cls_token, eos_token, pad_token, unk_token` from `tokenizer.special_tokens_map`); repeated token ids keep the max weight.
5. Dense path is unchanged CLS + L2-normalize (`modeling.py:99-101` `_dense_embedding` cls; `:277` `F.normalize`) — exactly what the repo's ST policy already produces (`embed_runtime.py:85-91`).

Therefore a **~30-line port** (tokenize → encoder forward → `relu(h @ W.T + b)` → dict) using the already-cached SentenceTransformer's underlying encoder + a downloaded 3.5 KB `sparse_linear.pt` reproduces the reference weights. Access to the token-level hidden states from the repo's cached `SentenceTransformer` goes through its underlying encoder module (`st_model[0].auto_model` — `inferred`, standard ST module layout; confirm the exact attribute at implementation time).

### 10. Measured sparsity and storage estimate (real run on the repo's cached snapshot)

Executed with the repo venv on cached snapshot `~/.cache/huggingface/hub/models--BAAI--bge-m3/snapshots/5617a9f…` + the downloaded head, ~500-char English code-doc chunk:

- 111 XLM-R tokens → **57 unique non-zero lexical weights** (~0.5/token; top weights ≈ 0.22-0.26).
- Qdrant `SparseVector` payload ≈ `n_nonzero × (4 B index + 4 B value)` ≈ **456 B raw** vs 4,096 B for the dense float32(1024) → sparse adds ~11% of dense per point in raw payload bytes; with index/persistence overhead budget **< 1.5 KB/point** (`inferred` — overhead factor, not measured).
- Server-side memory: sparse vectors live in a separate sparse (inverted) index; `SparseIndexParams.on_disk=False` (default) keeps postings in RAM, `on_disk=True` moves them to disk (`docs https://qdrant.tech/documentation/manage-data/vectors/`, "Sparse Vectors" section). In **local mode** there is no real inverted index — vectors persist in sqlite and are scored brute-force, so RAM impact at this repo's scale is negligible (`inferred` from local-mode architecture).
- Query-side lexical weights for short queries: proportionally smaller (a 10-word query ≈ 15-25 tokens → ~10-15 entries, ~100 B).

### 11. Query-side sparse has no asymmetry for bge-m3

`M3Embedder` (BGEM3FlagModel) defaults `query_instruction_for_retrieval=None` for bge-m3 and query/passage differ **only in max_length** (both default 512; `inference/m3.py:31-32,51-54`). The same encode path (tokenize → encoder → relu head → dict) produces query and document lexical weights. This matches the repo's existing bge-m3 query policy (bare ST encode, normalized dense — `embed_runtime.py:85-91`, `embedding_utils.py:17-33`), so the sparse query encode can hang off the same `embed_query` chokepoint + LRU cache pattern (cache key would need a `(model, text, "sparse")` dimension or a parallel cache).

### 12. Dependency weight: FlagEmbedding pulls a lot; the port pulls nothing

FlagEmbedding 1.4.2 `requires_dist` (PyPI JSON): `torch>=1.6.0`, `transformers>=4.44.2,<6.0.0` — both compatible with the repo (torch unpinned @2.14.0, transformers `>=4.51,<4.56` @4.55.4, `requirements.txt:38-39`) — **but** it also hard-requires `datasets>=2.19.0`, `accelerate>=0.20.1`, `peft`, `ir-datasets`, `sentencepiece`, `protobuf` (`sentence-transformers` too). That is a heavy footprint for one file's math. The DIY port (F9) adds **zero new packages**: `huggingface_hub` (already present via sentence-transformers) can `hf_hub_download(repo_id="BAAI/bge-m3", filename="sparse_linear.pt")`.

### 13. The cached local snapshot lacks both heads

`~/.cache/huggingface/hub/models--BAAI--bge-m3/snapshots/` contains `5617a9f…` (ST layout: `1_Pooling`, tokenizer, `pytorch_model.bin`) and `9a0624b…` (`model.safetensors`) — **no `sparse_linear.pt`, no `colbert_linear.pt`**. Any implementation must (a) download `sparse_linear.pt` once (3.5 KB) beside the snapshot or into a known cache dir, (b) extend the offline gate (`embed_runtime._snapshot_complete`, `embed_runtime.py:513-527`) so a complete snapshot = dense + sparse head, mirroring the existing "cached model must never ping huggingface.co" contract (`embed_runtime.py:562-567`).

### 14. Where the chokepoints and seams are (per pipeline)

Code side (search): `qdrant_query_support.search_collection:70-120` is the single place the dense `query=list(vector)` call is made; a hybrid variant would replace it with a prefetch+`FusionQuery` call when the collection layout says "sparse available" and the model policy says "sparse-capable". `merge_hits:123-136` merges ACROSS collections by max score — with RRF the per-collection fused scores are rank-based and comparable enough for cross-collection max-merge, but the mixed scale (raw cosine for legacy collections vs RRF for hybrid ones) makes cross-collection `merge_hits` ordering ill-defined if some collections fuse and others don't (see Risks).

Doc side (search): `mcp_graph_rag.py:569` (raw `query_points`) and the `.search()` branch just above it (`qdrant.search(query_vector=...)`) are the two dense-only call shapes; `graphrag_query_langextract.py` consumes `_query_encode`-produced vectors separately. Both need the same layout probe + fusion treatment, or a shared helper.

Doc side flat-tree twin: `doc-tiny/embedding_utils.py` must stay in sync with code-tiny policy constants (its own docstring says so, :11-12) — add the sparse marker/head-resolution twin there.

Prior-plan conventions observed (`docs/plans/261009-1304-unified-qwen3-embedding/`): `plan.md` + `phase-NN-*.md` + `flows.md` (end-to-end diagrams with file:line) + `research-digest.md` + `red-team.md` + `verification-report.md`; findings numbered with path:line citations; changes mapped to phases in tables. This digest follows that numbering style.

---

## Conventions to follow

1. Route every behavior change through the existing chokepoints — `local_qdrant.ensure_collection` (code collections), `graphrag_ingest_langextract.create_collection` (doc collections), `qdrant_query_support.search_collection` (code search), `embed_runtime.embed_query/model_policy` (query encode), `embedding_marker` (identity) — do not fork per-backend copies (the module docstrings in `qdrant_query_support.py:1-14` mandate this).
2. Keep doc-tiny's `embedding_utils.py` twins in sync with any new code-tiny policy constant (sparse marker, head filename).
3. Extend `_snapshot_complete` offline gating rather than introducing network access on cached loads; new downloads go through `hf_hub_download` with `local_files_only` probes.
4. Respect the sentinel: never stamp the sentinel with a dict vector that includes sparse (plain list stays legal — F5), and keep the `must_not` exclusion on all new query shapes (`qdrant_query_support.py:53-67`).
5. `_mess` hash-vector collections stay out of model-policy scope (`MESSAGE_COLLECTION_SUFFIX`, `local_qdrant.py:41`).
6. Tests follow the repo layout (`code-tiny/tools/common/test_*.py`, `doc-tiny/tests/`) and the prior plan's budget style (phase files list per-phase test counts).
7. Name the sparse vector one fixed constant (e.g. `"sparse"`) shared by both pipelines; the dense unnamed default keeps key `""` in dict-shaped vectors.

## Risks / open questions

- **unknown** — Server-mode (`RemoteQdrantStore`) `update_collection(sparse_vectors_config=...)` on an existing populated collection: not tested here; local mode definitively refuses (F4). Assume recreate for both modes until a remote probe says otherwise.
- **inferred** — Accessing token-level hidden states via `st_model[0].auto_model` from the cached SentenceTransformer; exact module attribute to confirm during implementation (F9.5). Alternative: a forward hook or loading a second plain `AutoModel` (duplicating weights in RAM — avoid).
- **unknown** — Whether pre-261009 collections' payloads reliably retain enough text to re-encode sparse during a backfill migration (code-side narrowed payloads still store `text`, but older layouts may vary); if not, backfill degrades to full re-ingest.
- **open** — Cross-collection `merge_hits` semantics when a project mixes hybrid (RRF scores) and legacy (cosine scores) collections; needs a decision: fuse-everything (dense-only collections get a synthetic dense prefetch → RRF too) or per-collection-mode scores with a documented ordering caveat.
- **open** — `Modifier.IDF` on the sparse vector (Qdrant rescales by doc frequency; SPLADE-style). BGE-M3 weights are already learned; IDF is a tuning knob, default `Modifier.NONE` (recommended `inferred`).
- **open** — Prefetch limit defaults (candidate depth per source) and whether an env override like the existing `QDRANT_HNSW_EF` pattern is wanted.
- **risk** — fp16 head artifact on CPU/MPS: cast `sparse_linear.pt` weights to float32 at load (repo runs MPS/CPU; measured run used fp32-cast weights and matched expected magnitudes).
- **risk** — Ingest throughput: sparse requires the token-level forward the dense ST `encode()` does not expose, so the sparse head computation is an extra pass over the encoder unless fused into one forward with pooling done in-house. For doc-tiny's one-point-per-upsert loop (`graphrag_ingest_langextract.py:746`) this doubles per-paragraph encoder calls unless batched or computed in the same forward as the dense vector.
- **risk** — Adding `sparse_vectors_config` at creation while `vector_size` check/marker logic is untouched means a collection stamped before this feature (dense-only, no sparse config) is indistinguishable by marker from a sparse-enabled one; extend `_embed_meta` payload (e.g. `sparse_vector: "sparse"`) or probe layout at query time (F7-style) — decision for the plan.

## Recommended approaches

1. **DIY sparse head, no FlagEmbedding**: `hf_hub_download` the 3.5 KB `sparse_linear.pt`, implement `relu(h @ Wᵀ + b)` + special-token mask in `embed_runtime` (policy-gated on `bge-m3`), reusing the cached SentenceTransformer's encoder — zero new dependencies (F9, F12).
2. **Named sparse vector at creation, recreate for existing**: add `sparse_vectors_config={"sparse": SparseVectorParams()}` inside `ensure_collection` / doc `create_collection`; treat existing collections as a recreate+backfill phase since local mode cannot mutate (F4, F5).
3. **Fusion inside the chokepoints**: extend `search_collection` (code) and the doc query path with a layout-probed hybrid `query_points(prefetch=[dense using="", sparse using="sparse"], query=FusionQuery(Fusion.RRF), limit=top_k)`, keeping the legacy dense-only call for collections without the sparse vector (F3, F14).
