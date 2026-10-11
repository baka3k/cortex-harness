---
title: "Hybrid Retrieval BGE-M3 pha 1: dense + sparse (RRF) cho cả code-tiny và doc-tiny"
status: completed (2026-10-11) — implemented via hi-craft --full; verification: verification-report.md; EMBED_HYBRID ships OFF (flip là quyết định post-A/B)
created: 2026-10-11
mode: hi-plan --full
scope: "Khai thác thêm sparse output (learned lexical weights) của BGE-M3 — model đang là unified default — cạnh dense 1024-dim hiện có, fuse server-side bằng Qdrant Query API (prefetch dense+sparse → Fusion.RRF). (1) encode runtime: đầu ra sparse từ cùng forward pass, head file acquisition qua audit path + offline gate, golden fidelity test; (2) ingest: named sparse vector tại mọi writer chokepoint (CodeEmbedder/primary_vector_sync/cobol/livingdoc + doc ingest), sparse_vectors_config trong ensure_collection/create_collection/provision; (3) query: capability detection qua collection info (mở rộng qdrant_layout_cache), fusion tại search_collection + doc query paths, fallback dense-only trong suốt cho collection chưa có sparse; (4) marker: trường `hybrid` trong sentinel _embed_meta; (5) backfill script tái dùng payload text + runbook A/B (EN/VI/JA/identifier) trước khi flip default. KHÔNG làm trong plan này: ColBERT multi-vector (defer — chỉ rerank top-50 nếu sau này có bằng chứng), thay đổi MAX_EMBED_CHARS/chunking (kế hoạch riêng), đổi model/dimension."
blockedBy: []
blocks: []
relatedPlans:
  - 261009-1304-unified-qwen3-embedding
  - 260829-2322-vector-search-query-optimization
  - 260728-0000-unified-ingest-query-contract
sources:
  - docs/plans/261011-hybrid-bge-m3/prediction_report_20261011-154022.md
  - docs/plans/261011-hybrid-bge-m3/research-digest.md
  - docs/plans/261011-hybrid-bge-m3/red-team.md
delegation:
  - "role=researcher mode=spawn scope=FlagEmbedding sparse API + Qdrant 1.18 sparse/fusion API + touchpoint inventory -> research-digest.md status=done"
scopeChallenge:
  q1_pha: "P1 dense+sparse RRF only — ColBERT defer (prediction report risk #storage), MAX_EMBED_CHARS tách plan riêng"
  q2_compat: "Sống chung không ép re-index: collection cũ dense-only fallback trong suốt; backfill script tùy chọn; flip default sau A/B"
  q3_dependency: "Decision gate phase-01: FlagEmbedding nếu nhẹ/không xung đột pin; ngược lại port sparse_linear.pt + golden test"
phases:
  - phase-01-sparse-encode-runtime.md
  - phase-02-ingest-sparse-write.md
  - phase-03-query-fusion-capability.md
  - phase-04-backfill-ops-verification.md
successCriteria:
  - "EMBED_HYBRID=1: ingest ghi cả dense + sparse tại mọi chokepoint; query trả kết quả fused RRF với quality A/B không regress (EN/VI/JA + identifier sets)"
  - "EMBED_HYBRID=0 hoặc collection dense-only: hành vi byte-parallel với hiện trạng (không đổi response shape results/errors[])"
  - "Offline-first giữ trọn: thiếu head files khi flag bật → fail fast với thông báo actionable, không network storm"
  - "Marker sentinel phản ánh capability hybrid; mixed-state (dense-only vs hybrid) không bao giờ rơi vào mismatch false-positive"
notes:
  - "graph_mcp không index repo harness (chỉ index các project đích) → Graph Impact Layer skip (fast-fail once), evidence lấy từ code reading trực tiếp"
  - "GRAPH QUERY KHÔNG ĐỔI: plan chỉ ở tầng Qdrant vector. intelligent_retrieval seed semantic bằng query_points trực tiếp (intelligent_retrieval.py:169, không qua search_collection) → giữ dense nguyên vẹn; FalkorDB/Cypher/GraphExpander không bị chạm. Xem phase-03 Out of scope."
---

## Bối cảnh

Ngày 2026-10-11 embedding default đã quay về `BAAI/bge-m3` (swap 1 dòng + policy qua `model_defaults.py`/`embed_runtime.model_policy`; thấy `docs/UNIFIED_INGEST_QUERY_CONTRACT.md` mục "Embedding-model swap playbook (BGE-M3 era)"). BGE-M3 sinh **3 biểu diễn từ cùng một forward pass**: dense (đang dùng), sparse lexical weights (bỏ phí), ColBERT multi-vector (bỏ phí). Prediction report (CAUTION) chốt: dense+sparse RRF là pha 1; 3 risk High phải xử lý trong plan này — (H1) snapshot local thiếu `sparse_linear.pt`/`colbert_linear.pt`, (H2) sparse fidelity nếu tự port sai math, (H3) mixed-state nửa vời giữa các collection.

## Kiến trúc đích

```
                        ┌─ EMBED_HYBRID=0 ──► byte-parallel hiện trạng (dense only)
ingest ─► embed_runtime ─┤
           encode dense  └─ EMBED_HYBRID=1 ──► sparse head port (sparse_linear.pt, 3.5KB)
           (path dày đặc                    ──► token-level forward riêng (không đụng dense path)
            không đổi)                      ──► upsert named vectors {dense, sparse} — CHỈ collection mới
                                           ──► ensure_collection: + sparse_vectors_config (local mode
                                               KHÔNG thêm-sparse-vào-collection-cũ được → upgrade = recreate)
                                           ──► marker stamp hybrid (visibility, không phải nguồn chân lý)

query ─► embed_runtime ──► dense vec + sparse vec (khi flag bật)
     ─► qdrant_layout_cache: capability = Mọi collection trong fan-out đều có sparse?
                              │ không ──► TOÀN bộ fan-out query dense thuần (đồng nhất scale)
                              │ có    ──► query_points(prefetch=[dense@"", sparse@"sparse"],
                              │                    query=FusionQuery(RRF), limit=top_k)
     ─► merge_hits: cả fan-out cùng một scale (RRF hoặc cosine, không bao giờ trộn)
     ─► sentinel exclusion + embedding_model mismatch warnings: giữ nguyên
```

Nguyên tắc bất di bất dịch (kế thừa contract hiện có):
1. **Mọi behavior change sống tại chokepoint chung** — `qdrant_query_support` (query), `CodeEmbedder`/`primary_vector_sync`/`local_qdrant` (ingest), `embedding_utils` (doc twin). Không có logic hybrid riêng lẻ trong 4 backend MCP.
2. **Fail-closed ở writer, soft ở reader** — writer thiếu sparse khi flag bật là error; reader gặp collection dense-only là fallback êm, không nhét error vào `errors[]`.
3. **Offline-first** — probe `_st_local_files_only`-style cho head files; thiếu → fail fast có hướng dẫn, không bao giờ HEAD-storm (bài học từ cache-load fix b5b21ee).
4. **Marker là nguồn chân lý capability** — `_embed_meta` thêm `hybrid`; query ưu tiên collection info cache để tránh round-trip, marker để enforce.

## Acceptance criteria (tổng hợp, map vào phase)

| AC | Phase | Nội dung |
|----|-------|----------|
| AC1 | 01 | `embed_runtime` cung cấp sparse encode (query + ingest) từ cùng forward pass dense; đầu ra khớp vector-golden với reference (FlagEmbedding hoặc math port được chứng minh) |
| AC2 | 01 | Flag bật mà thiếu head files → RuntimeError actionable ngay tại load, zero network call khi snapshot đủ; head tải qua audit session |
| AC3 | 02 | Ingest ghi dual-vector tại mọi chokepoint khi flag bật; **collection mới** có `sparse_vectors_config` (local mode không thêm-sparse-vào-cũ được — upgrade collection cũ = recreate qua backfill P4); marker stamp `hybrid` (visibility) |
| AC4 | 03 | Query tự động chọn fused hay dense-only theo capability — **all-or-none trên fan-out** (mọi collection cùng scale, không trộn RRF↔cosine); response shape không đổi |
| AC5 | 03 | `merge_hits` merge đúng RRF scores cross-collection; sentinel exclusion + mismatch warning giữ nguyên |
| AC6 | 04 | Backfill script biến collection dense-only → hybrid **qua recreate + re-encode từ payload text** (local mode không hỗ trợ in-place); idempotent, resumable; quality check bằng top-k overlap trên sample query, không phải hash vector |
| AC7 | 04 | Runbook A/B (EN/VI/JA/identifier) + verification report; flag default vẫn OFF khi plan close (flip là quyết định post-A/B) |
