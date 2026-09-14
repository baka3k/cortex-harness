# Phase 03 — GLiNER ONNX: **GO cho fp32 / NO-GO cho int8** (decision record)

Đo 2026-09-14, macOS arm64, CPU. Model `urchade/gliner_large-v2.1`
(deberta-v3-large backbone), gliner **0.2.28**, 8 label mặc định của
`entity_extractors.DEFAULT_GLINER_LABELS`, threshold **0.35** (dev wiring; code
default 0.3), paragraph bound 500 ký tự (dev `MAX_PARAGRAPH_CHARS`).

## 1. Premise cũ của plan là SAI — và đã được sửa bằng bằng chứng

Plan gốc (phase-13/14) chốt "GLiNER stays Python sidecar" vì "không có ONNX export
chính thức". gliner 0.2.28 có `GLiNER.export_to_onnx(save_dir, quantize=False,
opset=19)` (`gliner/model.py:1706`) và `from_pretrained(load_onnx_model=True)` với 6
class ORT (`gliner/onnx/model.py`, `UniEncoderSpanORTModel:114`) — kiến trúc đúng là
uni-encoder span. Cảnh báo int8-mất-accuracy cho DeBERTa cũng có thật
(`model.py:562-563`). Toàn bộ đã được kiểm chứng, và giờ có số.

## 2. Kết quả so khớp contract

Corpus 200 paragraph **có thực thể** (138/200 doc chứa entity, **848 entity** reference).
Cả hai phía đi qua CÙNG entry production `entity_extractors.extract_entities_gliner`
(bao gồm cả span backfill `_normalize_entities`), chỉ model instance khác nhau ⇒
đo đúng phần graph/decode, không lẫn preprocessing.

| Backend | Entity tìm thấy | exact (name+type+span+order, score ≤1e-3) | missing | extra | max Δscore | thứ tự list đúng |
|---|---|---|---|---|---|---|
| torch (reference) | 848 | — | — | — | — | — |
| **ORT fp32** (1784 MB) | 848 | **848/848 = 1.000** | 0 | 0 | **1.3e-05** | **200/200** |
| **ORT int8** (653 MB) | **106** | **0/848 = 0.000** | **762** | 20 | **0.613** | 61/200 |

int8 **mất 87.5% số entity** (848→106), Δscore tới 0.61. Đây chính xác là điều
gliner cảnh báo bằng lời, nay có số: **int8 NO-GO, không bàn thêm**. fp32 giữ
nguyên contract ở mức Δscore 1e-5 — tốt hơn nhiều gate 1e-3.

## 3. Latency

| Đường | p50 | p95 | throughput |
|---|---|---|---|
| torch sidecar (hiện tại) | 157.1ms | 247.1ms | 6.02 texts/s |
| ORT fp32 | **76.3ms** | 170.8ms | **11.39 texts/s (1.89×)** |
| ORT int8 | 68.7ms | 171.2ms | 11.77 texts/s |

int8 nhanh hơn fp32 đúng ~10% p50 nhưng đổi lại mất 87.5% recall ⇒ vô nghĩa.
fp32 ONNX đã nhanh hơn torch ~2× mà **không** cần GPU.

## 4. Device behavior thay đổi (phải ghi nhận)

Python GLiNER hiện **không có device plumbing** (`gliner/model.py:226-238` tự chọn
cuda/cpu). ORT trong kế hoạch này chạy **CPU**. Nghĩa là trên máy có CUDA, đường
Python hôm nay có thể đang chạy GPU còn đường ONNX thì không — so sánh hiệu năng
phải nói rõ điều đó. Nếu cần GPU thì đó là việc chọn execution provider, không phải
port.

## 5. Decision record

**GO (có điều kiện) cho port fp32. NO-GO cho int8. GLiNER sidecar VẪN LÀM VIỆC
cho tới khi port xong decode Rust.**

Cơ sở: contract parity fp32 hoàn hảo (848/848, Δ 1.3e-05) + nhanh hơn 1.89×.

Điều kiện / việc còn lại (chưa làm trong phase này, ghi rõ để không đếm là đã xong):
1. **Chưa port decode sang Rust.** Số học span-decoding phải tái tạo gồm
   **6 input tensor**: `input_ids`, `attention_mask`, `words_mask`, `text_lengths`,
   `span_idx`, `span_mask` (`gliner/onnx/model.py:139-152`). Tức là phải port cả phần
   word-token alignment + span pairing + threshold + top-K + NMS-ish của gliner,
   không chỉ "chạy graph". Đây là unit work riêng, ước tính tương đương một
   mini-phase (kèm fixture `{entity,type,score,span}` riêng).
2. **Graph phải export bằng Python** (giống jina-v3): `export_to_onnx(opset=19)`
   chạy trong 5.1s và tái lập được, nên chi phí thấp; nhưng nó là build-time
   dependency của artifact, không phải runtime.
3. Quirk taxonomy SSI→CRYPTO (pinned phase-13) nằm ở `_normalize_entities`, dùng
   chung cả hai phía trong phép đo này ⇒ **chưa chứng minh** được Rust port sẽ khớp
   khi tự viết lại hàm đó. Phải fixture hoá riêng.
4. Export script: `scripts/rust_parity/gliner_onnx_eval.py` (in + so + latency).
   Chưa có `make` target cho GLiNER vì chưa có quyết địnhport.

## 6. Hai bẫy đã bắt được trong phase này (nên đọc trước khi port)

1. **Corpus rỗng tạo bằng chứng giả.** Lần chạy đầu dùng `wiki/` (toàn tài liệu API):
   cả torch lẫn ORT đều trả **0 entity**, `exact_rate` in ra `0.0`/vacuous và mọi
   implementation đều "khớp". Script giờ abort nếu reference < 100 entity
   (`MIN_ENTITIES`). Bài học: gate so khớp phải có guard độ lớn mẫu.
2. **`from_pretrained(load_onnx_model=True, file_name=...)` không tồn tại** — tham số
   đúng là `onnx_model_file`, và vì signature có `**model_kwargs` nên tên sai bị
   **nuột im lặng**, load luôn `model.onnx` (fp32) nằm cạnh `model_quantized.onnx`.
   Kết quả "int8" lần đầu là fp32 đo lần hai: rate 1.0 và latency gần bằng fp32.
   Script giờ chứng minh bằng probe fingerprint (`fp32_probe` ≠ `int8_probe` trong
   JSON) thay vì tin vào kwarg.
