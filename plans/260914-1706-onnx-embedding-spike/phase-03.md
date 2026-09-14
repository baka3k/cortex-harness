# Phase 03 — GLiNER ONNX evaluation (go/no-go bằng số)

## Scope

1. **Export**: dùng gliner **0.2.28** trong `.venv` — `GLiNER.export_to_onnx(save_dir,
   quantize=False, opset=19)` fp32 trước; int8 (`quantize=True`) chạy sau trên cùng
   export pipeline. Model `urchade/gliner_large-v2.1` (uni-encoder span, backbone
   deberta-v3-large — chính gliner cảnh báo int8 mất accuracy).
2. **Port decode Rust** trong `cortex-embed` (hoặc crate con `cortex-embed-ner`):
   - Kiến trúc `UniEncoderSpanORTModel` (`gliner/onnx/model.py:114`).
   - So ở mức **contract** `{entity, type, score, span:[start,end]}` — không so tensor:
     threshold logic, span backfill `_normalize_entities` (`entity_extractors.py:24-64`),
     taxonomy quirk SSI→CRYPTO (pinned phase-13), batch method selection order
     (`entity_extractors.py:304-320`), labels default `DEFAULT_GLINER_LABELS`.
3. **Parity harness**: corpus = stock doc thật (đủ danh mục entity đa dạng); chạy
   Python (`extract_entities_gliner`) vs Rust-ort; so exact entity/type/span, score
   tolerance 1e-3, list order exact. Tái dùng pattern GATE_GLINER của
   `scripts/rust_mcp/compare_mind.py:278-303`.
4. **Decision record** (deliverable bắt buộc, kể cả no-go):
   - fp32 pass? int8 pass? latency batch-1 (dev dùng `--no-batch`, batch 1) vs batch 8?
   - Nếu pass → kế hoạch thay `GlinerProvider` (`cortex-doc/src/providers.rs:95-215`)
     + xóa sidecar Python; nếu fail → giữ sidecar, ghi lý do số liệu vào plan
     rust-full-migration (cập nhật premise "không có ONNX export" đã lỗi thời).

## Touchpoints inventory liên quan

#12, #13, #14. Lưu ý: GLiNER là ingest-time only (mind tools đọc entity từ graph store,
`cortex-mcp/src/mind/mod.rs:13-15`) — không có query-time NER cần port.

## Điểm cần xử lý khác Python-sidecar

GLiNER hiện **không có device plumbing** (tự chọn cuda/cpu — `gliner/model.py:226-238`).
Rust ort chạy cpu — cần ghi rõ trong decision record rằng behavior device thay đổi
(cuda không còn tự động); nếu cần GPU thì là follow-up execution provider.

## Gates

- [ ] fp32: contract parity ≥ 99% exact-match entity/span trên corpus ≥ 200 paragraphs
      (mọi mismatch liệt kê + phân loại nguyên nhân).
- [ ] int8: cùng gate; nếu rớt → ghi số + giữ fp32/sidecar.
- [ ] Latency: batch-1 P95 so với Python sidecar hiện tại (số liệu decision).
- [ ] Decision record written — go hoặc no-go đều chốt bằng số.

**Kết quả:** reports/phase03-gliner-onnx.md
