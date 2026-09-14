# Phase 02 — bge-m3 dense ort + swap seam mind/doc: **PARTIAL** (parity PASS, flip CHƯA đạt)

Đo 2026-09-14, macOS arm64, CPU-only, HEAD `c6de7d2`. Artifact: graph chính thức
`BAAI/bge-m3/onnx/model.onnx` (opset 11, 2.27GB, snapshot `5617a9f6…`), inputs đúng
`input_ids`+`attention_mask`, outputs `token_embeddings`(idx 0)+`sentence_embedding`.

## 1. Gates

| Gate | Yêu cầu | Đo được | verdict |
|---|---|---|---|
| Cosine fp32 | ≥ 0.999 từng cặp, ≥300 paragraphs + ≥100 queries | **840 cases (520 code + 320 doc), mean 1.0000001, worst 0.9999994, 0 dưới gate**; token-id **840/840 drift=0** | **PASS** |
| Reproducibility | fixture sinh lại như nhau | cả 2 plane **byte-identical** | **PASS** |
| P95 query-embed (mức embed, warm) | ≤ P95 sidecar persistent, cùng máy | ort **p50 21.4 / p95 23–25.1ms** vs worker **p50 39.6 / p95 41.2ms** | **PASS (1.6× nhanh hơn)** |
| Throughput one-shot doc-ingest | ≥ 0.8× sidecar | ort batch8 **10.5–10.8** vs worker **12.6** texts/s trên paragraph = **0.83–0.86×**; nhưng one-shot CLI thật: ort **1.6s** vs sidecar **4.2s** wall | **PASS** (kèm ghi chú: ort scale kém hơn khi batch lớn) |
| Rollback `CORTEX_EMBED_BACKEND=python` | byte-equivalent hiện trạng | `compare_mind.py` **37/37 pass, 0 fail** (và 38/38 ở run đủ gate); CLI `embed` giữ nguyên keys/`dimension`/giá trị `python=".venv/bin/python"` | **PASS** |
| `compare_mind.py` all-gates PASS **với backend onnx** | 0 fail | **15 fail**: `mind tools/call` 16/30 (drift score ~1e-7), `latency P95` fail | **FAIL** — xem mục 3 |
| int8 (gate kép cosine + recall@10) | như P01 | chưa chạy | **OPEN** |

## 2. Số học đã sửa so với plan

`findings.md` C2 đã bác "bge-m3 encode raw không normalize": `modules.json` có
`2_Normalize` + `1_Pooling: pooling_mode_cls_token=true`, `max_seq_length=8192`.
Crate dùng `Pooling::Cls` + L2-normalize, token cap 8192 (không phải 512), và
**không** đọc `sentence_embedding` của graph — Rust tự CLS+normalize từ
`token_embeddings`, để đường số học nằm trong code có test chứ không trong artifact.
Gate cosine chứng minh lựa chọn này đúng (0.9999994 worst).

## 3. Hai phát hiện làm P02 chưa flip được

**(a) Contract drift ~1e-7 ở score, không phải lỗi port.**
`semantic_search.*` fail kiểu `0.78921336 != 0.7892131` — chữ số thứ 7.
Vector cosine 0.9999994 ⇒ điểm truy vấn (dot với vector đã lưu) lệch ~1e-7 là tất yếu.
Harness phase-13 so score key với tolerance **1e-9 tuyệt đối**, vốn được hiệu chuẩn
cho đường Python-bit-identical. Đây chính xác là hệ quả mà decision #7 đã chỉ ra và
giao cho **phase-04 (re-baseline golden fixtures)** — không phải defect số học.
Vì P04 đang defer (dogfood), gate này KHÔNG THỂ pass trong phase-02.

**(b) Regression latency end-to-end, chưa giải thích được.**
`compare_mind --latency-rounds 60` (steady state, lặp lại 2 lần):

| backend Rust server | rust p50 | rust p95 | python server p95 |
|---|---|---|---|
| `python` (worker persistent) | 43.2ms | **44.1ms** | 50.1ms ✅ PASS |
| `onnx` | 68.4ms | **75.2ms** | 48.6ms ❌ FAIL |

Mâu thuẫn có chủ đích cần ghi lại: đo cô lập thì ort NHANH hơn worker
(25.1 vs 41.2ms), nhưng trong server MCP thật nó CHẬM hơn ~25-31ms/tool-call.
Đã loại trừ: cold-load session (60 rounds, vẫn 68.4 p50), số threads
(4 vs 8: 23.5 vs 21.4ms), `deterministic_compute` (bật/tắt: không đổi đáng kể).
Giả thuyết còn lại, chưa kiểm chứng: áp lực page-cache khi 2.27GB graph sống chung
với store/server, hoặc tokio worker thread tương tác với intra-op pool của ORT.
**Việc này phải giải quyết trước khi bật `onnx` làm mặc định.**

## 4. Đã đổi gì

- `cortex-doc/src/embed.rs`: `encode()` thành router — `encode_sidecar()` (giữ
  nguyên 100% hành vi cũ) | `encode_onnx()`; thêm `backend_label()` cho key
  `python` trong output để parity report hiện đúng backend.
- `cortex-mcp/src/mind/embed.rs`: `encode_query()` thành router; `encode_query_worker()`
  giữ nguyên; `encode_query_onnx()` cache session trong `OnceLock<Mutex<Option<…>>>`
  (không load lại 2.27GB mỗi query). Hai call site `tools.rs:664,775` không đổi.
- `cortex-embed`: `SessionConfig` thêm env `CORTEX_EMBED_ORT_THREADS`,
  `CORTEX_EMBED_ORT_DETERMINISTIC`; harness `embed_golden.rs` thành **một gate đa
  plane** (quét `tests/fixtures/*_golden.json`, dispatch theo field `plane`) nên
  thêm model mới chỉ cần thêm fixture. `throughput_probe` đo theo lane query thật.
- `scripts/rust_parity/`: `fetch_bge_onnx.py` (artifact chính thức + checksum),
  `gen_embed_fixtures.py` tham số hoá `--plane code|doc`,
  `bench_mind_worker.py` (baseline sidecar persistent qua đúng NDJSON protocol).
- Dependency mới: `cortex-doc` và `cortex-mcp` → `cortex-embed`.

## 5. Sửa một lỗi harness thật, không phải lỗi sản phẩm

Corpus golden ban đầu **trôi theo file mới**: thêm `scripts/rust_parity/bench_*.py`
đã chiếm slot và đẩy `bench_journal.py` ra khỏi 500 text ⇒ fixture không tái lập
được. Đã sửa: (i) loại `scripts|plans|tests|installers` — harness không tự nhúng
mình; (ii) thứ tự corpus theo `sha256(đường dẫn)` thay vì theo đường dẫn (sort theo
tên làm 500 slot đầu rơi hết vào một thư mục). Kết quả phân bố: code-tiny 240,
rust 234, doc-tiny 14, cortex_harness 12 và **byte-identical khi sinh lại**.
Nhớ lại: `code-tiny` là first-party (528 file tracked, không submodule) nên không
được loại khỏi corpus.

## 6. Kết luận phase-02

Parity số học và rollback đã chứng minh ở mức chặt (cosine 1e-7, contract 37/37
khi `python`). **Nhưng `CORTEX_EMBED_BACKEND` vẫn phải giữ mặc định `python`**:
flip sang `onnx` cần (1) re-baseline fixture của phase-04 cho drift ~1e-7 và
(2) tìm ra nguyên nhân regression latency 25-31ms/tool-call ở mục 3(b).
Không có hai thứ đó thì bật onnx là làm vỡ hợp đồng phase-13 đã pass.
