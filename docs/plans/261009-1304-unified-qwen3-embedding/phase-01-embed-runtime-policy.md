# Phase 01 — Query-side encode paths: model policy + pooling chuẩn Qwen3 (code + doc)

> Sửa theo red-team: F3 (CPU fallback mean-pool nhầm), F4 (doc query + explore_service trước đây không thuộc phase nào), F10 (local_files_only).
> Tham chiếu luồng: [flows.md](flows.md) **§3 Query MCP** — sơ đồ 4 đường query hiện trạng (embed_query mean-pool, explore_service trần, doc query trần, shared search_collection) + bảng thay đổi.

## Goal

Mọi đường query-side (code MCP, graph fan-out, doc query) sinh vector đúng chuẩn Qwen3 (last-token pooling + query instruction); mọi quyết định model-specific tập trung vào MỘT policy helper.

## Changes

1. `code-tiny/tools/common/embed_runtime.py` — thêm `model_policy(model_name)`:
   - Trả `{trust_remote_code: bool, backend: "st"|"automodel", query_prompt: Optional[str], normalize: bool}`.
   - Policy: tên chứa `qwen3-embedding` → `backend="st"`, dùng `prompt_name="query"` (từ ST config của model), `trust_remote_code=False`, `normalize=True`; `jina` → legacy (AutoModel OK, trust_remote_code=True); còn lại → `backend="st"` default config.
   - Thay 2 site heuristic nội bộ: `_should_trust_remote_code` (`:69-73`), nhánh `get_sentence_transformer` (`:288`).
   - `get_sentence_transformer` thêm tham số `local_files_only` (F10) — giữ hành vi offline snapshot; gọi từ policy path truyền `resolve_embedding_cache` semantics như hiện có.
2. `embed_query` (`:235-267`): policy `backend="st"` → `get_sentence_transformer(...).encode([text], prompt_name=..., normalize_embeddings=...)`. **F3 — CPU fallback ST-aware:** khi accelerator RuntimeError, evict đúng `_SENTENCE_TRANSFORMER_CACHE` (không phải `_EMBEDDER_CACHE` của AutoModel), reload ST trên CPU với cùng prompt/normalize; **cấm** rơi vào đường AutoModel mean-pool cho model thuộc policy ST.
3. **`explore_service._make_embedder`** (`code-tiny/mcp/services/explore_service.py:184`): `_model.encode([text])` → encode qua policy (`prompt_name="query"`, normalize cho Qwen3); jina/generic giữ hành vi cũ. Delegate fallback sang `cplus.cplus_mcp._embed_query` giữ nguyên signature (test `test_explore_project_scope.py:57-67`).
4. **Doc query instruction** (`doc-tiny/mcp_graph_rag.py:918,1019`, `doc-tiny/graphrag_query_langextract.py:180`): `embedder.encode([query])` → `encode([query], prompt_name="query", normalize_embeddings=True)`. Documents/paragraphs giữ nhúng trần. `prompt_name` là argument thuần của ST — doc-tiny không cần import từ code-tiny (F6).
5. Giữ nguyên: split `embed_runtime` vs `embedding_runtime` (guard `tests/test_embed_runtime.py:248`), delegate names patchable (`:253`), LRU cache key `(model, text)`.

## Acceptance criteria

- AC1: `embed_query(..., "Qwen/Qwen3-Embedding-0.6B")` == ST `encode(prompt_name="query", normalize_embeddings=True)` (parity, stub ST ghi nhận args).
- AC2: `explore_service._make_embedder` và doc query path encode với prompt_name đúng cho Qwen3, không prompt cho jina/generic (parameterized).
- AC3: Policy table đúng 4 lớp input: qwen3 / jina / local path / tên lạ.
- AC4: CPU fallback cho model ST-backend reload ST trên CPU, không bao giờ trả vector mean-pool; cache ST bị evict đúng.
- AC5: jina legacy không đổi hành vi (suite hiện tại green không sửa); `local_files_only` vẫn được truyền khi offline.

## Test scope (test-budget mapping)

- AC1 → 1 parity test; AC2 → 1 test parameterized (3 path × 2 model); AC3 → 1 test bảng 4 hàng; AC4 → 1 test fallback; AC5 → suite + 1 test local_files_only pass-through.
- Kỳ vọng: ~5 test functions mới. Size **M**. Không test model thật trên mạng — stub hết; load thật là verification thủ công (snapshot local, `HF_HUB_OFFLINE=1`).

## Out of scope

- Không đụng CodeEmbedder/analyzers (P2), không đụng marker/collection (P3), không đổi literal defaults (P4).
