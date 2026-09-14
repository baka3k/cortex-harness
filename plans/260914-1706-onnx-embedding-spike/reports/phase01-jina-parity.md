# Phase 01 — `cortex-embed` + jina-v3 ort: KẾT QUẢ **PASS**

Ngày đo: 2026-09-14. Máy: macOS arm64 (Apple Silicon), 25.7GB RAM, CPU-only,
`ORT_INTRA_THREADS=4` / `torch.set_num_threads(4)` cho cả hai phía.
HEAD lúc bắt đầu: `f6b5cdc` (branch `feat/change-db`).

## 1. Gates

| Gate | Yêu cầu | Đo được | verdict |
|---|---|---|---|
| 1a — graph export đúng trước khi sang Rust | cosine ≥ 0.9999, graph chạy bằng `onnxruntime` Python, chưa đụng Rust | **1.000000** trên cả 8 case (gồm text 992 token, tiếng Việt, emoji) | **PASS** |
| Graph không còn `task_id` | inputs chỉ `input_ids` + `attention_mask` | `inputs=['input_ids','attention_mask'] outputs=['last_hidden_state']` | **PASS** |
| Token-id | diff = 0, corpus thật | **520/520 checked, drift = 0** (ingest 500 + query 20, 0 case bị cắt) | **PASS** |
| fp32 cosine | ≥ 0.999 từng cặp, corpus ≥ 500 texts | cases=520, **mean 1.0000001**, **worst 0.9999995**, **0 dưới gate** | **PASS** (dư 5 đơn vị thập phân) |
| Mode generic 512/không-normalize | unit test tự chứa, không cần model | `pooling::` + `model::` tests, 20/20 pass trong `cargo test` thường | **PASS** |
| CI-safe | `cargo test`/clippy không cần weights | 3 test model `#[ignore]`; `make rust-check` (clippy `-D warnings` + `cargo test --workspace`) exit 0 | **PASS** |
| Benchmark texts/s CPU vs Python | có số liệu cho P05 | xem mục 4 | **PASS** (đã đo) |
| int8 (tự quantize) | cosine ≥ 0.999 **và** recall@10 ≥ 0.99 | **CHƯA chạy** | **OPEN** — xem mục 6 |

Worst-case cosine 0.9999995 nghĩa là gate 0.999 không phải biên giới thật của phương
án này: khoảng cách Rust↔Python nhỏ hơn gate ~4 bậc độ lớn. `mean > 1` là noise do
vector trong fixture lưu 8 chữ số thập phân, không phải lỗi số học.

## 2. Crate

`rust/crates/cortex-embed` (edition 2024, `publish = false`, `unsafe_code = "deny"` —
theo convention workspace):

| Module | Vai trò |
|---|---|
| `backend.rs` | trait `Embedder` + chọn backend qua `CORTEX_EMBED_BACKEND=python\|onnx` (default `python`) |
| `model.rs` | `ModelSpec` (pooling/normalize/max_length/max_chars/strip/dimension), chuỗi env 2 plane, HF snapshot + graph discovery, `python_strip` |
| `onnx.rs` | `OnnxEmbedder` (`ort` load-dynamic + `tokenizers`), `SessionConfig`, `tokenize_ids` |
| `pooling.rs` | mean/CLS pool, L2-normalize, chunk-mean — phần test được không cần weight |
| `sidecar.rs` | `SidecarEmbedder`: worker NDJSON persistent (`scripts/rust_mcp/embed_worker.py`), respawn-once, timeout 120s |
| `error.rs` | `EmbedError` cùng shape `ProviderError` của cortex-doc |

Deviation so với design #1 trong plan (có chủ đích, ghi lại):

1. `embed(texts)` không nhận `opts` theo lời gọi — mọi lựa chọn số học nằm trong
   `ModelSpec` lúc dựng embedder. Lý do: pooling/normalize/max_length là thuộc tính
   **model**, không thuộc tính **call site**; để ở call site là chỗ dễ sinh lỗi im lặng.
2. `dimension() -> Option<usize>` (plan ghi `-> usize`): sidecar Python chỉ echo
   chiều sau response đầu, nên `usize` buộc phải nói dối hoặc load model sớm.
3. Thêm field `strip` trên spec: lane ingest (SentenceTransformer) strip text, lane
   `embed_query` (AutoModel `.encode`) thì không — xem C8/findings.

## 3. Số học đã chốt (khác bản plan gốc)

jina-v3 có **một** đường duy nhất: `XLMRobertaLoRA.encode` mean-pool fp32 theo
attention mask + L2-normalize, token max **8194**, **LoRA adapter TẮT**
(`adapter_mask` không bao giờ được truyền). `mean-pool 512 không normalize` chỉ là
nhánh fallback cho model thay thế không có `.encode`. Chi tiết + bằng chứng:
[`../findings.md`](../findings.md) C1.

## 4. Benchmark (500 text ingest thật, cùng corpus với fixture)

| batch | torch Python | ort Rust (release) | Rust/Python |
|---|---|---|---|
| **8** (= `EMBED_BATCH_SIZE` mặc định) | 38.1 texts/s | **38.1 texts/s** | **1.00×** |
| 32 | 42.7 | 31.9–32.8 | 0.75× |
| 128 | 39.8 | 25.7–25.8 | 0.65× |
| single-text p50 | 43.5 ms | **28.5–29.1 ms** | **1.5× nhanh hơn** |
| single-text p95 | 61.7 ms | **40.0–42.0 ms** | **1.5× nhanh hơn** |
| cold start (load model) | 3.82 s | **0.46 s** | **8.3× nhanh hơn** |

Đọc số: ở đúng cấu hình production (batch 8) ort **ngang** torch; ort scale kém hơn
khi tăng batch (intra-op saturate), nên nếu P05 muốn ăn theo batch lớn thì phải tìm
hiểu thêm, còn hiện tại không phải đường đi. Ngược đời với giả định "torch thắng trên
CPU": **query latency và cold-start Rust thắng rõ** — cold-start 8.3× là lợi ích trực
tiếp của lane one-shot `cortex-doc` (#9) và MCP restart. Debug build Rust thấp hơn
release đáng kể (23 vs 38 texts/s) nên mọi số benchmark phải lấy ở `--release`.

Số đo Rust nằm trong `#[ignore] test throughput_probe` (`tests/embed_golden.rs`);
phía Python: `scripts/rust_parity/bench_embed_python.py`.

## 5. Artifact

| Thứ | Giá trị |
|---|---|
| Graph | `.cache/embed/jina-v3-onnx-fp32/model.onnx` (gitignored qua `.cache/*`) |
| sha256 graph / data | `be2de66d2b4e087d…` / `a0497c9e634b6faa…` (data 2,233,278,464 B) |
| opset / exporter | 18 / `torch.onnx.export` dynamo=True, torch 2.13.0 |
| ORT | 1.29.0, provision bởi `scripts/ensure_ort.py` → `.cache/ort/1.29.0/` |
| Golden fixture | `rust/crates/cortex-embed/tests/fixtures/jina_golden.json` (6.30 MB, 520 cases, sha256 `84be74c0…`) |
| Crate deps | `ort =2.0.0-rc.13` (load-dynamic; **không có bản stable**), `tokenizers 0.23.2` |
| Python deps mới | `onnx>=1.17` (quantize/inspect), `onnxscript` (dynamo exporter) |

Export bắt buộc phải opset **≥18**: torch 2.13 dynamo phát hành `Split` với attribute
`num_outputs` (chỉ có từ opset 18); với opset 17 ONNX Runtime từ chối graph dạng
`InvalidGraph`. Legacy TorchScript exporter (`dynamo=False`) không phải phương án dự
phòng: nó crash trong `_C._jit_pass_peephole` trên chính model này.

## 6. Còn mở

1. **int8 chưa đo.** Không có artifact int8 chính thức cho jina-v3 (chỉ fp32 + fp16),
   nên gate kép cosine + recall@10 phải chạy trên graph do ta `quantize_dynamic`.
   `onnx` đã cài nên bước này chỉ còn là script + corpus query. fp16 cũng là một điểm
   giữa chưa được plan xét (artifact fp16 có sẵn, mà fp16-vs-fp32 trên cùng input sẽ
   cho biết luôn trần của quantization).
2. Fixture 6.3MB là file commit lớn nhất repo hiện có — nếu thấy nặng, giảm
   `--limit` hoặc tách vector sang file nén; gate vẫn chạy được vì test đọc file lúc
   run (không `include_str!`).
3. `SidecarEmbedder` chưa được bất kỳ crate nào gọi — nó chỉ có tác dụng khi P02 swap
   seam `cortex-mcp`/`cortex-doc`.

## 7. Bẫy đã ghi để phase sau khỏi tái phạm

- `tokenizers::TruncationParams::default().max_length == 512`: không set tường minh
  8194/8192 là tái hiện đúng cái-myth-512 thành bug thật.
- `tokenizer.json` của 2 model đều `truncation: null`, `padding: null`, và
  `tokenizer_config.json` không có `pad_token_id` → pad phải resolve `<pad>` qua vocab
  (= 1), không hardcode.
- Python `str.strip()` cắt thêm U+001C..U+001F mà `char::is_whitespace()` không cắt (C8).
- Hai `OnnxEmbedder` cùng lúc = hai session 2.2GB (test dựng 2 lane); trên máy 25.7GB
  thì ổn nhưng phải nhớ khi swap vào MCP server.
