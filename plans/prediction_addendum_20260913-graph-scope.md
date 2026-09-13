# HI-PREDICT Addendum: Scope thu hẹp — "graph code" sang Rust

- **Date:** 2026-09-13 · **Parent report:** prediction_report_20260913-1553.md (verdict STOP cho full rewrite)
- **Scope đề xuất mới:** `code-tiny/tools/graph/` (18.3k LOC) — core graph layer
- **Verdict: CAUTION** — khả thi, với 1 quyết định kiến trúc bắt buộc (embedded DB seam) và parity gate.

## Bằng chứng đã xác minh

1. `tools/graph/` **không phụ thuộc ML** (không torch/gliner/langextract) — toàn bộ là logic thuần: Cypher construction, journal, writers, schema, operations. Đây là phần portable nhất của repo.
2. `falkordblite` thực chất là `redislite.falkordb_client.FalkorDB` — Redis server embedded fork ra in-process/Unix socket, persistence AOF+RDB. **Không có tương đương Rust** (wheel Python chứa Redis module compiled). Đây là rào cản duy nhất còn lại của scope này.
3. `GraphDriverFactory` đã có placeholder `"Kuzu driver not yet implemented"` — kiến trúc driver abstraction (`core/base.py`, `core/cypher_driver.py`) đã预留 sẵn đường swap engine. Kuzu = embedded Cypher graph DB, single-writer file-backed, **có Rust bindings chính thức** — giống mô hình falkordblite về deployment promise.
4. MCP server layer (`code-tiny/mcp/`, 22.3k) **vẫn dính torch** (fastmcp_server, services/explore_service) — không nằm trong scope này, giữ Python.

## Ba kịch bản khả thi

| Kịch bản | Mô tả | Đánh đổi |
|---|---|---|
| **A. Hybrid PyO3/sidecar** (rủi ro thấp nhất) | Port `tools/graph` logic sang Rust, driver vẫn gọi falkordblite qua Python sidecar (JSON-RPC stdio) | Giữ nguyên data .rdb; thêm 1 process; không đổi promise no-daemon |
| **B. Port + swap Kuzu** (đích cuối cùng sạch nhất) | Rust binary hoàn chỉnh, driver Kuzu thay FalkorDBLite; migration Cypher-export → import | Đổi format on-disk (.rdb → kuzu dir) → bắt buộc migration tool; kiểm tra chênh lệch Cypher dialect + full-text index |
| **C. Chỉ port logic thuần** | journal (rusqlite 1:1 với sqlite_store 3.1k), writer (3.9k), operations, schema; driver giữ Python | Nhanh nhất nhưng giá trị phân tán; thích hợp làm phase 1 của B |

## Risk chính (scope graph-only)

| Risk | Severity | Mitigation |
|---|---|---|
| FalkorDB Cypher dialect ≠ Kuzu Cypher (hàm, full-text, procedural calls) | High | Contract test replay toàn bộ query đã dùng qua cả 2 engine trước cutover |
| Data migration .rdb → Kuzu cho instance hiện có | High | Migration tool + backup; version hoá manifest instance |
| MCP contract nếu sau này port tiếp graph MCP tools | Medium | Giữ MCP server Python, gọi Rust qua PyO3/subprocess — contract không đổi |
| Lợi ích hiệu năng chưa đo (Falkor engine time > Python overhead) | Medium | Profiling baseline trước; đặt SLO (vd. ingest batch -30%) |
| redislite single-writer vs Kuzu single-writer khác biệt locking | Low | Stress test đa tiến trình trên journal/lease path |

## Effort ước tính

- Kịch bản C → A: ~4–6 tuần (1 senior, có parity tests sẵn: tests/mcp/test_provider_boundary.py + journal tests)
- Kịch bản B hoàn chỉnh (kèm migration tool): ~8–12 tuần

## Điều kiện tiên quyết trước khi bắt đầu

1. Profiling 1 phiên ingest xác nhận graph write/query là path đáng tối ưu (hoặc chấp nhận động cơ = distribution/single-binary).
2. Chọn kịch bản A hay B (A an toàn, B sạch nhưng phải nhận migration).
3. Đi qua parity gate cho mọi cutover.

## Phụ lục: Chiến lược GLiNER khi cần đến ML

**Bối cảnh đã xác minh trong repo:**
- GLiNER chỉ nằm ở đường **ingest offline** (`doc-tiny/entity_extractors.py`, `graphrag_ingest_langextract.py`), KHÔNG nằm ở đường query.
- Model mặc định `urchade/gliner_large-v2.1`, labels cố định (PERSON/ORG/PRODUCT/GPE/DATE/TECH/CRYPTO/STANDARD), threshold 0.3–0.35, batch-size mặc định 1.
- Repo **đã có provider seam**: `--entity-provider gliner / langextract / spacy` (`cortex_harness/dev.py:3601`) và `extract_entities_gliner(_batch)/gemini/langextract` cùng trả về (entities, relations).

**Chiến lược theo giai đoạn:**
1. **Ngắn hạn (kèm port graph code): giữ GLiNER bằng Python sidecar.** Đóng khung `entity_extractors.py` thành worker process có contract JSON `{text, labels, threshold} → [{entity, type, score, span}]` qua stdio/HTTP; Rust ingest gọi qua biên này. Contract gần như đã tồn tại sẵn. Parity 100%, penalty ~0 vì ingest là offline batch.
2. **Dài hạn (full-Rust): ONNX + `ort` crate.** GLiNER là BERT encoder → export ONNX (đường chuẩn qua optimum); tokenizer dùng crate `tokenizers` (chính là thư viện Rust mà gliner Python dùng bên dưới → parity tokenization cao); port post-processing của `predict_entities` (~vài trăm dòng: span merge, threshold, label decode) + golden parity test trên corpus thật với threshold margin. Weights giữ nguyên file.
3. **Không khuyến nghị:** candle/burn (port tay kiến trúc model — effort/rủi ro numeric không cần thiết); đổi sang LLM extraction chỉ vì muốn bỏ Python (đó là quyết định product — đổi cost/latency/offline — không phải quyết định porting).

### Jina embeddings (`jinaai/jina-embeddings-v3`) — khác GLiNER ở 1 điểm: nằm trên cả đường query

- **Vị trí:** embed model chính của mọi language analyzer (ingest batch) **và** query-time embedding trong semantic expansion (`mcp/semantic_graph_expansion.py`, `services/explore_service.py` import torch). Load bằng `AutoModel.from_pretrained(trust_remote_code=True)` + torch/peft, local qua `JINA_MODEL_PATH`.
- **Giai đoạn 1 — sidecar:** gộp chung **một** Python ML worker với GLiNER (NER + embedding cùng process, contract JSON). Ingest batch penalty ~0; query-side chỉ embed 1 text/lần → IPC round-trip vài ms, không đáng kể.
- **Giai đoạn 2 — ONNX + `ort`:** Jina v3 có **ONNX export chính thức** (kèm bản fp16/int8) trên HF; tokenizer là XLM-R `tokenizer.json` → chạy trực tiếp bằng crate `tokenizers`; post-processing chỉ mean-pooling + normalize. peft/LoRA Python dependency biến mất (adapter merge sẵn trong graph). Effort ~1 tuần + parity validation.
- **Rủi ro đặc thù (nghiêm trọng hơn NER):** v3 dùng task LoRA (retrieval.query/passage…). Call sites trong repo **không truyền prompt_name/task** → đang chạy default task. ONNX export bake sẵn 1 task — nếu task lệch với runtime Python hiện tại thì **toàn bộ vector trong Qdrant thuộc không gian khác → phải re-embed mọi collection**. Bắt buộc golden parity test (cosine ≥ 0.999 giữa Python và Rust trên chunk code thật) trước cutover.
- **Không khuyến nghị:** đổi sang embedding API (Jina/OpenAI) — repo đang theo hướng local-only (`JINA_MODEL_PATH`, `GLINER_LOCAL_ONLY`); đổi API là quyết định product, không phải porting.

## Phụ lục 2: Inventory đầy đủ embedding/query/BM25 + đánh giá lại (2026-09-13)

### ML models — đúng 3 model local, mọi thứ còn lại là thuật toán

| Model | Dùng ở đâu | Cách load | Đường |
|---|---|---|---|
| `jinaai/jina-embeddings-v3` (1024-d, task LoRA) | 11 analyzers (go/python/js/shell/rust/perl/jp1/java/cplus/kotlin/php) — mỗi cái một bản `CodeEmbedder` copy (~100–150 LOC × 11) | `AutoModel.from_pretrained(trust_remote_code=True)` + peft | Ingest batch + query (semantic expansion) |
| `BAAI/bge-m3` | doc-tiny: `graphrag_query_langextract.py` (query embedding), ingest doc | `SentenceTransformer` | Ingest + query |
| GLiNER `urchade/gliner_large-v2.1` | `entity_extractors.py` | torch/transformers | Ingest only |

LLM-extraction (langextract/gemini) là **API call** — không phải local ML, port sang reqwest là trivial.

### Query pipeline — retrieval brain (thuần thuật toán, ~2.3k LOC)

- `IntelligentRetrievalEngine` (745 LOC): fusion 3 tín hiệu — Qdrant dense search + FalkorDB fulltext keyword + **BM25 client-side** (99 LOC, `rank_bm25`, weight mặc định 0.15), blend theo weight profile theo query intent (semantic 0.60 / graph 0.10 / bm25 0.15).
- `query_understanding.py` (513 LOC): **zero ML, zero HTTP** (stdlib only) — enrich embedding_text bằng domain keyword expansion.
- `signal_normalizer.py` (209 LOC): normalize score về [0,1] — pure math.
- Doc-side "rerank" là **heuristic weighted formula** (entity/type/confidence weights trong `mcp_graph_rag.py`), KHÔNG có cross-encoder model.

### Đánh giá lại sau inventory

**Củng cố khả thi (so với lần trước):**
1. ML surface đóng đúng 3 model → 1 Python ML sidecar duy nhất chứa được tất cả (NER + 2 embedder).
2. Query path chỉ cần 1 query embedding/lần → sidecar penalty ~0; toàn bộ retrieval brain (fusion, BM25, normalizer, query understanding) là pure logic port 1:1 sang Rust.
3. `CodeEmbedder` bị copy 11 lần trong analyzers (~1.5k duplicated LOC) — Rust hoá thành 1 embed module; có thể dedupe trong Python trước port để giảm rủi ro.
4. Không có cross-encoder/reranker model — ít một model phải port/parity.

**Parity gates bắt buộc (2 embedding model, chặt hơn NER):**
- jina-v3: task-LoRA default phải khớp ONNX export (repo không truyền task name) — nếu lệch → re-embed toàn bộ code collections.
- bge-m3: model đa-output (dense+sparse+colbert) — ONNX chỉ lấy dense head; golden cosine ≥ 0.999.
- Fusion scores: golden test weight profile + BM25 normalization để kết quả ranking không đổi.

**Verdict giữ CAUTION — nhưng risk table ngắn hơn:** Critical duy nhất còn lại là embedded DB seam (falkordblite → sidecar hoặc Kuzu). ML đã được đóng hộp hoàn toàn. Query path + retrieval brain giờ là ứng viên Rust sáng giá nhất (thuần thuật toán, có golden test được).

## Next step

Chạy `/hi-predict` depth `deep` trên scope đã chọn (A hoặc B) → nếu CAUTION/GO thì `/hi-plan` chia phase port.
