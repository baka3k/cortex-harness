# Phase 03 — Query-side fusion: capability detection + RRF tại chokepoint + fallback trong suốt (code + doc)

> Tham chiếu: [plan.md](plan.md) AC4, AC5 · Prediction report risk Medium (merge RRF cross-collection).
> Nguyên tắc: **thay đổi sống tại `qdrant_query_support`** — 4 backend MCP (cplus/android/java/fastmcp) đã delegate về đây, không có logic hybrid riêng lẻ.

## Goal

Query tự động chọn fused (RRF) hay dense-only theo capability của từng collection; response shape `results`/`errors[]` không đổi; sentinel exclusion + mismatch warning giữ nguyên.

## Changes

1. `code-tiny/tools/common/qdrant_layout_cache.py` — `get_collection_meta` mở rộng trả thêm `has_sparse` (đọc `info.config.sparse_vectors` — đường khác `config.params.vectors` mà `vector_sizes` đang đọc nên dim-guard không đụng; cache cùng TTL hiện có; refetch-on-miss giữ nguyên pattern `:83-93`). Fan-out caller tính capability = AND(`has_sparse` mọi collection đã chọn).
2. `code-tiny/tools/common/qdrant_query_support.py`:
   - `search_collection` (`:70-120`): khi `hybrid_enabled()` VÀ query mang sparse vector → `query_points(collection, prefetch=[Prefetch(query=dense, using="", limit=fuse_k), Prefetch(query=SparseVector, using="sparse", limit=fuse_k)], query=FusionQuery(fusion=Fusion.RRF), limit=top_k, ...)`; ngược lại → nhánh hiện trạng nguyên vẹn (đường "default vector, không using" lẫn "named dense vector" của `filter_collections_for_vector` đều giữ). Digest xác minh fusion hoạt động local mode; `RrfQuery(k=, weights=)` là tùy chọn tune sau, pha 1 dùng `FusionQuery` thuần.
   - **All-or-none trên fan-out (red-team F1):** capability được tính trên **mức tối thiểu của fan-out** — chỉ khi MỌI collection được chọn đều có sparse thì toàn bộ fan-out chạy fused; nếu một collection dense-only → toàn fan-out query dense thuần. Collection `{base}_mess` (hash vectors) luôn tính là không-hybrid (red-team F6). Lý do: `merge_hits` so score trực tiếp, RRF và cosine không cùng scale — trộn trạng thái trong một fan-out là sai ngữ nghĩa. Capability đọc qua layout cache (đã có TTL).
   - `fuse_k` (prefetch depth mỗi channel, default `max(4×top_k, 64)`, env `EMBED_FUSE_K`).
   - Sentinel exclusion (`_with_sentinel_exclusion`) áp cho cả nhánh fusion; `hnsw_ef` env giữ cho dense prefetch.
   - `merge_hits` (`:123-136`): không đổi logic — thêm docstring ghi rõ precondition "cùng một scale trong một lần merge" (được bảo đảm bởi all-or-none).
   - Signature mở rộng backward-compatible: `vector` + `sparse_vector: Optional[Tuple[List[int], List[float]]] = None`.
3. Callers cấp backend truyền sparse: các chuỗi query backend lấy vector từ `embed_query` — đổi thành lấy cặp `(dense, sparse)` qua helper mới `embed_query_pair()` (P1) khi hybrid bật; `explore_service._make_embedder` tương tự (trả fused encode cho fan-out).
4. Doc side: `doc-tiny/mcp_graph_rag.py` `_query_encode`/`query_points` (`:569`) + `graphrag_query_langextract.py` (`:179-180`) — mirror: capability check qua collection info, fusion khi có sparse, bare ST dense encode giữ nguyên khi không.
5. Fallback matrix (bắt buộc test): flag ON × fan-out toàn hybrid → fused; flag ON × fan-out có ≥1 collection dense-only → **toàn fan-out dense** (all-or-none, không trộn scale); flag OFF → hiện trạng; point cũ trong collection hybrid (thiếu sparse) → vẫn match được qua dense prefetch (digest: sparse trống per-vector marker, không error).

## Acceptance criteria

- AC4: Dispatch matrix all-or-none (flag × fan-out capability, parameterized); response shape không đổi (schema test trên hits dict).
- AC5: Fusion query gửi đúng prefetch/using/FusionQuery (stub store ghi nhận args; dense prefetch `using=""`); sentinel `must_not` có mặt trong cả hai prefetch filter; `merge_hits` trả đúng top_k dedupe với score đồng nhất.
- AC4b: Backend callers + explore_service + doc paths nhận sparse qua helper, không tự encode lần 2 (đếm encode calls trên stub).
- Hiện trạng: flag OFF → toàn bộ suite query hiện có green không sửa assert.

## Test scope (test-budget mapping)

- AC4 → 1 bảng dispatch (8 hàng) + 1 schema test; AC5 → 2 test (fusion args, merge RRF); AC4b → 1 test fan-out encode-once; fallback dense-only → 1 test.
- Kỳ vọng: ~6–7 test functions. Size **M–L** (nhiều call sites nhưng chung helper).

## Out of scope

- **`intelligent_retrieval` giữ nguyên dense**: nó seed kênh semantic bằng `query_points` TRỰC TIẾP từ `local_qdrant` (`intelligent_retrieval.py:169`), không đi qua `search_collection` — fusion không lan vào graph-fusion engine. Scorer của nó đã min-max normalize từng signal (`signal_normalizer`) nên nếu sau này muốn kênh semantic hưởng hybrid thì chỉ thứ hạng seeds đổi, công thức combine không vỡ — là follow-up riêng sau A/B.
- Graph query thuần (FalkorDB/Cypher, `tools/graph/`, GraphExpander, drivers): không đụng — plan này chỉ ở tầng Qdrant vector.
- Không đụng ingest (P2); không đổi `filter_collections_for_vector` selection logic (chỉ bỏ qua has_sparse khi chọn); không ColBERT rerank; không đổi tool contract MCP.
