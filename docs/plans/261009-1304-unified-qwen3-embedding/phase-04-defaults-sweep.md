# Phase 04 — Defaults sweep → Qwen/Qwen3-Embedding-0.6B + dependency floors + wire env chết

> Sửa theo red-team: F5 (dependency floors + fail-fast), F6 (`.env.example` ×2, cobol reclassify, doc-tiny flat không import được code-tiny), F10 (dev.py:769 set `EMBED_DEVICE` nhưng doc đọc `EMBEDDING_DEVICE`).
> Tham chiếu luồng: [flows.md](flows.md) **§4 dev init + env propagation** (sơ đồ init → config JSON → env merge cho code/doc process, 4 cái bẫy hiện tại) + **§5** (cột "Model mặc định mới" — danh sách đường cần sweep).

## Goal

Mọi default model converge về một giá trị mỗi tree; env chain hết dead-end; phiên bản thư viện đủ để load Qwen3; doc side không còn default CPU-only.

## Changes

1. **Dependency floors (F5)**: root `requirements.txt:39` → `transformers>=4.51,<4.56`; `sentence-transformers>=2.7.0` (root `:15`, `doc-tiny/requirements.txt:13`). Fail-fast: `embed_runtime` kiểm tra version khi policy chọn model Qwen3, thiếu → raise message rõ (không TypeError giữa chừng).
2. **Hằng số mỗi tree (F6)**: code-tiny `tools/common/model_defaults.py` `DEFAULT_CODE_EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-0.6B"`; doc-tiny `embedding_utils.py` `DEFAULT_DOC_EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-0.6B"` (flat tree không import code-tiny được — `graphrag_ingest_langextract.py:1450`; comment nhắc đồng bộ 2 giá trị).
3. **Code side literals → hằng số** (digest F1a): `dev.py:1731,3032,3528,3634`; 4 MCP chains (`fastmcp_server.py:116-121`, `cplus_mcp.py:125-129`, `android_mcp.py:104-108`, `java_mcp.py:103-107` — giữ thứ tự ưu tiên `CODE_EMBEDDING_MODEL_PATH` → `CODE_EMBEDDING_MODEL` → `JINA_MODEL_PATH` → default); 12 legacy entrypoints (python:1919, js:1791, java:2378, cplus:6109, kotlin:2152, php:1616, ts:1883, sql:2025, delphi:2497, csharp:1796, plsql:2309, vb:2323, android_java:772, android_kotlin:4391) + delegating (go:1270, rust:1318, swift:1207, perl:288, flutter:149, jp1:87, shell:315). **Cobol: heuristic đã xử lý ở P2; default literal ở đây** (`cobol_analyzer.py:220`, đọc `EMBEDDING_MODEL` one-off — giữ hành vi).
4. **Doc side literals**: `dev.py:1270,3053`; `mcp_graph_rag.py:80`; `graphrag_ingest_langextract.py:1519-1520`; `graphrag_query_langextract.py:175-176`; livingdoc ×6 (`living-doc-vectorize.py:257,46`, `living-doc-link.py:198,47`, `living-doc-vectorize-infra.py:60`, `living-doc-pipeline.py:67`).
5. **Wire env chết (D7, F10)**: `embedding_utils.resolve_embedding_model` chuỗi: arg → `EMBEDDING_MODEL_PATH` → `EMBEDDING_MODEL` → `DOC_EMBEDDING_MODEL` → default; `dev.py:769` đổi set `EMBEDD_DEVICE` → `EMBEDDING_DEVICE` cho doc process; gỡ `TEXT_EMBEDDING_MODEL` khỏi `doc-tiny/Readme.md:61`, root `.env.example`, `code-tiny/.env.example` (F6) + cập nhật `CODE_EMBEDDING_MODEL` value trong 2 `.env.example`.
6. **Doc device auto-detect (D5)**: `embedding_utils.resolve_embedding_device` (`:28-34`) — arg → `EMBEDDING_DEVICE` → auto (MPS/CUDA/CPU); `cpu` explicit vẫn tôn trọng.
7. **Tests pin literal**: `tests/test_primary_analyzer_vector_contract.py:173,177,193,208` fixture → `Qwen/Qwen3-Embedding-0.6B`; `tests/test_explore_project_scope.py:59,64` fixture → Qwen3; `tests/test_embedding_runtime.py:27` giữ jina (resolve_embedding_cache tên jina vẫn hợp lệ) + parameterize thêm Qwen3.

## Acceptance criteria

- AC1: Không set env → mọi entry point resolve default Qwen3 (test tầng hằng số + đại diện mỗi pipeline).
- AC2: `DOC_EMBEDDING_MODEL=x` (không EMBEDDING_MODEL) → doc side resolve x.
- AC3: `EMBEDDING_DEVICE=cpu` ép CPU; bỏ env → auto MPS trên macOS; dev.py truyền đúng tên env cho doc process.
- AC4: Version thiếu (stub `transformers.__version__` cũ) → fail-fast message rõ khi load Qwen3.
- AC5: Suite green; `.env.example` không còn chỉ model cũ.

## Test scope (test-budget mapping)

- AC1 → 1 test parameterized; AC2 → 1; AC3 → 1; AC4 → 1. Suite fixtures: sửa 2 file + parameterize 1.
- Kỳ vọng: ~4 test functions mới. Size **M**.

## Out of scope

- Không đụng argparse plumbing (chỉ giá trị default); config JSON cũ của user giữ value cũ tới khi re-init (ghi chú runbook P5).
