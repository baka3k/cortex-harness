---
title: "Thống nhất embedding model: Qwen/Qwen3-Embedding-0.6B cho cả code (graph_mcp) + doc (mind_mcp)"
status: active
created: 2026-10-09
mode: hi-plan --full
scope: "Thay jinaai/jina-embeddings-v3 (code side) và BAAI/bge-m3 (doc side) bằng Qwen/Qwen3-Embedding-0.6B ở mọi tầng: (1) mọi query-side encode path — pooling last-token + query instruction; (2) 12 bản sao CodeEmbedder ingest — delegate sang SentenceTransformer; (3) model-identity marker ở writer chokepoints + size guard chống trộn vector 1024 im lặng; (4) sweep ~40 literal defaults code+doc+livingdoc + dependency floors; (5) reset tooling + backup/rollback + runbook re-index + A/B verification. KHÔNG đụng: collection {base}_mess (hash vectors), message_scan logic, graph schema/writer, MCP tool contracts, storage adapter API, sync code region của dev.py thuộc plan 260821-2115 (chỉ chung file, khác region)."
blockedBy: []
blocks: []
relatedPlans:
  - 260829-2322-vector-search-query-optimization
  - 260821-2115-dev-sync-code-windows
  - 260807-0929-mcp-ingest-query-concurrency
sources:
  - docs/plans/261009-1304-unified-qwen3-embedding/research-digest.md
  - docs/plans/261009-1304-unified-qwen3-embedding/red-team.md
  - docs/plans/261009-1304-unified-qwen3-embedding/flows.md (flows chi tiết: ingest code/doc, query MCP, dev init)
  - https://huggingface.co/Qwen/Qwen3-Embedding-0.6B (model card)
  - https://arxiv.org/html/2506.05176 (Qwen3 Embedding tech report)
  - https://arxiv.org/html/2508.21290 (Jina code-embeddings paper — benchmark so sánh)
  - docs/UNIFIED_INGEST_QUERY_CONTRACT.md
  - docs/plans/260829-2322-vector-search-query-optimization/plan.md (prior art: embed_runtime, delegate names)
---

# Thống nhất embedding model: Qwen3-Embedding-0.6B cho cả hai pipeline

## Overview

Hiện trạng (research-digest.md F1–F10): code side (graph_mcp) mặc định `jinaai/jina-embeddings-v3`, doc side (mind_mcp) mặc định `BAAI/bge-m3` — hai không gian vector khác nhau, **không thể so sánh vector chéo code↔doc** (điều kiện so sánh là cùng model, không phải cùng dim). Nhu cầu: thống nhất một model duy nhất — `Qwen/Qwen3-Embedding-0.6B` (1024 dim, last-token pooling, query instruction `Instruct: {task}\nQuery: {query}`, docs nhúng trần, Apache-2.0, MTEB-Code 74.69 gần bằng jina-v4, MTEB Multilingual 64.33 bao phủ tiếng Việt + tiếng Nhật cho phía doc).

Động lực: (a) so sánh/so khớp vector chéo code↔document trong cùng không gian; (b) code retrieval mạnh hơn v3/bge-m3 rõ rệt; (c) bỏ vướng license CC-BY-NC của jina.

### Ba blocker đã verify (research-digest Risks 1–3)

| # | Blocker | Hiện trạng | Xử lý |
|---|---|---|---|
| 1 | **Pooling sai phía query code side** | `embed_runtime.embed_query` → AutoModel + **mean pooling** (`embed_runtime.py:162-198`); Qwen3 cần last-token pooling → vector query rác nếu swap thô | P1: reroute `embed_query` qua `get_sentence_transformer` + `prompt_name="query"` cho Qwen3 |
| 2 | **Trộn vector 1024 im lặng** | v3, bge-m3, Qwen3 đều 1024-dim; code side chỉ raise khi lệch dim (`local_qdrant.py:226-232`), doc side **không check gì** khi collection đã tồn tại (`graphrag_ingest_langextract.py:611-620`) | P3: model-identity marker **ở writer chokepoints** (red-team F1) + size guard doc side + reset tooling |
| 3 | **Heuristic `"jina"` rải ~30 file** | `embed_runtime.py:73,288`, `primary_vector_sync.py:309`, `cobol/qdrant.py:129`, 12 CodeEmbedder copies, 3 runtime branch, `JINA_MODEL_PATH` env | P2: policy helper model-family-aware duy nhất; sweep 12 bản sao CodeEmbedder sang ST-backed |

Lưu ý kiến trúc ingest: tồn tại **2 đường ingest** — 12 analyzer "legacy" dùng CodeEmbedder (AutoModel + mean pooling tại `max_length=512`) rồi tự ghi qua `QdrantWriter` (`python_analyzer.py:1708,1878`), và đường primary/delegating dùng `primary_vector_sync.sync_vector_documents` (SentenceTransformer, `normalize_embeddings=True`, `primary_vector_sync.py:302-317`). **Cả hai đường đều phải chuyển sang last-token pooling** — chỉ sửa query path là chưa đủ (12 analyzer legacy vẫn sinh vector rác cho Qwen3).

## Scope Challenge (3 câu)

1. **Giải quyết gì?** Một không gian embedding duy nhất cho code + doc (điều kiện để so sánh vector chéo), với pooling/instruction đúng chuẩn Qwen3, chống trộn vector khi model đổi. Không làm lại kiến trúc hybrid (vector + graph expansion giữ nguyên), không đổi collection naming/tool contract.
2. **In/Out?** IN: `code-tiny/tools/common/embed_runtime.py`, `primary_vector_sync.py`, 12 CodeEmbedder copies (python/js/java/cplus/kotlin/php/ts/sql/delphi/csharp/plsql/android_kotlin + vb_analyzer_base), 4 MCP DEFAULT_MODEL chains, 8 delegating analyzers, `cobol/qdrant.py`, `local_qdrant.py` guard, livingdoc writers, `dev.py` 6 site, doc-tiny 3 scripts + `embedding_utils.py`, reset script mới, `.env.example` ×2, requirements floors, tests 3 file pin literal, docs ~20 hits, `UNIFIED_INGEST_QUERY_CONTRACT.md`. OUT: `{base}_mess` hash collections + `message_scan.py`, `rebuild_vector_collection.py` (copy vector, không re-embed), graph schema/writer, MCP tool fan-out/precedence, storage adapter API, xlsx pipeline, provider spacy/gliner, đổi sang model khác (JCE-0.5b, Qwen3-4B/8B).
3. **Biết xong khi nào?** (a) mọi default qua env chain resolve về `Qwen/Qwen3-Embedding-0.6B`; (b) parity test: cả 3 query path (`embed_query`, `explore_service`, doc query) cho Qwen3 == ST `encode(prompt_name="query")` (last-token pooling, không mean-pool); (c) ingest vào collection có marker model khác → hard error **từ mọi writer chokepoint** nêu đúng lệnh reset; (d) reset script/per-project reset chỉ drop nội dung đích, `_mess` nguyên vẹn, sentinel được dọn; (e) A/B pilot 1 project nhỏ: bộ query EN/VI/JA trước/sau swap — tỷ lệ truy vấn không thụt lùi; (f) so chéo hoạt động: cosine(vector function ↔ vector doc paragraph) cùng không gian cho kết quả có ý nghĩa; (g) `rg "jina-embeddings-v3|bge-m3"` (trừ `*.bak`, docs/plans) chỉ còn policy/legacy path + tests cố ý.

## Design Decisions (đã sửa theo red-team F1–F10)

- **D1 — Một policy helper duy nhất trong `embed_runtime`:** `model_policy(name) → {trust_remote_code, backend: "st"|"automodel", query_prompt: Optional[str], normalize: bool}`. Qwen3 → backend ST, `prompt_name="query"` (từ ST config, không hardcode chuỗi), `trust_remote_code=False`, `normalize=True`; jina → giữ nguyên hành vi legacy; path local → giữ logic `CODE_EMBEDDING_MODEL_PATH`. Mọi site heuristic `"jina"` (kể cả `cobol/qdrant.py:129` — F6) gọi helper này.
- **D2 — Query instruction CHỈ phía query, ở MỌI query path (F4):** `embed_runtime.embed_query` (code MCP), `explore_service._make_embedder` (`explore_service.py:184` — graph fan-out), doc query (`mcp_graph_rag.py:918,1019`, `graphrag_query_langextract.py:180`). Ingest luôn nhúng trần. `prompt_name` là argument thuần của ST encode nên doc-tiny (flat tree) dùng trực tiếp, không cần import bridge từ code-tiny (F6).
- **D3 — CodeEmbedder delegate encode xuống SentenceTransformer** (giữ wrapper chunking/truncation + `_infer_vector_size`): ST tự áp đúng pooling; xóa 12 bản sao mean-pool + `_should_trust_remote_code` + `fix_mistral_regex`. Legacy ST encode branch cũng đặt `normalize_embeddings=True` (F10, khớp D8).
- **D4 — Model-identity marker ở WRITER CHOKEPOINTS (F1):** sentinel 1 point/collection, **private uuid5 namespace riêng** (không `NAMESPACE_URL` — tránh collision với symbol_id thật, F7), payload `{"_embed_meta": true, embedding_model, vector_size, project_id, stamped_at}`. Enforce tại: `local_qdrant.ensure_collection`/writer path (mọi analyzer + message_scan đi qua đây → loại trừ `_mess` theo suffix), `cobol/qdrant.py:140-155`, livingdoc writers (`living-doc-vectorize.py:226-229`, `living-doc-vectorize-infra.py:165`), doc `create_collection`. Search: shared `qdrant_query_support.search_collection` thêm `must_not` `_embed_meta` (unscoped query `project_scope.py:141-153` không trả sentinel về làm hit rác, F7). Query lệch model → soft warning trong shared `search_collection` (F8 — phủ mọi backend/parallel wrappers) + doc query; không hard-fail.
- **D5 — Doc side: size guard + device auto-detect + wiring env đúng:** `create_collection` check dim; `resolve_embedding_device` mặc định auto (MPS/CUDA); `dev.py:769` đổi sang set `EMBEDDING_DEVICE` (doc đọc tên này — F10).
- **D6 — Defaults converge về hằng số, mỗi tree một bản (F6):** code-tiny: `tools/common/model_defaults.py`; doc-tiny flat không import được code-tiny (`graphrag_ingest_langextract.py:1450` ghi rõ giới hạn) → hằng số riêng trong `doc-tiny/embedding_utils.py`, giá trị giống nhau + comment nhắc đồng bộ.
- **D7 — Wire env chết, không bỏ env cũ:** `embedding_utils.resolve_embedding_model` thêm `DOC_EMBEDDING_MODEL` sau `EMBEDDING_MODEL`; gỡ `TEXT_EMBEDDING_MODEL` khỏi doc Readme + `.env.example` (F6: root `.env.example` và `code-tiny/.env.example` cũng phải sweep).
- **D8 — Normalization: thống nhất normalized cho Qwen3** cả ingest (cả primary lẫn legacy ST branch — F10) lẫn query; jina legacy giữ bất đối xứng cũ (prior-art plan.md:146-148).
- **D9 — Migration = drop + re-ingest, có gate, thứ tự tuần toàn:** P1 → P2 → P3 → P4 → P5 (P2 tiêu thụ `model_policy` của P1 — không craft song song như dự thảo đầu; P3 bắt buộc trước P4). Theo doctrine UNIFIED_INGEST_QUERY_CONTRACT (:176-179).
- **D10 — Dependency floors + fail-fast (F5):** root `requirements.txt:39` → `transformers>=4.51,<4.56`; `sentence-transformers>=2.7.0` (root `:15` + `doc-tiny/requirements.txt:13`); `embed_runtime` check version khi load model Qwen3, thiếu → lỗi rõ ràng thay vì TypeError/kiến trúc lạ.
- **D11 — Backup/rollback bắt buộc (F9):** mọi drop phải sau `db_transfer export` collection tương ứng; rollback = re-import + set env model cũ + re-index ngược — ghi trong runbook P5.

## Cross-Plan Dependencies

| Plan | Status | Quan hệ |
|---|---|---|
| 260829-2322-vector-search-query-optimization | implemented | Prior art: embed_runtime/delegate names/test gates — P1 tái sử dụng pattern, không đụng lại kết quả của nó |
| 260821-2115-dev-sync-code-windows | active | Chung file `dev.py` nhưng khác region (họ: cửa sổ sync code; mình: literal defaults + env wiring) — coordinate khi craft, không blockedBy |
| 260807-0929-mcp-ingest-query-concurrency | pending | P2/P4 đụng đường ingest — nếu plan đó active khi craft, review phần lock/single-flight trước khi merge |

## Phases

| Phase | File | Nội dung | Test size |
|---|---|---|---|
| 01 | [phase-01-embed-runtime-policy.md](phase-01-embed-runtime-policy.md) | Policy helper + reroute MỌI query path (embed_query, explore_service, doc query) + ST-aware CPU fallback | M |
| 02 | [phase-02-ingest-pooling-unification.md](phase-02-ingest-pooling-unification.md) | 12 CodeEmbedder → ST-backed + `primary_vector_sync` + `cobol/qdrant` policy + normalize thống nhất (13 file, mechanical) | L |
| 03 | [phase-03-model-marker-and-reset.md](phase-03-model-marker-and-reset.md) | Sentinel marker ở writer chokepoints + size guard + search exclusion + soft warning + reset tooling dọn sentinel | L |
| 04 | [phase-04-defaults-sweep.md](phase-04-defaults-sweep.md) | Sweep literal → Qwen3 (code+doc+livingdoc+.env.example), wire env chết + EMBEDDING_DEVICE, dependency floors + fail-fast, update tests pin | M |
| 05 | [phase-05-reindex-docs-verification.md](phase-05-reindex-docs-verification.md) | Backup/rollback + runbook re-index + A/B gate EN/VI/JA + docs sweep + verification-report | M |

Thứ tự **tuần toàn bắt buộc** (P2 dùng P1; P3 trước P4 — D9).

Chi tiết hiện trạng → thay đổi theo từng luồng — [flows.md](flows.md): (1) ingest code (2 đường: 12 legacy CodeEmbedder → QdrantWriter, primary/delegating → primary_vector_sync), (2) ingest doc (graphrag_ingest + livingdoc), (3) query MCP (graph_mcp 3 đường + mind_mcp), (4) dev init + env propagation — kèm ma trận tổng hợp đường ghi/đọc × cơ chế bảo vệ.

## Risks & Mitigations

| Risk | Mức | Mitigation |
|---|---|---|
| Mean-pool sót ở một đường ingest/query nào đó → vector rác im lặng | High | P1/P2 sweep checklist + parity test cho 3 query path; verification gate `rg` residual (trừ `*.bak` — F6) |
| Trộn vector 1024 im lặng khi đổi model, qua writer nào đó | High | P3 marker tại chokepoints (local_qdrant + cobol + livingdoc + doc) phủ mọi đường ghi; thứ tự P3→P4; query soft warning nhìn thấy được |
| Qwen3 trên MPS: numerical/dtype khác CUDA, chậm trên CPU | Medium | Guard CPU-fallback ST-aware (P1 — F3); doc side auto-detect (P4); A/B pilot đo thời gian trước khi cut over |
| `local_files_only` + ST load Qwen3 snapshot local chưa verify trong repo | Medium | Phase 01 test load snapshot local (`HF_HUB_OFFLINE`); tải sẵn bằng hf-cli; `get_sentence_transformer` thêm `local_files_only` (F10) |
| Transformers 4.41–4.50 trong window pin cũ không load được Qwen3 | High | D10 floors + startup fail-fast check |
| Straggler literal (~40 site + `.env.example` + cobol riêng) | High | D6 hằng số mỗi tree + grep gate P5 |
| Sentinel bị search trả về làm hit rác / id collision | Medium | F7: private namespace + payload `_embed_meta` + `must_not` trong shared search_collection |
| Pilot doc reset để lại marker cũ → tự khoá chân mình | Critical→fixed | F2: per-project reset xóa sentinel; "chỉ còn sentinel" = rỗng → re-stamp |
| Re-index cả fleet rồi gate fail — không đường lùi | Medium | D11: `db_transfer export` bắt buộc trước drop + rollback procedure trong runbook |
| Dev.py là file chung với plan 260821-2115 active | Low | Khác region; merge theo region, coordinate trong craft |

## Bài học từ lần swap bge-m3 trước (context hội thoại)

Lần trước v3→bge-m3 cùng 1024-dim nên mismatch **im lặng** — tỷ lệ truy vấn giảm mà không có lỗi nào. Lần này: marker ở chokepoints + size guard biến sai sót thành hard error (ingest) / cảnh báo hiển thị (query), A/B gate (P5) bắt buộc trước khi re-index toàn bộ, và có backup/rollback (D11) — thứ lần trước không hề có.
