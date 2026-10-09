# Flows chi tiết: hiện trạng → thay đổi (ingest code / ingest doc / query MCP / dev init)

> Bổ sung theo yêu cầu: mô tả từng luồng end-to-end với `file:line` hiện trạng và thay đổi tương ứng (phase tham chiếu plan.md). Nguồn: research-digest.md F1–F10 + red-team.md F1–F10 (đã verify chéo).

## 1. Luồng INGEST CODE (code-tiny / graph_mcp)

### Hiện trạng

```
dev sync code [all]                    dev.py sync_code :3432 / sync_code_all :3580
  │  env EMBEDDING_MODEL (hoặc literal "jinaai/jina-embeddings-v3")   dev.py:3528, :3634
  ▼
code-tiny/tools/sync/incremental_sync.py
  │  --embed-model → _build_analyzer_cmd; env CODE_EMBEDDING_MODEL   incremental_sync.py:1232, fallback :3845
  ▼
Analyzer entrypoint   model_name = args.embed_model or "jinaai/jina-embeddings-v3"   (12 legacy, digest F1a)
  │
  ├─【Đường A — 12 analyzer legacy】 CodeEmbedder (AutoModel + MEAN POOL max_length=512)
  │     python_analyzer.py:947 (bản sao ×12) → truncate/chunk theo ký tự → encode
  │     ▼
  │   QdrantWriter → local_qdrant.ensure_collection :218-240  (chỉ raise khi LỆCH dim)
  │     ▼
  │   upsert collection {project_id}  (== project_id, UNIFIED_INGEST_QUERY_CONTRACT.md:33)
  │
  └─【Đường B — primary/delegating: go/rust/swift/perl/flutter/jp1/shell/cobol】
        primary_vector_sync.sync_vector_documents :302-317
          SentenceTransformer + normalize_embeddings=True (ST pooling ĐÚNG cho Qwen3 sẵn)
          ▼
        ensure_collection → upsert + _delete_stale :354-367

{base}_mess (message_scan.py:25): hash vectors, KHÔNG qua model — không đổi.
```

**Hai đường đang lệch nhau:** Đường A mean-pool (sai cho Qwen3), Đường B ST + normalize (đúng pooling). Swap thô → 12 analyzer legacy sinh vector rác mà không lỗi nào.

### Thay đổi

| Bước | Thay đổi | Phase |
|---|---|---|
| dev.py truyền `--embed-model` | literal → hằng số `model_defaults.DEFAULT_CODE_EMBEDDING_MODEL` | P4 |
| incremental_sync fallback `:3845` | `CODE_EMBEDDING_MODEL or EMBED_MODEL` giữ nguyên; giá trị resolve về Qwen3 | P4 |
| Đường A: CodeEmbedder | delegate load/encode sang `embed_runtime.get_sentence_transformer` (last-token pooling); giữ wrapper chunk/truncate + `_infer_vector_size`; xóa `_should_trust_remote_code` + `fix_mistral_regex` | P2 |
| Đường A: legacy ST encode branch | thêm `normalize_embeddings=True` (thống nhất D8) | P2 |
| Đường B: `primary_vector_sync.py:309` | heuristic jina → `model_policy()`; giữ normalize | P2 |
| ensure_collection / QdrantWriter | check sentinel marker trước upsert — lệch model → hard error nêu lệnh reset; `_mess` loại trừ | P3 |
| cobol (đường riêng `cobol/qdrant.py:129,140-155`) | heuristic → policy + marker check | P2 + P3 |

Sau thay đổi: **cả hai đường đều ST-backed, last-token pooling, normalized, cùng marker** — không còn phân nhánh pooling theo analyzer.

## 2. Luồng INGEST DOC (doc-tiny / mind_mcp)

### Hiện trạng

```
dev sync doc [all]                     dev.py _sync_doc_folder :1220
  │  --embedding-model: env EMBEDDING_MODEL (hoặc literal "BAAI/bge-m3")   dev.py:1270
  │  --no-batch (encode từng paragraph)                                    dev.py:1277
  ▼
doc-tiny/graphrag_ingest_langextract.py
  │  resolve_embedding_model(args, "BAAI/bge-m3")    :1519-1520
  │    chuỗi env hiện tại: arg → EMBEDDING_MODEL_PATH → EMBEDDING_MODEL → default
  │    (embedding_utils.py:16-25; EMBEDDING_DEVICE mặc định "cpu" — :28-34)
  ▼
SentenceTransformer(model, device)      :1522
  ▼
create_collection :611-620   ← collection TỒN TẠI = reuse CÂM, KHÔNG check gì
  ▼
ingest_to_qdrant: vector = embedder.encode([paragraph])[0]   :638 (từng paragraph)
  ▼
upsert {project_id}_doc   (registry naming, mcp_graph_rag.py:203)

Livingdoc (code-tiny nhưng dùng model doc): living-doc-vectorize.py:257,259-267,270;
living-doc-link.py:198,203-206; vectorize-infra:60; pipeline:67 — default bge-m3.
```

**Rủi ro đặc thù:** bge-m3 và Qwen3 cùng 1024 dim → `create_collection` reuse câm nghĩa là re-ingest sau khi đổi model **trộn vector im lặng** (đúng kịch bản "tỷ lệ giảm" lần trước).

### Thay đổi

| Bước | Thay đổi | Phase |
|---|---|---|
| chuỗi env | thêm `DOC_EMBEDDING_MODEL` sau `EMBEDDING_MODEL` (wire var đang chết, dev.py:766) | P4 |
| device | `resolve_embedding_device` mặc định auto (MPS/CUDA/CPU) thay vì `cpu` | P4 |
| create_collection | + size guard (dim lệch → raise kèm hướng dẫn `0_reset_all.py`) + sentinel marker | P3 |
| encode paragraph | giữ nhúng TRẦN (không prompt); ST pooling của Qwen3 tự đúng | — (không đổi) |
| livingdoc defaults | bge-m3 → hằng số doc (6 file) | P4 |
| 0_reset_all per-project | xóa luôn sentinel; "chỉ còn sentinel" = rỗng → re-stamp | P3 (F2) |

## 3. Luồng QUERY QUA MCP

### Hiện trạng

**graph_mcp (code):**
```
semantic_search (tool) → fastmcp_server.py
  │  model: CODE_EMBEDDING_MODEL_PATH → CODE_EMBEDDING_MODEL → JINA_MODEL_PATH → jina  :116-121
  │  query vector: _embed_query :621-653 → embed_runtime.embed_query :235-267
  │     LRU cache (model, text) → get_embedder (AutoModel!) → encode_texts?
  │     AutoModel KHÔNG có .encode → MEAN POOL max_length=512  :162-198   ← SAI cho Qwen3
  ▼
qdrant_query_support.search_collection :694-698  (shared, mọi backend)
  ▼
parallel wrappers: cplus_mcp.py:935-949 / android_mcp.py:754-768 / java_mcp.py:603-617
explore_service (graph fan-out): _make_embedder :169-190 — ST nhưng encode([text]) TRẦN,
  model từ env EMBED_MODEL :138, fallback cplus_mcp._embed_query
```

**mind_mcp (doc):**
```
semantic_search / hybrid_search → doc-tiny/mcp_graph_rag.py
  │  get_embedder :159-168 lazy singleton — resolve_embedding_model(None, "BAAI/bge-m3")
  ▼
q_vec = embedder.encode([query])[0]   :918, :1019   ← TRẦN, không query instruction
  ▼
{project_id}_doc (+ graph expansion FalkorDB cho hybrid)
```

**Điểm yếu hiện tại:** query code mean-pool (sai pooling); query doc + explore_service không có instruction (mất 1–5% chất lượng theo model card); không có cơ chế phát hiện collection lệch model.

### Thay đổi

| Đường | Thay đổi | Phase |
|---|---|---|
| `embed_runtime.embed_query` | policy ST → `get_sentence_transformer().encode([text], prompt_name="query", normalize_embeddings=True)`; CPU fallback **ST-aware** (evict `_SENTENCE_TRANSFORMER_CACHE`, không rơi mean-pool) | P1 (F3) |
| `explore_service._make_embedder` | encode qua policy (prompt_name cho Qwen3); fallback delegate giữ signature | P1 |
| doc query `mcp_graph_rag.py:918,1019`, `graphrag_query_langextract.py:180` | `encode([query], prompt_name="query", normalize_embeddings=True)`; paragraph/doc vẫn trần | P1 |
| MCP DEFAULT_MODEL chains ×4 | literal → hằng số, giữ thứ tự ưu tiên env | P4 |
| `qdrant_query_support.search_collection` | `must_not` `_embed_meta` (sentinel không thành hit) + soft warning `embedding_model_mismatch` trong response khi lệch model (cache 1 lần/process/collection) | P3 (F7, F8) |
| doc query response | cảnh báo lệch model tương tự trong response meta | P3 |

Kết quả: mọi query path cùng không gian, cùng chuẩn instruction; lệch model nhìn thấy được trong response thay vì tự mòn chất lượng.

## 4. Luồng DEV INIT + ENV PROPAGATION

### Hiện trạng

```
dev init (interactive)
  │  prompt "EMBEDDING_MODEL | Keep default or change to your local model"   HARNESS_WORKFLOW.md:96
  │  code section: _p("EMBEDDING_MODEL", ["code","env","EMBEDDING_MODEL"], "jina…")   dev.py:3032
  │  doc  section: _p("EMBEDDING_MODEL", ["doc","env","EMBEDDING_MODEL"],  "bge…")   dev.py:3053
  ▼
persist .cortext-harness/config/*.json — code.env / doc.env   dev.py:3138, :3148
  ▼
Khi launch process:
  code env: CODE_EMBEDDING_MODEL + EMBED_MODEL (+ EMBED_DEVICE :741)   dev.py:735-737
  doc  env: DOC_EMBEDDING_MODEL (KHÔNG ai đọc!) :765-766 + EMBED_DEVICE :769
            (doc-tiny lại đọc EMBEDDING_DEVICE — lệch tên, F10)
MCP start: dev.py mcp start ≡ scripts/mcp-lifecycle.py start — env dict byte-identical
  (FALKORDB_*, QDRANT_COLLECTION, QDRANT_COLLECTION_DOC, …)   UNIFIED_INGEST_QUERY_CONTRACT.md:200-218
```

**Bẫy hiện tại:** (1) init ghi jina/bge vào config JSON — user cũ giữ model cũ vĩnh viễn kể cả khi code đổi default; (2) `DOC_EMBEDDING_MODEL` set nhưng không ai đọc; (3) `EMBED_DEVICE` vs `EMBEDDING_DEVICE` lệch tên giữa launcher và reader; (4) `TEXT_EMBEDDING_MODEL` documented nhưng never-read.

### Thay đổi

| Bước | Thay đổi | Phase |
|---|---|---|
| prompt init code + doc | default hiển thị/ghi = `Qwen/Qwen3-Embedding-0.6B` (đường dẫn local vẫn nhập được) | P4 |
| doc env merge `dev.py:766` | giữ `DOC_EMBEDDING_MODEL` (giờ được đọc) | P4 |
| doc env merge `dev.py:769` | `EMBED_DEVICE` → `EMBEDDING_DEVICE` (khớp reader) | P4 |
| launcher MCP | env dict byte-identical giữ nguyên biến, chỉ giá trị resolve mới — **không thêm biến mới vào launcher** (tránh lệch 2 launcher) | P4 (kiểm chứng bằng test launcher hiện có) |
| Config JSON user cũ | KHÔNG tự ghi đè — runbook P5: re-init hoặc set `EMBEDDING_MODEL` env trước khi re-index; marker P3 sẽ chặn nếu quên | P5 |

## 5. Ma trận tổng hợp: đường ghi/đọc × cơ chế bảo vệ

| Đường | Model mặc định mới | Pooling | Query prompt | Marker check | Lệch model thì |
|---|---|---|---|---|---|
| Ingest code — 12 legacy (QdrantWriter → local_qdrant) | Qwen3 (P4) | ST last-token (P2) | — (trần) | local_qdrant chokepoint (P3) | hard error + lệnh reset |
| Ingest code — primary/delegating (primary_vector_sync) | Qwen3 (P4) | ST (đã đúng) + policy (P2) | — | local_qdrant (P3) | hard error |
| Ingest code — cobol riêng | Qwen3 (P4) | ST (P2) | — | cobol/qdrant (P3) | hard error |
| Ingest doc (graphrag_ingest) + livingdoc | Qwen3 (P4) | ST | — | create_collection (P3) | hard error + `0_reset_all` |
| `{base}_mess` | hash vectors | — | — | loại trừ | N/A |
| Query graph_mcp (embed_query + wrappers) | env chain (P4) | ST (P1) | `prompt_name="query"` (P1) | shared search_collection (P3) | warning trong response |
| Query graph fan-out (explore_service) | EMBED_MODEL (P4) | ST (P1) | `prompt_name="query"` (P1) | qua search_collection (P3) | warning |
| Query mind_mcp (mcp_graph_rag) | env chain mới (P4) | ST | `prompt_name="query"` (P1) | response meta (P3) | warning |
