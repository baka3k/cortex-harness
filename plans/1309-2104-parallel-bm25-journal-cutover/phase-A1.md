# Phase A1: BM25 auto-corpus trong IntelligentRetrievalEngine (default OFF)

**status: planned** · **track: A (query path)** · **độc lập với Track B**

## Mục tiêu

BM25 signal fire thật trong search pipeline mà không cần caller inject
`bm25_ranker`: engine tự build corpus từ seed candidates. **Mặc định OFF**
để không đổi behavior — chỉ sẵn sàng qua flag.

## Thiết kế

1. **Corpus text source** (chốt trước khi code): candidates từ Qdrant/graph
   có field identifier `qualified_name`, `name`, `kind`, `file_path`
   (`intelligent_retrieval.py:_qdrant_hit_to_candidate`). Corpus text =
   `' '.join(filter(None, [qualified_name, name, kind, file_path]))` —
   tokenizer `[a-z0-9_]+` của BM25Ranker sinh cho identifier nên không cần
   prose notes. Ghi quyết định vào test golden nhỏ (5 candidates → text
   chính xác từng char).
2. **Vị trí trong pipeline** (`search()`): sau step 2 (merge seeds), TRƯỚC
   step 2b hiện tại. `bm25_ranker=None` + auto ON → tạo `BM25Ranker()`
   (rust-backed qua `rust_bridge`), `build_index(candidate_documents,
   text_field="_bm25_text", id_field="node_id")`, score, inject theo đúng
   logic 2b hiện có (candidate có sẵn và mới-added khi không project-scoped).
3. **Flag**: env `CORTEX_BM25_AUTO` — `0` (mặc định) OFF, `1` ON; +
   constructor param `auto_bm25: Optional[bool] = None` override env (API
   additive, caller cũ không đổi). BM25 weight giữ `bm25_weight=0.15` hiện có.
4. **explore_service**: chưa đụng ở phase này (A2 mới flip).

## Gates

- [ ] Unit test corpus text golden (identifier fields → text chính xác).
- [ ] Unit test auto path: OFF → không có signal `bm25` trong candidates;
      ON → có signal, scorer_weights nhận weight `bm25` (dùng fix a3d5f2d).
- [ ] Test cả 2 bridge mode (rust + `CORTEX_RETRIEVAL_RUST=0`) — parity.
- [ ] `PYTHONPATH=code-tiny pytest code-tiny/tests -q` xanh toàn bộ.
- [ ] Không đụng `explore_service` ở phase này.

**Trạng thái:** planned
