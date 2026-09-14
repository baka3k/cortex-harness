# Phase 02 (follow-up) — root-cause regression latency 25-31ms/tool-call: **RESOLVED**

Đo 2026-09-14, macOS arm64 (10 logical cores: 4P+6E), HEAD sau `831a09e`. Đây là việc
còn lại của P02 mục 3(b) trong `reports/phase02-bgem3-parity.md` — khi đó chưa flip
được `CORTEX_EMBED_BACKEND` vì (a) drift score 1e-7 chờ P04 re-baseline và **(b)
regression latency end-to-end chưa giải thích được**. Báo cáo này đóng (b) bằng bằng
chứng đo từng bước; (a) vẫn là việc của phase-04 (deferred, decision #7).

## 1. Tách khối lượng bằng trace trong server

Thêm trace env-gated (`CORTEX_EMBED_TRACE=1`) vào `mind/tools.rs` (embed vs qdrant vs
body) và `mind/qdrant.rs` (`send` vs `read`), cùng `[cortex-embed.trace]` trong
`cortex-embed/src/onnx.rs` (tokenize/run/pool). Kết quả trong server thật:

| backend | embed (trong server) | qdrant | body tổng | client p50 |
|---|---|---|---|---|
| `python` (worker NDJSON) | 42ms | **4-6ms** | ~47ms | 47.0ms |
| `onnx` | 19-21ms (= isolated) | **46-57ms** | ~70ms | 71.7ms |

Embed onnx trong server **không hề chậm hơn** isolate (21.1 vs 21.4ms p50). Toàn bộ
+45ms rơi vào… request HTTP tới qdrant. Ngoài ra mỗi `semantic_search` phát **2
request**: `GET /collections` (availability check, chậm ~45ms) rồi `POST
/points/search` (nhanh ~3ms).

## 2. Loại trừ giả thuyết (mỗi cái một phép đo)

| Giả thuyết | Phép đo | Kết quả |
|---|---|---|
| ORT spin-wait đốt CPU | matrix `CORTEX_EMBED_ORT_SPIN=0`, `ORT_THREADS=2/4`, `DETERMINISTIC=0` × 60 rounds | p50 70.3-74.3ms, **không đổi** |
| Embed chậm hơn trong server | trace `embed_p50` in-server vs isolated | 21.1 vs 21.4ms — giống nhau |
| Mutex embedder chặn | trace `lock_wait` | 0.0ms |
| ORT làm chậm TCP trong process | raw `TcpStream` trước/sau ORT (`examples/qdrant_probe.rs` cũ) | ~1.5-2ms cả trước lẫn sau |
| Tokio + blocking ureq | repro `cortex-mcp/examples/latency_repro.rs` V1/V2 | POST 1.5-4ms — nhanh |

## 3. Root cause: stall ~45ms của GET nhỏ trên connection keep-alive vừa idle

Repro tối giản (`latency_repro.rs`, không cần ORT):

| Biến thể | Gap trước request | GET `/collections` | POST |
|---|---|---|---|
| V3 sleep20 + GET + POST | 20ms | **43-53ms** | 2.9ms |
| V4 sleep60 + GET + POST | 60ms | 3.0-3.9ms | 3.5ms |
| V5 sleep20 + POST only | 20ms | — (không GET) | **4.9-9.1ms** |
| V6 sleep20 + GET `Connection: close` | 20ms | **3.4-4.0ms** | — |

Trigger là **request nhỏ trên connection keep-alive được reuse sau idle ~20-50ms** —
cửa sổ delayed-ACK/Nagle trên đường qua ssh port-forward của colima (qdrant chạy
Docker trong VM; `127.0.0.1:6333` là `ssh` mux của lima, kiểm chứng bằng `lsof`).
Request lớn (POST 20KB) không dính vì full-MSS segment không bị Nagle giữ; connection
mới không dính vì không có data chưa-ACK.

Vì sao phase-02 trước thấy worker "nhanh hơn onnx trong server": worker Python embed
mất ~42ms → gap giữa 2 request rơi ngoài cửa sổ stall; onnx embed nhanh hơn (~19ms)
→ gap rơi ĐÚNG trong cửa sổ → mỗi search bị phạt +45ms, nhiều hơn số tiền embed tiết
kiệm được. Regression không phải do ORT — nó là tương tác thời gian giữa embed nhanh
hơn và HTTP keep-alive qua tunnel.

## 4. Fix: cache TTL cho danh sách collection của search path

`cortex-mcp/src/mind/qdrant.rs` — hàm mới `collection_names_cached()`:

- TTL mặc định **30s**, env `CORTEX_MCP_QDRANT_LIST_TTL_MS` (0 = tắt, quay lại GET
  mỗi lần). 30s > một phiên benchmark/tool-call điển hình nên measure không phổi p95.
- **Chỉ dùng cho availability check trong `qdrant_search_entity_payload`**
  (`tools.rs:360`). Tool-facing `list_qdrant_collections` vẫn gọi thẳng
  `list_collection_names` — hành vi tool không đổi.
- Local backend (`QdrantBackend::Local`) là directory scan, đọc thẳng không cache.

Trade-off đã cân nhắc: trong TTL, collection mới tạo có thể chưa thấy trong availability
check → case search với `collection`/`project_id` scope sẽ report lỗi từ chính POST
search thay vì LookupError "not ingested". Fixture phase-12/13 là static nên replay
byte-identical; rủi ro chỉ ở runtime có ingest đồng thời.

## 5. Kết quả sau fix

| cấu hình | p50 | p95 | verdict |
|---|---|---|---|
| Python reference server | 50.3ms | 51.6ms | baseline |
| Rust backend `python` (worker) | 47.0ms | 48.0ms | PASS |
| **Rust backend `onnx` + fix** | **25.1ms** | **26.1ms** | **PASS — nhanh nhất** |

_embed 20.1ms + POST ~4ms ≈ 25ms — đúng dự đoán sau khi bỏ GET._

Gates xác minh:

- `cargo test -p cortex-embed --test embed_golden -- --ignored`: 3/3 PASS (mặc định).
  Chạy lại với `CORTEX_EMBED_ORT_SPIN=0`: 3/3 PASS — spin-off **không đổi số học**
  (spin chỉ là lịch chạy thread).
- `compare_mind.py` backend `python`: **38/38 PASS** (rollback byte-equivalent).
- `compare_mind.py` backend `onnx`: 24/38 — `initialize`/`tools/list`/`gliner`/
  **latency P95 PASS**; 14 fail còn lại đúng là drift score 1e-7 của
  `semantic_search.*` đã ghi ở phase-02, chờ P04 re-baseline (deferred, decision #7).

## 6. Công cụ mới để đo lại

- `scripts/rust_parity/bench_mind_server.py` — bench end-to-end theo ma trận env
  (backend/threads/spin), boot python reference 1 lần, parse trace từ stderr log,
  so `rust_p95` vs `python_p95` per config.
- `cortex-mcp/examples/latency_repro.rs` — repro root-cause (V1-V6) giữ lại làm tài
  liệu thí nghiệm.
- Env mới: `CORTEX_EMBED_TRACE` (stage timing), `CORTEX_EMBED_ORT_SPIN` (spin-wait
  của ORT pool; default on giữ nguyên hành vi, parity đã chứng minh không đổi),
  `CORTEX_MCP_QDRANT_LIST_TTL_MS`.

## 7. Kết luận

P02 blocker (b) **đóng**: onnx không những không còn regression mà còn là backend
nhanh nhất end-to-end (26.1ms p95, ~2× nhanh hơn worker). Việc flip
`CORTEX_EMBED_BACKEND` mặc định vẫn phải đợi P04 re-baseline fixture cho drift 1e-7
(blocker (a)) — không đổi so với decision #7 của plan.
