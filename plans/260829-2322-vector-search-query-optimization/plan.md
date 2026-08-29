---
title: "Vector-search query latency optimization — embed runtime, payload path, collection tuning, ingest reuse"
status: implemented
created: 2026-08-29
implemented: 2026-08-30
mode: hi-plan --full
scope: "MCP query-time embedding runtime (shared embedder + query-vector cache + device auto-detect), Qdrant payload narrowing + collection metadata cache, collection tuning (payload indexes / HNSW env / optional quantization) with operator rebuild script, ingest embedder reuse + batch defaults + upsert wait policy, benchmark harness with numeric gates"
blockedBy: []
blocks: []
relatedPlans:
  - 260828-1508-multi-instance-fanout-default
  - 260807-0929-mcp-ingest-query-concurrency
  - 260818-infra-up-remote-support
  - 260728-0900-simplify-search-full-removal
  - 260817-storage-backend-adapter
  - 260806-1648-local-file-storage
---

# Vector-search query latency optimization

## Overview

Semantic search (`semantic_search` MCP tool) và các thao tác liên quan vector
search chậm. Research xác định chi phí truy vấn nằm ở: (1) embed query bằng
`jinaai/jina-embeddings-v3` (~570M params) trên CPU, batch 1, mỗi lần gọi,
không cache — và bị **nhân bản**: bốn backend module
(`fastmcp_server`, `cplus_mcp`, `android_mcp`, `java_mcp`) giữ bốn bản sao
byte-identical của pipeline embed với bốn `_embedder_cache` riêng;
(2) mỗi search gọi `get_collection_info` cho **từng** collection candidate
(lên tới 3 lần danh sách mỗi query) không có cache; (3) mọi hit trả về full
payload (`with_payload=True` hardcode tại
`code-tiny/tools/common/local_qdrant.py:229`) gồm `text` tới 16.000 ký tự;
(4) Qdrant local-mode không tune — không HNSW config, không quantization,
payload index duy nhất `project_id_normalized` trong khi filter dùng thêm
`parser`/`root_scope`/`file_path`; (5) ingest tạo SentenceTransformer mới
mỗi lần sync và upsert `wait=True` từng batch với `BATCH_SIZE=1` mặc định
trong dev-init config.

Plan này **không** đụng hai hành vi được người dùng xác nhận là spec:
quét tuần tự collection khi không truyền `project_id`, và fan-out đa
instance tuần tự của FalkorDB (xem Non-goals). Model giữ nguyên
`jinaai/jina-embeddings-v3` — không re-index; tối ưu thuần runtime, trừ
phase tune collection (bắt buộc rebuild mới nhận HNSW/quantization, nhưng
copy vector nên không re-embed; giá trị tuning chỉ materialize trên Qdrant
server — local mode no-op hóa payload index và nuốt HNSW kwargs).

**Bề mặt production (red team #2):** dev.py launch `unified_mcp.py`
(`dev.py:171-177`); `semantic_search` không nằm trong `_FANOUT_SEARCH_TOOLS`
nên dispatch về default backend `cplus` → `cplus_mcp.tool_semantic_search`
(`unified_mcp.py:150-166`, `:451`, `:531-536`). Bốn backend giữ 4 bộ helper
search song song (`_qdrant_search`, `_merge_qdrant_results`,
`_filter_collections_for_vector`…). Mọi thay đổi query-path vì vậy được
gom vào helper chung `tools/common/` cho cả 4 backend delegate, và
benchmark đo qua unified dispatch — không đo lệch sang `fastmcp_server`.

## Scope challenge decisions (2026-08-29, user-confirmed)

### 1. Phạm vi: loại #2 và #5

**Decision:** "Quét tuần tự tất cả collection" và "Graph fan-out đa instance
tuần tự" là spec — truyền `project_id` thì search scoped, không truyền thì
search tất cả. Không song song hoá, không đổi ngữ nghĩa. Toàn bộ đối sách
còn lại (embed runtime, payload/metadata path, collection tuning, ingest)
thuộc phạm vi.

### 2. Model: giữ jina-v3, tối ưu runtime + auto-detect device

**Decision:** không đổi model mặc định, không re-index. Bổ sung yêu cầu:
auto-detect device — macOS có MPS thì mặc định MPS, Windows/Linux có CUDA
thì mặc định CUDA, chỉ fallback CPU khi unavailable/fail. Explicit
`EMBED_DEVICE` vẫn được tôn trọng với hành vi fallback hiện tại.

### 3. Tiêu chí: benchmark trước/sau với mục tiêu số

**Decision:** Phase 01 xây harness đo p50/p95 theo stage (embed / metadata /
qdrant / expansion / total) và chốt baseline. Mỗi phase sau phải chứng minh
cải thiện mục tiêu của mình trên benchmark và không regression các stage
khác trước khi merge.

### 4. (In-plan) Thống nhất lệch CPU-retry giữa 4 bản sao

`fastmcp_server._embed_query` retry CPU trên mọi RuntimeError khớp
cuda-error (`:713`); ba bản còn lại chỉ retry khi `str(device).startswith
("cuda")` (cplus `:963-964`, android `:779`, java `:628`). **Decision:**
thống nhất theo hành vi fastmcp (retry khi phát hiện cuda-runtime error,
gated `EMBED_FALLBACK_TO_CPU` mặc định "1") — an toàn hơn và là hành vi
module chính.

### 5. (In-plan) Hợp đồng payload narrowing

`tool_semantic_search` đọc `summary`/`comment`/`code` để dựng `content`
trước khi prune (`fastmcp_server.py:380-405`), còn collection của
`primary_vector_sync` chỉ ghi `text` (không ghi summary/comment/code) —
nghĩa là loại `text` khỏi selector làm mất nội dung hiển thị của collection
mới. **Decision (red team #5):** dùng `PayloadSelectorExclude(["text"])`
thay vì Include liệt kê — không phụ thuộc việc liệt kê đủ key của mọi họ
writer (kotlin/android_kotlin có `class_name`/`package_name` dễ bị sót);
chi phí còn lại là kéo thêm vài field nhỏ, chấp nhận. `_select_content`
thêm fallback preview `text` giới hạn (400 ký tự đầu) khi thiếu
summary/comment/code; full `text` chỉ được fetch lazy bằng `retrieve` cho
các hit cuối (≤ top_k) khi caller xin raw fields — retrieve group theo
collection nguồn, hit được **tag `_collection` ngay từ `_qdrant_search`**
(red team #6: point id trùng được giữa các collection vì uuid5
deterministic). Output contract của tool giữ nguyên shape; có test fixture
cho cả ba họ payload (legacy analyzer, primary_vector_sync,
kotlin/android_kotlin) khóa hành vi.

### 6. (In-plan) Cache nhúng phải nằm ở `tools/common` — module MỚI `embed_runtime.py`

`unified_mcp.py` load backend bằng `importlib` dưới alias
`cplus_backend`/`android_backend`/`fast_backend` (`:63-65`), nên module-level
cache trong file backend bị nhân bản nếu file còn được import dưới tên thật
(`explore_service.py:190` làm đúng vậy với cplus). **Decision:** cache
embedder + query-vector đặt ở module mới
`code-tiny/tools/common/embed_runtime.py` — mọi consumer đã import
`tools.common.*` bằng tên ổn định trước khi backend chạy. **Cảnh báo
(red team #1):** `code-tiny/tools/common/embedding_runtime.py` đã tồn tại
với vai trò khác (`resolve_embedding_cache` + HF network-audit, 12 analyzer
đang import) — module cũ không bị đụng; tên mới `embed_runtime.py` tránh
va chạm; test guard chốt 2 module không bị gộp. Các tên `_embed_query`,
`_get_embedder`, `_resolve_embed_device`… ở backend giữ nguyên làm thin
delegate vì test patch theo tên module
(`tests/test_qdrant_project_scope.py:181`).

### 7. (In-plan) Thứ tự tune collection

Không có đường upgrade in-place: `ensure_collection` raise khi vector-size
khác (`local_qdrant.py:167-188`), `recreate_collection` tồn tại nhưng không
ai gọi, không có flow copy point-with-vectors. **Decision:** (a) payload
index mới tạo idempotent tại sync-time (an toàn với collection hiện hữu);
(b) HNSW/quantization chỉ áp cho collection tạo mới qua env opt-in;
(c) rebuild collection hiện hữu = script operator copy vector
(scroll `with_vectors=True` → tạo collection tuned → validate count → swap),
yêu cầu `--yes`, không re-embed.

## Verified baseline (research 2026-08-29)

- 4 bản sao byte-identical của embed pipeline: `fastmcp_server.py`
  (`_embedder_cache`:192, `_resolve_embed_device`:626-639, `_get_embedder`
  :642-662, `_embed_query_with_model`:686-701, `_embed_query`:704-724,
  preload :733-744), `cplus_mcp.py` (:207/:879-892/:896-914/:939-955/:957-970),
  `android_mcp.py` (:186/:696-709/:711-731/:756-772/:774-787),
  `java_mcp.py` (:173/:545-558/:562-580/:605-621/:623-636).
- Query path: mean-pool `max_length=512`, **không L2-normalize**
  (`fastmcp_server.py:686-701`); ingest path normalize
  (`primary_vector_sync.py:297-303`) — chênh lệch đã tồn tại, plan này
  không đổi.
- Metadata: `_filter_collections_for_vector` bắn 1
  `get_collection_info`/collection/query (`fastmcp_server.py:961-1010`,
  bản cplus `:1188`), `_resolve_base_collections` gọi
  `_fetch_qdrant_collections` mỗi query (`:842-880`); cache duy nhất
  `_VECTOR_LAYOUT_CACHE` (dict `intelligent_retrieval.py:109`, logic
  `:125-149`) không TTL, không invalidate, cache cả giá trị lỗi `None`.
- Routing: dev.py launch `unified_mcp.py` (`dev.py:171-177`);
  `semantic_search` dispatch về default backend `cplus`
  (`unified_mcp.py:150-166`, `:451`, `:531-536`).
- Payload: `local_qdrant.query_points` hardcode `with_payload=True`
  (`local_qdrant.py:229`); `text` tới 16.000 ký tự
  (`primary_vector_sync.py:42`); repo chưa từng dùng `PayloadSelectorInclude`
  (qdrant-client==1.18.0 có sẵn cả Include/Exclude).
- Store: Qdrant local-mode (`cortex_harness/storage/qdrant.py:62`), collection
  tạo bằng `VectorParams(size, COSINE)` không HNSW/quantization
  (`local_qdrant.py:185-188`); payload index duy nhất `project_id_normalized`
  (`primary_vector_sync.py:197-207`).
- Ingest: SentenceTransformer mới mỗi `sync_vector_documents`
  (`primary_vector_sync.py:286-296`); upsert `wait=True` từng batch
  (`:326-332`); dev-init mặc định `BATCH_SIZE="1"` (`dev.py:2801`,
  `:2813`), `MAX_EMBED_CHARS="500"` (`:2802`); dev sync truyền device **chưa
  normalize** qua CLI (`dev.py:1519`, doc `:1085`); argparse fallback
  analyzer là 4 (python `:1918`, go `:1296`…).
- Device: `_resolve_embed_device` mặc định `"cpu"`, chỉ validate availability
  khi được set (`fastmcp_server.py:626-639`); `python_analyzer` đã có
  auto-detect đầy đủ với `--device` default `"auto"` tự resolve nội bộ
  (`python_analyzer.py:1917`, `:1977-1984`) — các analyzer khác và MCP
  backend thì chưa; `dev.py::_normalize_embed_device("auto")` hiện
  pass-through nguyên văn (`:571-572`).
- Test seam: patch `module._embed_query` (`test_qdrant_project_scope.py:181`),
  stub store + `_Embedder` inject qua `embedder_factory`
  (`test_primary_vector_sync.py`), pytest `pythonpath=["code-tiny","doc-tiny"]`,
  không repo-level conftest.
- Benchmark convention: `tests/benchmark_*.py` tồn tại;
  `scripts/validate_retrieval.py` (repo-root `scripts/`) là mẫu wiring
  runtime env + storage resolution cho tool đúng nghĩa.

## Non-goals (user-confirmed 2026-08-29)

- Không song song hoá vòng lặp collection (`_merge_qdrant_results`,
  `intelligent_retrieval._retrieve_qdrant`) — spec unscoped search.
- Không song song hoá fan-out FalkorDB đa instance hay lane retrieval của
  `explore_graph` — spec.
- Không đổi embedding model, không re-embed collection (rebuild copy vector).
- Không đổi ngữ nghĩa scoping `project_id` (đang theo
  `260728-0900-simplify-search-full-removal`).
- Không đụng gateway lane/lease (`260807-0929`) hay lifecycle fan-out
  (`260828-1508`).
- Không chuẩn hóa normalize query-vs-ingest (rủi ro chất lượng, không phải
  mục tiêu latency).

## Success criteria (numeric gates; số chốt lại sau Phase 01 baseline)

| # | Gate | Mục tiêu tạm | Phase chịu trách nhiệm |
|---|------|--------------|------------------------|
| G1 | Repeat-query embed (cache hit) | p50 < 5 ms | 02 |
| G2 | Cold embed trên macOS (MPS) | ≥ 2× nhanh hơn baseline CPU | 03 |
| G3 | `semantic_search` scoped, 1 collection | p50 giảm ≥ 30% (không tính expansion) | 02+04 |
| G4 | `semantic_search` unscoped, đa collection | p50 giảm ≥ 40% | 04+05 |
| G5 | `get_collection_info` round-trip / search | ≤ 1 (cold), 0 (cached) | 04 |
| G6 | Ingest embed throughput (default config) | ≥ 3× so với `BATCH_SIZE=1` | 06 |

Điều kiện merge mọi phase: toàn bộ suite hiện tại pass + benchmark không
regression ở các stage không thuộc phase + `scripts/validate_retrieval.py`
pass (correctness giữ nguyên). **Mọi gate đo qua unified dispatch**
(`unified_mcp._dispatch_tool("semantic_search", …)`), không đo thẳng
`fastmcp_server` — bề mặt production là cplus backend.

## Phases

| Phase | File | Nội dung | Phụ thuộc |
|-------|------|----------|-----------|
| 01 | [phase-01-benchmark-baseline.md](phase-01-benchmark-baseline.md) | Benchmark harness + timing instrumentation + baseline report | — |
| 02 | [phase-02-shared-embedding-runtime.md](phase-02-shared-embedding-runtime.md) | `tools/common/embedding_runtime.py`: shared embedder + query-vector LRU; 4 backend delegate; G1, G3(embed) | 01 |
| 03 | [phase-03-device-autodetect.md](phase-03-device-autodetect.md) | Auto MPS/CUDA/CPU + sửa plumbing dev.py; G2 | 02 |
| 04 | [phase-04-payload-narrowing-metadata-cache.md](phase-04-payload-narrowing-metadata-cache.md) | Payload selector + lazy text fetch + TTL metadata cache; G4, G5 | 01 |
| 05 | [phase-05-qdrant-collection-tuning.md](phase-05-qdrant-collection-tuning.md) | Payload indexes, HNSW/quantization opt-in, rebuild script; G4(Qdrant) | 04 |
| 06 | [phase-06-ingest-embedder-reuse-batch.md](phase-06-ingest-embedder-reuse-batch.md) | Embedder reuse, wait-on-last-batch, `BATCH_SIZE` 1→8; G6 | 02 |

Phase 03 và 04 chạy song song được (chạm nhau ở `embedding_runtime.py` chỉ
tại hàm `resolve_device` đã chốt interface từ Phase 02). Phase 05 và 06 độc
lập nhau.

## Cross-plan dependencies

- **260828-1508-multi-instance-fanout-default** (pending): trùng file
  `fastmcp_server.py` (fan-out Cypher path). Plan này không sửa `_run_cypher_first`
  cũng như discovery; chỉ file-level merge order. Không block.
- **260807-0929-mcp-ingest-query-concurrency** (pending): Phase 06 đụng
  write path của `primary_vector_sync` (wait policy) — nằm trong staging
  write hiện có, không đổi lane/lease/generation contract của gateway.
- **260818-infra-up-remote-support** (active): benchmark Phase 01 có mode
  chạy với remote Qdrant nếu config chỉ tới server; plan này không xây
  lifecycle (đã thuộc plan kia).
- **260728-0900-simplify-search-full-removal** (pending): contract
  `project_id`-only giữ nguyên — các selector/cache không phụ thuộc scoping.
- **260817-storage-backend-adapter**: selector/kwargs đi qua adapter layer
  (`LocalQdrantStore`/`RemoteQdrantStore` mirror API) — cả hai adapter phải
  pass-through `with_payload` selector.

## Risks & rollback

| Rủi ro | Giảm thiểu | Rollback |
|--------|-----------|----------|
| Đổi hành vi output của `semantic_search` khi loại `text` khỏi payload | Exclude(["text"]) thay vì Include liệt kê + fallback preview + lazy retrieve theo `_collection` tag + fixture test cho 3 họ payload; gate `validate_retrieval.py` | revert commit phase 04 |
| MPS numeric khác CPU (jina-v3 có op tự fallback) + MPS fail làm crash boot preload | Acceptance Phase 03 gồm so sánh score CPU vs MPS trong tolerance; classifier lỗi mở rộng cho MPS/Metal; preload bọc try/except; `EMBED_DEVICE=cpu` (cần restart) là escape hatch | set env `EMBED_DEVICE=cpu` + restart |
| Cache stale sau khi collection đổi layout | TTL 300s + invalidate-on-error + `invalidate()` gọi ở sync/publish path | env tắt cache (mã giữ `MCP_COLLECTION_META_CACHE=0`) |
| Script rebuild destructive | Copy-then-validate-then-swap, bắt `--yes`, count validation, không re-embed | collection cũ chỉ bị xoá sau khi count khớp; backup thư mục Qdrant trước khi chạy |
| Alias importlib làm cache nhân bản | Cache nằm `tools/common` (tên import ổn định) + test khẳng định 2 module backend chia sẻ hit | revert phase 02 |
| Upsert `wait=False` giữa chừng làm dữ liệu lộ nửa chừng | Đường sync upsert thẳng collection sống (chưa có staging gate — thuộc `260807-0929` pending), partial visibility đã tồn tại hôm nay; chính sách tự vá là re-sync (`_delete_stale` + re-upsert); wait-on-last-batch giữ durability tại ranh giới hàm | revert phase 06 |
| Module trùng tên / gộp nhầm với `embedding_runtime.py` (HF audit, 12 analyzer import) | Module mới tên `embed_runtime.py`; test guard assert API cũ còn nguyên | revert phase 02 |

## Red team / validation

- Red team đã chạy 2026-08-29 (adversarial review đối chiếu codebase):
  verdict **FIX-FIRST** với 4 BLOCKER / 5 MAJOR — toàn bộ đã xử lý:
  (1) `embedding_runtime.py` trùng tên → module mới `embed_runtime.py`;
  (2) production route qua unified→cplus → Phase 04 gom helper chung cho
  4 backend, benchmark qua dispatch; (3) payload index/HNSW vô nghĩa ở
  local mode → Phase 05 chuyển remote-only, local là call-through có
  warning; (4) `device="auto"` raw crash analyzer → dev.py resolve trước
  khi rời launcher; (5) Include→Exclude(["text"]); (6) tag `_collection`
  cho lazy retrieve; (7) classifier lỗi MPS + preload guard; (8) rationale
  wait-policy viết lại theo re-sync self-heal; (9) mô tả
  `explore_service._make_embedder` sửa lại; kèm các MINOR line-ref.
  Chi tiết: `plans/reports/260829-2322-vector-search-query-optimization-redteam.md`.
- Interactive validate được bỏ qua theo mode `--full` (optional); các câu
  hỏi critical đã xử lý trong Scope challenge decisions.
