# Phase 01 — Crate `cortex-embed` + jina-v3 ort (2 đường số học code plane)

## Scope

1. Crate mới `rust/crates/cortex-embed`:
   - Deps: `ort` (pin version, feature CPU), `tokenizers`, `ndarray`, `serde`.
   - Trait `Embedder`: `embed(&[String]) -> Vec<Vec<f32>>`, `dimension() -> usize`,
     `backend_name() -> &str`. Backend `OnnxEmbedder` + `SidecarEmbedder` (worker Python
     hiện tại, dùng cho A/B).
   - Session/config: model dir, threads (`ORT inter/inner threads`), opt level, env
     `CORTEX_EMBED_BACKEND`, `EMBED_DEVICE` (cpu-only trong phase này, giá trị khác → log
     warn + cpu).
2. **jina-v3 ONNX — TỰ EXPORT** (xem `findings.md` C3: artifact chính thức trên HF dùng
   KHÔNG được vì graph đòi `task_id` scalar 0..4, không biểu diễn được "adapter tắt").
   - `scripts/rust_parity/export_jina_onnx.py`: load `XLMRobertaLoRA` từ snapshot đã cache
     (`trust_remote_code`, code nằm ở `~/.cache/huggingface/modules/transformers_modules`),
     bỏ qua LoRA (không truyền `adapter_mask` — khớp đúng production), export module
     `.roberta` (`XLMRobertaFlashModel`, rotary, **không** phải `XLMRobertaModel` stock)
     bằng `torch.onnx.export`, dynamic axes `[batch, sequence]`, opset **18** (torch
     dynamo phát hành `Split` với `num_outputs`, chỉ hợp lệ từ opset 18 — opset 17 bị
     ORT từ chối `InvalidGraph`; `dynamo=False` không dùng được vì crash
     `_jit_pass_peephole`), fp32, external data. Artifact + checksum ghi vào
     `.cache/embed/jina-v3-onnx-fp32/`.
   - **Gate 1a (trước khi sang Rust)**: chạy graph vừa export bằng `onnxruntime` Python
     (1.29, CPU, threads cố định) so với reference torch (`embed_runtime`) — cosine phải
     ≥ 0.9999. Không pass thì lỗi ở export, không phải ở Rust.
   - Tokenizer: `tokenizer.json` đã được `tokenizers` 0.23.2 tái tạo **0/20 sai khác**
     (findings C4) ⇒ gate số 0 coi như đạt, chỉ cần giữ trong harness. KHÔNG cần xử lý
     `fix_mistral_regex` (tokenizer thật là `XLMRobertaTokenizerFast`). Bắt buộc set
     truncation = 8194 tường minh (`TruncationParams::default()` = 512).
3. Số học: jina-v3 có **MỘT** đường (findings C1) — mean-pool fp32 theo attention mask +
   L2-normalize, max token 8194, char-bound 4000/hard 16000 từ `documents_from_payloads`,
   LoRA tắt. Crate vẫn expose mode generic `mean-pool/không-normalize/512` cho model thế
   bằng `CODE_EMBEDDING_MODEL_PATH` (khi model không có `.encode`) + mode `--chunk-embed`
   (mean của chunk vectors, KHÔNG re-normalize ⇒ norm < 1), nhưng không cần corpus fixture
   riêng cho jina-v3 ở mode đó.
4. Golden parity harness `scripts/rust_parity/embed_parity.py`:
   - Corpus: texts thật từ stock (code documents theo format `documents_from_payloads`
     `primary_vector_sync.py:99-155`, có secret-redaction `:69-78`) + query texts thật
     cho #6/#7 — cùng một đường số học (findings C1), chỉ khác char-bound.
   - Python dump vectors → JSON fixture → Rust so cosine từng cặp ≥ 0.999 + token-id diff 0.
   - Fixture fp32 trước; int8 chạy sau trên cùng fixture, ghi cosine + recall@10.

## Touchpoints inventory liên quan

#1, #2, #2b (cobol max_chars=800 — chỉ ảnh hưởng corpus, không ảnh hưởng embedder), #6/#7
(query text dùng đường legacy — corpus thêm query texts thật).

## Gates

- [ ] Gate 1a: graph jina tự export đạt cosine ≥ 0.9999 vs reference torch, đo bằng
      `onnxruntime` Python trên CPU (chưa đụng Rust).
- [ ] Token-id diff = 0 trên toàn corpus (đã pass 20/20 ở probe; phải reopen trên corpus
      thật ≥ 500 texts).
- [ ] fp32 cosine ≥ 0.999 từng cặp, corpus ≥ 500 texts, đường jina-v3 duy nhất
      (mean-pool + normalize).
- [ ] Mode generic (mean-pool/không-normalize/512) có unit test số học tự chứa trong
      crate (không cần model), để P04/P05 dùng nếu `CODE_EMBEDDING_MODEL_PATH` đổi model.
- [ ] int8 (tự `quantize_dynamic`): cosine ≥ 0.999 AND recall@10 ≥ 0.99 trên bộ query
      fixture (nếu fail → ghi số liệu, giữ fp32, decision record).
- [ ] `cargo test -p cortex-embed` + clippy `-D warnings` chạy được **không cần weights**
      (fixture golden commit, test nặng `#[ignore]`).
- [ ] Benchmark sơ bộ texts/s CPU vs Python (ghim CPU cả 2 bên) — số liệu cho P05.

**Kết quả:** reports/phase01-jina-parity.md
