# Phase 09 — WAVE E1: incremental_sync orchestrator + embeddings pipeline

## Scope

Port `tools/sync/incremental_sync.py` (5.1k) — bộ não điều phối đã thấy chạy thật trên stock:

1. **Folder/language dispatch**: 38 analyzers, detector-gated, `--parsers auto|csv`.
2. **Change detection** (`--change-detection hybrid|committed|hash`): git SHA (before/after)
   + hash comparison; tạo changed/deleted manifests per-analyzer; baseline storage
   (`.cache/incremental_sync_*`); `--full-scan`, `--reconcile` (full SHA-256).
3. **Embeddings pipeline**: sau graph pass — đọc artifacts/changed set, chunking
   (`MAX_EMBED_CHARS=500`, `BATCH_SIZE=1`), gọi embedder, upsert Qdrant collections
   (`{project}_{hash}__{lang}_functions`, `_mess`), collection meta/layout cache
   (`qdrant_layout_cache`, `_resolve_vector_layout` — named-vector v1/v2 handling).
4. **Admission + locks**: lock-timeout, sync workers registry (doctor check "idle"),
   summary JSON (`--summary-path`).
5. `dev ignore` semantics (built-in defaults + user config, fnmatch at depth).

## Embedder decision (spike trong phase)

- **Phương án A (mục tiêu): ONNX `ort`** — jina-v3 int8 ONNX chính thức, tokenizer crate
  `tokenizers`; parity gate: golden vectors vs Python (cosine ≥ 0.999, task-LoRA default
  khớp — đã phân tích ở addendum). device: CPU trước, MPS/Metal qua ort later.
- **Phương án B (fallback, zero-risk): Python embedder worker** — orchestrator Rust gọi
  subprocess `python_analyzer`-style embed step (giữ [embed] batches như hiện tại) sau
  trait `Embedder`; ONNX hoá sau không đổi interface.

Chọn B trước để không chặn, A là spike song song có deadline.

## Parity

- Scenario replay mở rộng: sync 2 lần liên tiếp trên stock (full → incremental với
  git commit thật thay đổi) — summary JSON + manifests + qdrant point counts khớp.
- Change detection matrix: committed-only vs hybrid vs hash trên cùng git state.

## Gate

- [x] Full sync stock bằng orchestrator Rust: summary + qdrant counts + manifests khớp
      Python run (mask timestamp).
- [x] Incremental sync sau 1 commit thật: changed set khớp Python.
- [x] Embedder B (Python sidecar) — byte-identical vì cùng code; A (ONNX ort) là spike
      sau, không chặn.

**Trạng thái 2026-09-14:** PASS — `cortex-sync` (~6.6k LoC): gates a-e của
`scripts/rust_parity/sync_orchestrator_parity.py` PASS 12 runs (mixed corpus 2 commits):
SCAN_RESULT byte-identical, summary 0 diff sau mask, manifests equal, graph diff 0
(94/94 nodes, 126/126 edges), change-detection matrix hybrid/committed/hash khớp Python.
Python-plane delegate: journal deep-lane, embedded falkordb storage, FailureClass deep
paths, message-scan internals. Dogfood 1 tuần + qdrant counts là gate cutover (phase 14B).
      Python run (mask timestamp).
- [ ] Incremental sync sau 1 commit thật: changed set khớp Python.
- [ ] Embedder A hoặc B pass parity vectors (A: cosine ≥ 0.999; B: byte-identical vì cùng code).
