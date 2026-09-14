# Research corrections (2026-09-14) — measured before any code was written

Bằng chứng: đo trực tiếp trên máy dev (macOS arm64, `.venv` pin sẵn), HEAD `f6b5cdc`.
File này là phụ lục của `plan.md`; khi mâu thuẫn, file này thắng vì nó là số liệu.

## C1. Code plane chỉ có MỘT đường số học (refutes decision #2, phase-01 mục 3)

`mean-pool max_length=512 không normalize` mà inventory dòng #2 mô tả là **nhánh chết**
với jina-v3: `AutoModel` resolve thành `XLMRobertaLoRA` (auto_map trong `config.json`),
class này **có** `.encode()`, nên `if hasattr(model, "encode")` luôn đúng trước khi chạm
`mean_pool`/512.

- `~/.cache/huggingface/modules/transformers_modules/jinaai/xlm-roberta-flash-implementation/
  bd55a5ec/modeling_xlm_roberta.py:486-500` — `encode(..., batch_size=32,
  normalize_embeddings=True, task=None)`; `:566` `max_length = tokenizer.model_max_length`
  (= **8194**); `:653-661` `mean_pooling` nhân mask `.float()` → cộng dồn fp32.
- `code-tiny/tools/common/embed_runtime.py:169-181` (`encode_texts` → `model.encode`) và
  `:183-199` (mean-pool 512, **chỉ chạy cho model không có `.encode`**).
- `custom_st.py:130-143`: `task=None` ⇒ `adapter_mask` **không được truyền** ⇒ LoRA TẮT.
  Không cuộc gọi nào trong repo truyền `task`/`prompt` (`default_prompt_name: null`).

Đo trên CPU, 5 text thật: `cosine(ST-lane, AutoModel-lane) = 1.000000`, norm cả hai = 1.0,
`st.dtype = torch.float32` (ST 5.7 không dùng bf16 trên CPU), batch-order lệch ≤ 1.2e-7.

**Hệ quả P01**: một đồ thị duy nhất = mean-pool fp32 + L2-normalize, **không adapter**.
still keep a generic `mean-pool/no-normalize/max-512` mode in the crate for a custom
`CODE_EMBEDDING_MODEL_PATH` model lacking `.encode` (dòng #2 thật sự áp dụng ở đó),
nhưng không cần corpus fixture riêng cho jina-v3.

## C2. bge-m3 là CLS-pool + CÓ normalize (refutes inventory dòng #8/#9/#10)

Snapshot `models--BAAI--bge-m3/snapshots/5617a9f6/`:
`modules.json` = Transformer + Pooling + **`2_Normalize`**;
`1_Pooling/config.json` = `pooling_mode_cls_token: true`; `sentence_bert_config.json`
`max_seq_length: 8192`; `config.json` `torch_dtype: float32`.

**Hệ quả P02**: port "encode raw không normalize" như plan sẽ sinh sai vector cho toàn
lane mind/doc. Phải CLS(index 0) + L2-normalize, max_len 8192.

## C3. Artifact ONNX: jina PHẢI tự export, bge-m3 dùng chính thức

| Model | Artifact chính thức | Dùng được không |
|---|---|---|
| jina-v3 | `onnx/model.onnx` (opset 16, producer pytorch 2.2.1) + `model.onnx_data` 2.29GB; `onnx/model_fp16.onnx`; **không int8** | **KHÔNG** — input bắt buộc `task_id: int64 scalar` được wire vào `/roberta/embeddings/token_type_embeddings/Gather_1` (bảng 5 dòng, dù `type_vocab_size=1`), tức exporter mượn type-vocab làm task-embedding. Chỉ nhận 0..4 ⇒ không biểu diễn được "adapter tắt". Sweep đo thật: cos vs reference = 0.8949/0.8723/**0.9418**/0.8828/0.8918, task_id=5 ⇒ ORT báo lỗi out-of-range. Lỗi tái lập trên `onnxruntime` Python 1.29 ⇒ không phải bug Rust. Output thứ 2 (`13049`, norm ~9.5) cos ≈ 0 với reference ⇒ không phải sentence embedding của lane này. |
| bge-m3 | `onnx/model.onnx` (opset 11) + `model.onnx_data` 2.27GB | **CÓ** — inputs đúng `input_ids`+`attention_mask`, outputs `token_embeddings` + `sentence_embedding`. Vẫn phải verify `sentence_embedding` khớp Python (CLS+normalize) ở gate P02. |

⇒ P01 cần bước **tự export**: load `XLMRobertaLoRA` bằng code cached trong `~/.cache/huggingface/modules`,
gỡ/tắt LoRA parametrization, export module `.roberta` (`XLMRobertaFlashModel`, rotary +
`rotary_emb_base=20000`, **không** phải `XLMRobertaModel` stock) qua `torch.onnx.export`
(dynamic axes batch/seq) → graph sạch `input_ids,attention_mask → last_hidden_state`.
`optimum` không cần cho việc này.

## C4. Tokenizer: không có Mistral, Gate #0 ĐÃ PASS

- jina tokenizer thật = `XLMRobertaTokenizerFast`, `model_max_length=8194`, pad=1/bos=0/eos=2.
  bge = cùng class, `model_max_length=8192`. `fix_mistral_regex=True` (`python_analyzer.py:952`)
  là no-op với XLM-R ⇒ premise trong decision #3 không áp dụng cho model hiện tại.
- Rust `tokenizers` **0.23.2** load thẳng `tokenizer.json`: **0/20 token-id mismatch** với
  `AutoTokenizer` trên corpus khắc (tiếng Việt dấu, CJK, emoji, NFKC, whitespace, rỗng, 9000 ký tự).
- **Bẫy**: `tokenizers::TruncationParams::default().max_length == 512`. Phải set tường minh
  8194/8192, nếu không Rust sẽ tái hiện chính xác cái-myth-512 thành bug thật.

## C5. ort: pre-release + load-dynamic đã chứng minh

- crates.io `ort` **không có stable**: chỉ `2.0.0-rc.*`, mới nhất `2.0.0-rc.13` (2026-07-28).
- `ort = "=2.0.0-rc.13", default-features=false, features=["std","load-dynamic","ndarray"]`
  dlopen `libonnxruntime.1.29.0.dylib` có sẵn trong `.venv` → load + run graph ONNX thật OK
  trên arm64, **không download, không compile ORT**.
- API notes: `ort::init_from(path)?.commit() -> bool`; `Session::builder()?.commit_from_file()`;
  `session.run(ort::inputs![...])`; `outputs[i].try_extract_tensor::<f32>() -> (&Shape, &[f32])`;
  `Input/Output.name()`/`.dtype()` là **method**, field `inputs/outputs` private;
  scalar tensor cần feature `ndarray` (`Array0::from_elem(Ix0(), v)`).

## C6. Hạ tầng/deploy (plan chưa nêu)

- CI `lifecycle-macos.yml` job `rust` chạy `make rust-check` = `clippy -D warnings` +
  `cargo test --workspace` ⇒ test Rust không được cần weights. Fixture golden commit tại
  `rust/crates/cortex-embed/tests/fixtures/`, test nặng `#[ignore]`. Provisioning (ORT dylib,
  artifact) đưa vào `make build` theo quyết định người dùng.
- Convention report: `plans/<plan>/reports/` (không có `reports/` gốc).
- Model artifacts ~4.6GB: tái dùng `~/.cache/huggingface` cho bản chính thức; bản tự export
  vào `.cache/embed/` (đã `.gitignore` qua `.cache/*`).
- Query LRU cache Python key = `(model_name, text)` — **không có device**
  (`embed_runtime.py:215,227`). Phase-04 ghi "cache key theo model+device+text" ⇒ sẽ lệch
  Python. Sửa thành key không device nếu mục tiêu là parity.
- Line drift: `CodeEmbedder` 904→**947**; `graphrag_ingest` 600→**596** (embed `:607`,
  payload `:617-624`); `tools_explore` lane rỗng ở **`:605-609`** (khai báo `:19-21`);
  `embed_runtime` LRU **201-233**; `fusion.rs` `SeedInputs` **152-158**;
  `MAX_PARAGRAPH_CHARS` không phải constant Python (argparse default 1200 ở
  `graphrag_ingest_langextract.py:1097`, `dev.py:1226` truyền 500,
  `doctiny_parity.py:55-56` pin 1200/40).
- Rust hiện **không có trait `Embedder` nào**: seam thật là struct dữ liệu
  `SeedInputs{qdrant, keyword}` (`fusion.rs:152-158`) + `results.insert("results", json!([]))`
  (`tools_semantic.rs:332-338`). `cortex-doc::encode()` lấy dimension từ JSON sidecar
  (`unwrap_or(0)`), **không assert ở production** — chỉ `#[ignore]` test `:115` assert 1024.
- `_hash_vector` (`message_scan.py:393-401`) cần Python `str.lower()` (Unicode full
  lowercase) + SHA-1 hex → **bigint 160-bit** `% 1024`; Rust `to_lowercase()` khác bảng
  (`İ`, `Σ`, `ß`) ⇒ phải replicate bảng Python, không phải `str::to_lowercase`.

## C7. GLiNER ONNX: premise ĐÚNG (đã verify trong `.venv`)

`gliner/model.py:1706` `export_to_onnx(save_dir, onnx_filename="model.onnx",
quantized_filename="model_quantized.onnx", quantize=False, opset=19)` ✓ (plan nói đúng).
6 class ORT tại `gliner/onnx/model.py` (`UniEncoderSpanORTModel` `:114` ✓).
Cảnh báo int8-deberta có thật, ở docstring `quantize()` `model.py:562-563`: "Stock
DeBERTa-based models lose accuracy with int8; use this with models fine-tuned with
quantization-aware training (QAT)". int8 đi qua `onnxruntime.quantization.quantize_dynamic`
(cần pkg `onnx`, đã cài 1.22.0).

## C8. `str.strip()` của Python rộng hơn bảng `White_Space` Unicode (hole parity thật, đã bắt được)

Lane ingest đi qua `SentenceTransformer` → `Transformer.tokenize` gọi `str.strip()`.
Bảng `Py_UNICODE_ISSPACE` cắt thêm **U+001C..U+001F** (file/group/record/unit separator),
trong khi `char::is_whitespace()` của Rust dùng bảng Unicode `White_Space` **không** chứa
4 ký tự đó. Kiểm chứng bằng chứng nghiệm (`.venv/bin/python`):
`'\x1c'.isspace() == True`, `'\x1cpayload\x1d\x1e\x1f'.strip() == 'payload'`;
phía Rust `'\u{1c}'.is_whitespace() == false`.

⇒ `str::trim()` thuần sẽ cho token-id khác Python trên text có các ký tự này ở biên.
Crate cung cấp `python_strip()`/`is_python_space()` (`src/model.rs`) và dùng nó trong
`ModelSpec::prepare_text`; test `strip_matches_python_on_file_separator_chars` chốt lại.
Cùng loại rủi ro này áp dụng cho `_hash_vector` (mục C6) — chỗ đó là `str.lower()`,
không phải strip, nhưng cũng phải so bảng chứ không tin `to_lowercase()`.
