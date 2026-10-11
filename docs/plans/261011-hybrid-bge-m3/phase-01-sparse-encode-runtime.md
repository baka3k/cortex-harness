# Phase 01 — Sparse encode runtime: đầu ra lexical weights + head acquisition + golden fidelity (code + doc)

> Tham chiếu: [plan.md](plan.md) AC1, AC2 · Prediction report risk H1 (thiếu head files offline), H2 (sparse fidelity).

## Goal

`embed_runtime` cung cấp sparse encode (cho cả ingest và query) từ **cùng forward pass** với dense, tải được head files qua audit path hiện có, và fail fast offline-first khi thiếu. Doc-tiny có twin helper tương đương.

## Decision gate (ĐÃ CHỐT theo research-digest §5–§7)

**Port `sparse_linear.pt` — KHÔNG thêm FlagEmbedding.** Digest xác minh: head là `Linear(1024→1, bias=True)` fp16, file chỉ **3,516 bytes**; math = `relu(Linear(last_hidden_state))` trên token embeddings thô; post-process: giữ w>0, loại special tokens (cls/eos/pad/unk), max per repeated token id. Port ~30 dòng, zero new dependency. FlagEmbedding 1.4.2 (`BGEM3FlagModel` = alias `M3Embedder`) kéo theo datasets/accelerate/peft/ir-datasets — heavy, không đáng. Bất chấp: **golden test bắt buộc** so sparse vector với output tham chiếu (ghi fixture 1 lần trên máy có mạng vào `tests/fixtures/`).

## Changes

1. `code-tiny/tools/common/embed_runtime.py`:
   - Thêm `hybrid_enabled()` — đọc env `EMBED_HYBRID` (default `0`; truthy: `1/true/yes/on`), chỉ có ý nghĩa khi `model_policy()` nhận diện family `bge-m3` (family khác → hybrid no-op, warning 1 lần).
   - Thêm `encode_sparse(model_source, texts, device) -> List[Tuple[List[int], List[float]]]` (indices + values per text, drop weight ≤ 0, loại special tokens, max per id): **token-level forward riêng** qua first module của ST (`tokenize` → `auto_model` forward → `last_hidden_state`) — không tái dùng `model.encode()` (ST không expose hidden states; giữ dense path nguyên vẹn để AC1 "dense không đổi bit" khả thi). Sparse forward riêng nghĩa là ingest hybrid tốn ~2× embedding compute — chấp nhận được, ghi chú runbook.
   - Head load đi qua `resolve_embedding_cache`/`_st_local_files_only` semantics: snapshot đủ → offline hoàn toàn; thiếu head → RuntimeError actionable ("run prefetch_hybrid_heads.py / snapshot thiếu sparse_linear.pt"), **không bao giờ** HEAD-retry storm (bài học commit b5b21ee). Cache loader theo key riêng, không đụng `_SENTENCE_TRANSFORMER_CACHE`.
   - Query side: `embed_query_pair(text)` — trả `(dense, sparse)`; dense đi đúng đường `embed_query` hiện có (LRU `(model, text)`), sparse cache dict riêng. Không đổi signature `embed_query` (caller cũ không vỡ).
2. `code-tiny/tools/common/embedding_runtime.py` — `_missing_files()` nhận biết `sparse_linear.pt` khi env hybrid bật (chỉ thêm kiểm tra, không đổi behavior khi flag tắt).
3. `doc-tiny/embedding_utils.py` — twin helper `hybrid_enabled()` + converter lexical_weights → `(indices, values)` (flat-tree không import code-tiny — cùng pattern `QWEN3_EMBEDDING_MARKER` hiện có).
4. Prefetch tiện ích: `code-tiny/scripts/prefetch_hybrid_heads.py` — `sparse_linear.pt` (3.5KB; colbert head KHÔNG cần ở pha 1) + verify `_missing_files`.

## Acceptance criteria

- AC1: `encode_sparse` trả indices/values khớp golden fixture (so khớp exact token-id + weight với reference output đã ghi nhận); dense output **không đổi bit** so với trước khi bật hybrid.
- AC2: Snapshot đủ → load zero network (subprocess test với `HF_ENDPOINT=http://127.0.0.1:9` như `RealCacheOfflineProbeTests`); thiếu head + flag bật → RuntimeError chứa tên file bị thiếu và lệnh prefetch, không có network call.
- AC3: `EMBED_HYBRID` unset/`0` → mọi path hiện trạng byte-parallel (suite green không sửa assert hành vi cũ); family không phải bge-m3 + flag bật → no-op + warning.
- AC4: Doc twin helper quyết định giống hệt code side trên cùng model name (parity test bảng).

## Test scope (test-budget mapping)

- AC1 → 1 golden test (fixture mẫu cố định, skip khi thiếu fixture); AC2 → 2 test (offline-complete, missing-head fail-fast); AC3 → 1 test bảng flag × family; AC4 → 1 parity test.
- Kỳ vọng: ~5–6 test functions mới. Size **M**.

## Out of scope

- Không ghi vào Qdrant (P2), không đụng query path (P3), không backfill (P4), không ColBERT.
