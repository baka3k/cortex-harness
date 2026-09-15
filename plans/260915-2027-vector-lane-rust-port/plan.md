# Plan — Port vector-lane MCP sang Rust (local + remote mode)

Ngày: 2026-09-15. Tiền đề: `plans/260913-2130-rust-full-migration` (phase-11/12/13 parity
PASS) + `plans/260914-1706-onnx-embedding-spike` (embedder ONNX parity PASS, latency
root-cause đã fix). Kế thừa decision "ingest giữ Python" của spike phase-05.

> blockedBy-note: plan này **blocks** `plans/260915-2230-python-legacy-cleanup` —
> phase-03/04/05 phải xong trước khi cleanup xoá Python MCP plane (Python unified/mind
> vẫn là parity ground-truth + rollback của lane đang port).

## 1. Bối cảnh — vì sao cần plan riêng

MCP server Rust (`cortex-mcp`) đã pass parity toàn bộ surface trừ **vector lane**:

| Tool / lane | Python (ground truth) | Rust hôm nay | Vị trí stub |
|---|---|---|---|
| `semantic_search` (unified/code) | jina-v3 embed → qdrant local/remote thật | **stub — trả `results: []` cứng** | `cortex-mcp/src/graph/tools_semantic.rs:337` |
| `explore_graph` qdrant seeds | seed song song keyword lane rồi fusion | **stub — `seeds_qdrant: Vec::new()`** (fusion/BM25/graph-expansion đã port đủ) | `cortex-mcp/src/graph/tools_explore.rs:608` |
| `semantic_search`, `query_graph_rag_langextract` (mind/doc) | bge-m3 embed → qdrant local/remote thật | remote OK; **local trả lỗi runtime** | `cortex-mcp/src/mind/qdrant.rs:330-337` |

Hệ quả thực tế trên instance `cortex` (server Python đang chạy có embedder thật,
`qdrant/code` = 132MB dữ liệu thật): flip `dev mcp start` sang Rust hôm nay sẽ
**mất kết quả vector một cách âm thầm** ở lane code, và **lỗi runtime** ở lane doc-local.

## 2. Hiện trạng kỹ thuật (khảo sát 2026-09-15)

Python side (ground truth):

- Code lane: `code-tiny/mcp/cplus/cplus_mcp.py` (`semantic_search` :1641-1812,
  collection resolution :959-1062) + `tools/common/embed_runtime.py` (jina-v3,
  mean-pool + L2, max_len 8194, encode lane, LRU key `(model,text)`, device auto→MPS)
  + `tools/common/qdrant_query_support.py` (filter `project_id_normalized match.any`
  prefix-expansion :52-84, `merge_hits` dedupe theo point id :87-100,
  `PayloadSelectorExclude(["text"])`, hnsw_ef env `QDRANT_HNSW_EF`).
- Doc lane: `doc-tiny/mcp_graph_rag.py` (`semantic_search` :735-796,
  `qdrant_search_entity_payload` :265-373, dedupe key
  `(project_id_normalized, source_id, paragraph_id, text)`), bge-m3 CLS+L2 max_len 8192,
  device default **cpu**, filter `source_id` MatchValue + project filter.
- `explore_graph`: `services/explore_service.py` + `tools/common/intelligent_retrieval.py`
  (qdrant seed :700-730, mode weights :308-312, multi-project fan-out :490-570).
- Local store: `QdrantClient(path=...)` — **qdrant-client QdrantLocal**.

Rust side (đã có sẵn, tái sử dụng được):

- `cortex-embed`: **cả jina-v3 lẫn bge-m3 đã implement** (fp32 ONNX, mean/CLS-pool đúng,
  truncation 8194/8192, provenance pin, parity gate cosine ≥ 0.999 — jina 520/520,
  bge 840/840 PASS). Backend chọn bằng `CORTEX_EMBED_BACKEND=python|onnx`.
- `cortex-retrieval`: fusion/BM25/intent/normalize đã port — chỉ thiếu seeds từ qdrant.
- Remote qdrant: `mind/qdrant.rs` (search REST, ureq) + `cortex-storage/src/qdrant_remote.rs`
  (surface đầy đủ). Chưa thống nhất một client dùng chung.
- Mind plane **đã chạy vector search thật** qua remote: `mind/tools.rs:321+` +
  `mind/embed.rs` (python sidecar | onnx).

Phát hiện quyết định về **local store** (inspect `storage.sqlite` của instance cortex):

```
points(id TEXT, point BLOB)   -- BLOB = pickle của qdrant_client PointStruct
-- vd collection lớn nhất: cortext_4ee207813f__python_functions, 8587 points / 128MB
```

→ Format on-disk là **pickle** (không thể đọc an toàn từ Rust), và **ingest vẫn là Python**
(spike phase-05: NO-GO port ingest). Mọi phương án "migrate sang native store" đều bị
**drift** ngay sau lần sync đầu.

## 3. Quyết định kiến trúc (ADR)

| # | Quyết định | Lý do | Phương án bị loại |
|---|---|---|---|
| D1 | **Remote mode = native Rust** (REST ureq, gom 1 client dùng chung cho 2 server) | Sẵn sàng 90% (`mind/qdrant.rs`, `cortex-storage::qdrant_remote`); remote là wire protocol ổn định | — |
| D2 | **Local mode = Python vector sidecar** `vector_worker.py` (persistent NDJSON stdio, chỉ `qdrant-client` + numpy, **không torch/transformers**) | QdrantLocal = pickle → Rust không đọc được; ingest Python là writer duy nhất → sidecar đọc đúng file Python ghi, không drift; đúng pattern `embed_worker.py` đã chuẩn hoá ở spike | (a) đọc pickle bằng Rust — no-go; (b) migrate sang native store — drift khi sync; (c) spawn qdrant binary — không đọc được định dạng file QdrantLocal |
| D3 | **Query embedding = `cortex-embed` ONNX**, flip default `CORTEX_EMBED_BACKEND=onnx` sau khi re-baseline fixture (nợ của spike phase-04) | Latency root-cause đã fix (onnx p95 26.1ms — nhanh nhất); parity 0.999 PASS cả 2 model | giữ python sidecar làm **rollback flag**, không phải default |
| D4 | **Hợp đồng đúng đắn = golden capture từ Python có embedder thật (embedder ON)**, so cấu trúc byte-level + score tolerance tuyệt đối **1e-6** (drift chữ số thứ 7 ~1e-7 là đặc tính ONNX, đã đo ở spike) | Parity phase-11/13 cũ dùng embedder tắt → không phản ánh production | so byte-level tuyệt đối cho score |

Điểm cắm trong Rust đã xác định: thay `seeds_qdrant` (`tools_explore.rs:608`) và
`results` (`tools_semantic.rs:337`) bằng embed → `VectorSearch` trait → feed
`cortex_retrieval::fusion::candidate_from_qdrant_hit`. Mind: thay nhánh lỗi local
(`qdrant.rs:330`) bằng cùng trait.

Trait thống nhất (mới, đặt trong `cortex-storage` hoặc `cortex-mcp::vector`):

```
VectorSearch::search(backend, collection, vector, limit, filter: Option<Filter>) -> Vec<Hit>
Hit = { id, score, payload, collection }   // đúng shape Python: {id, score, payload, _collection}
Backend = Remote{url, api_key} | LocalSidecar{worker}
```

## 4. Lộ trình phase

| Phase | Nội dung | Gate chính |
|---|---|---|
| 01 | Contract & golden capture (Python embedder ON, local + remote) | fixture ghi từ server Python thật; comparator tolerance 1e-6 |
| 02 | Flip query-embedding sang ONNX (re-baseline nợ spike phase-04) | parity tool-level ≥ tolerance; p95 embed ≤ python worker; rollback python còn nguyên |
| 03 | Remote mode native (unified `semantic_search` + `explore_graph` seeds; gom client) | parity 100% cấu trúc vs Python+remote trên fixture thật |
| 04 | Local mode vector sidecar (`vector_worker.py`, không torch) | parity vs Python local trên snapshot instance `cortex`; worker import < 2s, RSS < 300MB |
| 05 | Cutover vận hành + docs | `dev mcp start` auto-flip cho cả 2 server; runbook cập nhật; rollback flag test |

Thứ tự cố ý: **02 trước 03/04** — mọi parity của 03/04 đều cần query-embedding deterministic
giữa 2 phía, mà điểm khác biệt duy nhất còn lại chính là embedder.

## 5. Rủi ro & mitigation

- **Drift điểm số 1e-7** (ONNX vs torch): chấp nhận, đóng băng bằng tolerance 1e-6 trong
  comparator + golden fixtures mới ở phase-02. Không bao giờ so score tuyệt đối.
- **Sidecar local là Python Process còn sống trong MCP runtime**: chấp nhận có kiểm soát —
  worker KHÔNG import torch/transformers (chỉ qdrant-client + numpy), khởi động < 2s;
  đường thoát dài hạn là chuyển local store sang remote hoặc native ingest (ngoài phạm vi).
- **QdrantLocal format version-coupled**: sidecar dùng chính `.venv` qdrant-client như
  ingest → cùng version, không tự parse format. Contract test pin version.
- **explore_graph fusion sai khác**: fusion/BM25/graph đã port + có golden từ phase-12;
  chỉ cần seeds đúng shape `{id, score, payload, _collection}` + dedupe point-id.
- **Hai bug đang mở phát hiện 2026-09-15** (fixture acceptance-matrix stale 24 fail;
  `list_source_ids` binder exception khi `project_id=None` — pattern lặp 4 chỗ Python +
  mirror Rust `mind/graphstore.rs`): **bắt buộc fix + mirror 2 phía trước phase-05**,
  vì cutover mặc định sẽ chuyển người dùng sang server Rust.

## 6. Rollback

- `CORTEX_MCP_BACKEND=python` — ép cả 2 server về Python (đã có, phase-14).
- `CORTEX_EMBED_BACKEND=python` — embedder quay về sidecar (đã có, spike).
- `CORTEX_MCP_VECTOR_WORKER` (mới, phase-04) — override đường `vector_worker.py`,
  giá trị rỗng → local search trả lỗi như hôm nay (không bao giờ fail-open sai dữ liệu).
- Vector lane Rust luôn phủ bởi golden parity trước khi flip default; flip là 1 commit
  config, revert 1 commit.

## 7. Deliverables

- `scripts/rust_mcp/vector_worker.py` + golden fixtures (`scripts/rust_mcp/fixtures/`).
- Comparator harness `scripts/rust_mcp/compare_vector.py` (mở rộng `compare_mind.py`).
- Rust: `VectorSearch` trait + remote client thống nhất + 2 điểm cắm thay stub.
- `docs/cutover-runbook.md` mục mới "vector lane"; cập nhật `Makefile` (nếu cần target
  fixture); report per-phase trong `reports/`.
