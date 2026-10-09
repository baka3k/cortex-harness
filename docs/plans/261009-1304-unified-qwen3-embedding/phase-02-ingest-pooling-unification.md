# Phase 02 — Ingest pooling: 12 CodeEmbedder → ST-backed + policy sweep (cobol, primary sync)

> Sửa theo red-team: F6 (cobol có sync riêng với heuristic jina inline), F10 (legacy ST encode branch chưa normalize), AC2 grep gate trúng `ts_analyzer.py.bak`. **Phụ thuộc P1** (`model_policy`).
> Tham chiếu luồng: [flows.md](flows.md) **§1 Ingest CODE** — sơ đồ 2 đường (A: 12 legacy CodeEmbedder → QdrantWriter → local_qdrant; B: primary/delegating → primary_vector_sync) + bảng thay đổi từng bước.

## Goal

Mọi đường ingest (12 legacy analyzers + primary sync + cobol riêng) sinh vector last-token pooling + normalized đúng chuẩn Qwen3; heuristic jina gọn về một điểm gọi policy.

## Changes

1. **12 bản sao `CodeEmbedder`** (python_analyzer.py:947, js:906, java:1309, cplus:2738, kotlin:1008, php:859, ts:490, sql:1090, delphi:1334, csharp:826, plsql:1365, android_kotlin:1163; vb_analyzer_base cùng pattern):
   - Load/encode delegate sang `embed_runtime.get_sentence_transformer(model_source, local_files_only=...)` — ST áp pooling từ model config; **giữ nguyên** `_truncate_text`/`_split_chunks`/`_mean_pool_chunks`, `max_embed_chars`, `chunk_embed`, `_infer_vector_size` (ưu tiên `get_sentence_embedding_dimension()` của ST).
   - Xóa local `_should_trust_remote_code` + `fix_mistral_regex` (python_analyzer.py:952) — policy quyết định.
   - Giữ `_resolve_embedding_model_source` (override `CODE_EMBEDDING_MODEL_PATH`).
2. **`primary_vector_sync.py:309`** — heuristic jina → `model_policy(...)`. `normalize_embeddings=True` giữ nguyên.
3. **`code-tiny/tools/cobol/qdrant.py:129`** — inline jina heuristic trong sync riêng của cobol (F6) → `model_policy`.
4. **Runtime branch checks** `"jina-embeddings-v3" in model_source.lower()` (cplus_analyzer.py:2758, kotlin_analyzer.py:1023, android_kotlin_analyzer.py:1188 — use_flash_attn) → policy (Qwen3: no-op).
5. **Legacy ST encode branch** (`python_analyzer.py:995-1004` và bản copy tương đương): thêm `normalize_embeddings=True` (F10, khớp D8 — ingest luôn normalized).

## Acceptance criteria

- AC1: CodeEmbedder Qwen3 → ST backend (last-token pooling) parity stub; jina giữ hành vi cũ (AutoModel + trust_remote_code qua policy).
- AC2: `rg '_should_trust_remote_code|fix_mistral_regex' code-tiny -t py -g '!*.bak'` chỉ còn embed_runtime + tests. (Gate loại `*.bak` — `ts_analyzer.py.bak` có 3 hits, không sửa file backup.)
- AC3: Chunking/truncation/`vector_size` hành vi không đổi (1024 từ ST).
- AC4: Suite hiện tại green, đặc biệt `test_primary_analyzer_vector_contract.py` (flag plumbing) + `test_embed_runtime.py:248,253` (module split + delegate names).

## Test scope (test-budget mapping)

- AC1 → 1 test parameterized qua danh sách 13 module (stub ST loader). AC3 → 1 test `_infer_vector_size` + chunk wrapper stub. Còn lại: suite + grep gate (P5).
- Kỳ vọng: ~3 test functions mới (parameterized). Size **L** (13 file, mỗi file chỉ vùng load/encode — craft chia 2 đợt 6-7 file).

## Out of scope

- Không đổi literal default model name (P4); không đụng writer/collection (P3); không đụng argparse flags.
