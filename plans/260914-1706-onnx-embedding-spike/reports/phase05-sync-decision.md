# Phase 05 — Quyết định sync-path + coverage 14/14: **NO-GO port ingest, giữ Python child**

Đo 2026-09-14. Đây là deliverable bắt buộc của phase-05 ("Điều P05 phải trả lời bằng số,
không bằng thị hiếu"), kèm checklist owner 14/14 dòng inventory của `plan.md`.

## 1. Decision record

**KHÔNG port `primary_vector_sync` (chunk + payload + upsert + `_hash_vector`) sang Rust
trong phạm vi spike này. Sync ingest tiếp tục là Python child.**

Bằng chứng (toàn bộ ở `reports/phase01-jina-parity.md` mục 4, cùng máy, CPU, 4 threads,
500 text ingest thật của repo):

| Đường | batch 8 (= `EMBED_BATCH_SIZE` mặc định) | batch 32 | batch 128 |
|---|---|---|---|
| torch Python (hiện tại) | 38.1 texts/s | 42.7 | 39.8 |
| ort Rust | **38.1 texts/s (1.00×)** | 32.8 (0.77×) | 25.7 (0.65×) |

Lập luận:
1. **Không có lợi ích throughput ở cấu hình production.** Batch 8 là mức mọi analyzer
   đang chạy (`EMBED_BATCH_SIZE` default 8) — ort ngang torch, không nhanh hơn. Ở batch
   lớn ort còn thua, nên "port xong rồi tăng batch" cũng không phải đường thắng.
2. **Chi phí port là rủi ro thật, không phải chi phí cơ học.** `primary_vector_sync` gồm
   redaction regex 3 tầng theo thứ tự (`_PRIVATE_KEY_RE` → quoted → unquoted, DOTALL),
   bounded text `min(max_chars,16000)`, `deterministic_point_id` (uuid5 với join order
   khác thứ tự tham số), stale-point cleanup, upsert contract. Mỗi thứ là một chỗ lệch
   im lặng tạo **vector sai trên dữ liệu thật** mà cosine gate vẫn có thể không bắt được
   (vì gate so vector, không so point id).
3. **`_hash_vector` message lane không port được rẻ.** Nó cần `str.lower()` bảng Unicode
   của Python + SHA-1 hex → **bigint 160-bit** `% 1024`, cộng thêm bài học U+001C..U+001F
   đã ghi ở `findings.md` C8: Rust `to_lowercase()`/`is_whitespace()` đều khác bảng
   Python ở biên. Bit-replicate là việc phải có test riêng, không phải "dịch code".
4. **Nơi ort THẮNG thì đã lấy được thắng lợi đó mà không cần port ingest** (mục 2 dưới).

Điều kiện mở lại quyết định này: nếu ingest trở thành nút thắt đo được (không phải cảm
giác) thì port theo `Embedder` trait tại seam orchestrator (#3) và phải kèm gate point-id
+ counts, không chỉ cosine.

## 2.win CONDITIONS: nơi ONNX native thực sự thắng (đã đo)

| Đường | Số | Nguồn |
|---|---|---|
| Query embed code plane (mức embed) | ort p50 21.4 / p95 23–25.1ms vs worker p50 39.6 / p95 41.2ms | P01+P02 bench |
| Query embed doc plane (mind) | ort p95 25.1ms vs sidecar 41.2ms | `bench_mind_worker.py` |
| One-shot doc CLI (`cortex-doc embed`) | 1.6s vs 4.2s wall (2.6×), cùng JSON shape | P02 |
| Cold start model | 0.46s vs 3.82s (8.3×) | P01 |
| GLiNER NER | 11.39 vs 6.02 texts/s (1.89×) và **848/848 contract khớp fp32** | P03 |
| Ingest throughput batch 8 | 1.00× (không đổi) | P01/P05 |

Kết luận chiến lược: **ort là câu chuyện của query/MCP/NER, không phải của ingest**.

## 3. Coverage checklist — 14/14 dòng inventory, owner chốt

| # | Luồng | Owner chốt sau spike | State |
|---|---|---|---|
| 1 | Ingest-sync, shared-CLI parsers | **Python child, vĩnh viễn** (mục 1) | giữ nguyên |
| 2 | Ingest-sync, legacy analyzers | **Python child, vĩnh viễn** | giữ nguyên |
| 2b | Ingest-sync cobol | **Python child, vĩnh viễn** | giữ nguyên |
| 3 | Orchestrator embedding pass | vẫn delegate sang child; `Embedder` trait có sẵn nếu reopen | giữ nguyên |
| 4 | Message lane `_mess` (+ `_hash_vector`) | **Python child**; hash fallback KHÔNG port (mục 1.3) | giữ nguyên |
| 5 | Rust analyzers | boundary "analyzer chỉ ghi graph" — không đổi | boundary |
| 6 | Query-code `semantic_search` | `cortex-embed::OnnxEmbedder` (mean+normalize, 8194) — **chưa cắm**, chờ phase-04 | P04 OPEN |
| 7 | Query-code `explore_graph` | như #6 (seeds thật vào `SeedInputs.qdrant`) — chờ phase-04 | P04 OPEN |
| 8 | Query-mind (bge-m3) | seam đã swap: `python` mặc định \| `onnx` sau flag; flip blocked | **đã cắm** |
| 9 | Ingest-doc `cortex-doc embed` | seam đã swap, giống #8 | **đã cắm** |
| 10 | Query-doc CLI `graphrag_query_langextract.py` | **giữ Python** — là CLI Python, không có seam Rust nào để cắm; env chain đã khớp `ModelSpec` Doc plane | chốt |
| 11 | Livingdoc | out of scope; **risk ghi nhận**: bge-m3 ghi vào code collection, `filter_collections_for_vector` chỉ match theo size 1024 → dễ cross-plane. Cần fixture behaviour ở P04 | risk open |
| 12 | NER GLiNER doc ingest | **Python sidecar giữ nguyên**; fp32 ONNX = GO về số liệu, nhưng decode Rust (6 input tensor) chưa port | P03 GO-conditional |
| 13 | NER GLiNER `cortex-doc` | như #12 | P03 GO-conditional |
| 14 | Contract/parity harness | đã dùng lại cho P03 (`{entity,type,score,span}` + tolerance 1e-3) | xong |

## 4. Docs + vận hành (đã làm)

- `docs/cutover-runbook.md`: thêm `CORTEX_EMBED_BACKEND`, `ORT_DYLIB_PATH`,
  `CORTEX_EMBED_ORT_THREADS` vào bảng env + **trạng thái chưa flip** với 2 lý do bằng số.
- `dev doctor`: check mới `embedding backend` (`scripts/mcp-lifecycle.py`
  `doctor_embedding_backend()`), in ra backend đang dùng, tên dylib ORT và kích thước
  2 graph. Thiếu artifact chỉ là **fail khi operator explicitly bật `onnx`**, còn mặc
  định là thông tin (required=False) — không làm doctor đỏ cho người không dùng spike.
  Đã test 4 nhánh: unset / python / onnx-đủ-artifact / onnx-thiếu-artifact.
- `make build` provision ORT; `make embed-artifacts` tạo graph; `make embed-parity`
  chạy lại toàn bộ gate cosine/token-id.
- `requirements.txt`: `onnx>=1.17`, `onnxscript>=0.7` (export path của torch 2.13).

## 5. Việc CHƯA làm (không được tính là xong)

1. **Dogfood add-on (`CORTEX_EMBED_BACKEND=onnx` một kỳ sync + MCP đầy đủ trên stock)
   = CHƯA chạy.** Đã chạy ở mức gần nhất: replay toàn bộ mind contract qua MCP server
   (`compare_mind.py`, 15 fail do drift 1e-7 + latency). Một kỳ `dev sync doc/code` thật
   với onnx bị chặn bởi chính 2 phát hiện P02 — chạy rồi thì kết quả cũng là fail có dự
   báo trước. Xong phase-04 thì chạy.
2. `dev doctor` chưa được gọi lại qua binary `dev` thật (chỉ test hàm trực tiếp).
3. Phase-04 (un-empty lane #6/#7 + re-baseline) và Rust GLiNER decoder (#12/#13) còn mở.
4. int8 cho jina-v3/bge-m3 chưa đo (GLiNER int8 đã đo và NO-GO).

## 6. Sửa một test đã đỏ từ trước (không phải do spike)

`tests/test_dev_lifecycle_commands.py::test_every_make_lifecycle_target_is_exposed_by_dev`
đã fail **từ trước phiên này** (tái hiện trên `Makefile` của `f6b5cdc`): nó parse dòng
đầu của `.PHONY` bằng `split()` nên token nối dòng `'\'` bị đếm như một target. Đã sửa
1 dòng (lọc `'\'`), `tests/test_make_lifecycle.py` + file này giờ 64 passed.
