# Phase 02 — bge-m3 dense ort + swap seam mind/doc (flag rollback)

## Scope

1. **bge-m3 dense ONNX** (1024-dim, dense head only — sparse/colbert không dùng ở đâu
   trong repo): **dùng artifact chính thức** `BAAI/bge-m3/onnx/model.onnx` (opset 11,
   inputs sạch `input_ids`+`attention_mask`, outputs `token_embeddings`+`sentence_embedding`,
   data 2.27GB) — không cần `optimum` export. Download + checksum + pin snapshot rev.
   Chạy **trên CPU** (khớp mặc định `EMBEDDING_DEVICE=cpu` của plane này).
   - Encode style parity — **(SỬA, findings C2)**: `modules.json` của bge-m3 có
     `2_Normalize` và `1_Pooling: pooling_mode_cls_token=true`, `max_seq_length=8192`,
     fp32 ⇒ vector thật là **CLS-pool + L2-normalize**, KHÔNG phải "raw không normalize"
     như bản plan cũ. Verify output `sentence_embedding` của graph khớp CLS+normalize;
     nếu khớp thì Rust dùng thẳng output đó, nếu không thì pool từ `token_embeddings`.
     Plan cũng phải giữ fixture riêng để phân biệt với lane code plane.
2. Swap seam sau flag `CORTEX_EMBED_BACKEND=python|onnx` (default `python`):
   - **#8 mind MCP**: `cortex-mcp/src/mind/embed.rs` — thay `scripts/rust_mcp/
     embed_worker.py` bằng `OnnxEmbedder` khi `=onnx`; giữ response shape
     `{"dimension": 1024, ...}` nguyên văn; env chain `EMBEDDING_MODEL_PATH →
     EMBEDDING_MODEL → BAAI/bge-m3` mirror y hệt (`embed_worker.py:30-45`).
   - **#9 ingest-doc**: `cortex-doc/src/embed.rs` (one-shot sidecar hiện tại) →
     `cortex-embed::OnnxEmbedder`; giữ dim assert 1024 (`embed.rs:115`) và payload shape.
   - **#10 query-doc CLI**: tái dùng seam (quyết inline trong phase: swap cùng lúc hay
     giữ Python — criterion: cùng env chain + cùng fixture).
3. Parity harness: mở rộng `scripts/rust_parity/embed_parity.py` — corpus = stock doc
   paragraphs (đúng bound `MAX_PARAGRAPH_CHARS`) + mind query texts thật;
   `compare_mind.py` chạy lại với backend onnx (GATE semantic + graph_rag).

## Touchpoints inventory liên quan

#8, #9, #10. (Không đụng #6/#7 — là phase-04.)

## Gates

- [ ] Cosine ≥ 0.999 từng cặp bge-m3 fp32 (corpus ≥ 300 paragraphs + 100 queries).
- [ ] int8: gate kép cosine + recall@10 (cùng tiêu chí phase-01); fail ⇒ fp32.
- [ ] P95 query-embed (mind tools, warm) ≤ P95 sidecar persistent hiện tại (đo cùng máy);
      throughput one-shot doc-ingest ≥ 0.8× sidecar.
- [ ] `CORTEX_EMBED_BACKEND=python` rollback test: kết quả byte-equivalent với hiện trạng.
- [ ] `compare_mind.py` all-gates PASS với backend onnx.

**Kết quả:** reports/phase02-bgem3-parity.md
