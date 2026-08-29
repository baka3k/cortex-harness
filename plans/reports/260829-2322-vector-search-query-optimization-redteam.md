# Red team — 260829-2322-vector-search-query-optimization

- Ngày: 2026-08-29
- Reviewer: adversarial subagent (read-only, đối chiếu trực tiếp codebase,
  105 tool calls, verify từng claim Against code + qdrant-client 1.18.0
  trong `.venv`)
- Verdict gốc: **FIX-FIRST** (4 BLOCKER, 5 MAJOR, 5 MINOR)
- Kết quả: toàn bộ đã xử lý trong plan/phase files cùng ngày.

## Findings & disposition

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| 1 | BLOCKER | `code-tiny/tools/common/embedding_runtime.py` đã tồn tại (`resolve_embedding_cache` + HF audit, 12 analyzer import, có test riêng) — plan định tạo module cùng tên | Đổi module mới thành `embed_runtime.py`; module cũ không đụng; thêm test guard assert API cũ còn nguyên (phase-02, plan decision #6) |
| 2 | BLOCKER | Production route `semantic_search` qua `unified_mcp` → default backend `cplus` (`unified_mcp.py:150-166/:451/:531-536`, dev.py:171-177), KHÔNG qua `fastmcp_server` — Phase 04 sửa 1 bản copy là vô hiệu; G3/G4/G5 đo nhầm bề mặt | Phase 04 gom helper chung `tools/common/qdrant_query_support.py` cho cả 4 backend delegate; Phase 01 đo qua `unified_mcp._dispatch_tool`; gates ghi rõ bề mặt đo (plan overview, phase-01, phase-04) |
| 3 | BLOCKER | Local-mode Qdrant no-op hóa `create_payload_index` (`qdrant_local.py:903-918`), `payload_schema` luôn `{}`, nuốt HNSW kwargs — Phase 05 acceptance không thể pass trên local | Phase 05 restate remote-only value; local = call-through + warning inert; acceptance remote có mệnh đề deferred |
| 4 | BLOCKER | `_normalize_embed_device("auto")` pass-through (`dev.py:571-572`) + dev sync truyền raw CLI → `SentenceTransformer(device="auto")` crash mọi analyzer trừ python (python đã tự resolve, `:1977-1984`) | Phase 03: dev.py resolve "auto" → device cụ thể TRƯỚC khi export env/dựng CLI; test assert phủ định `"auto"` trong CLI |
| 5 | MAJOR | `_PAYLOAD_INCLUDE_FIELDS` sót `class_name`/`package_name` (kotlin `:2019-2075`, android_kotlin) — claim "union đầy đủ" sai | Đổi sang `PayloadSelectorExclude(["text"])` — không cần liệt kê (plan decision #5, phase-04) |
| 6 | MAJOR | Lazy retrieve thiếu collection provenance: merge dedupe theo point-id, point id trùng được giữa collection (uuid5 deterministic) | Tag `_collection` ngay từ `_qdrant_search`; `lazy_full_payload` group theo collection (phase-04) |
| 7 | MAJOR | Classifier lỗi chỉ nhận "cuda" — MPS fail ném thẳng ra caller; preload fail (sau auto-detect) crash server boot | Phase 02: `_is_accelerator_runtime_error` nhận MPS/Metal; Phase 03: preload bọc try/except, server vẫn boot |
| 8 | MAJOR | Rationale "staging generation chưa publish" SAI — sync upsert thẳng collection sống (`primary_vector_sync.py:330-332`), staging thuộc plan 260807-0929 pending | Viết lại rationale theo re-sync self-heal (`_delete_stale` + re-upsert) ở phase-06 + plan risk table |
| 9 | MAJOR | `explore_service._make_embedder` là `SentenceTransformer(model_name)` KHÔNG có device arg — mô tả trong plan sai | Sửa mô tả; Phase 03 pin device tường minh `device=embed_runtime.resolve_device()` |
| 10 | MINOR | Line-ref drift: BATCH_SIZE `:2801/:2813` (không phải :2803/:2810), MAX_EMBED_CHARS `:2802`, cplus retry `:962`, cplus `_get_embedder` `:895`, android `:712`, java `:561`, `_VECTOR_LAYOUT_CACHE` dict `:109` logic `:125-149` | Đã hiệu chỉnh trong plan.md + phase files |
| 11 | MINOR | logger.debug sẽ bị nuốt — baseline không đọc được số | Phase 01 đổi sang `logger.info` gated `MCP_SEARCH_TIMING` |
| 12 | MINOR | Rebuild script thiếu count assert sau re-upload bước swap | Phase 05 thêm count assert lần 2 trên collection đích trước khi xoá temp |
| 13 | MINOR | Query-vector cache key thiếu device | Ghi giả định rõ (device cố định/process) trong phase-02 |
| 14 | MINOR | Claim "auto-detect chỉ tồn tại nửa vời" sai — python_analyzer đã full auto | Sửa baseline plan.md; thành đầu vào cho fix #4 |

## Gate validity

- G1 đo được và trung thực (jina-v3 + xlm-roberta tokenizer đã có trong HF
  cache local; LRU hit mức µs).
- G2 đo được trên máy này (MPS available); mức "≥2×" chưa chứng minh cho
  batch-1/512-token jina-v3 — acceptance kèm tolerance check, fallback
  lever (float16/ONNX) ghi nhận ngoài scope.
- G3/G4/G5: ban đầu đo nhầm bề mặt (finding #2) — đã re-point qua unified
  dispatch.
- G6: đo được; 3× có thể lạc quan với short-text — phase-06 có mệnh đề
  chốt lại số thực tế.

## Scope gaps

Không phase nào tái giới thiệu song song hoá #2/#5 hay đổi model — nhất
quán với quyết định người dùng. Defect duy nhất (include-list, #5) đã xử
lý bằng Exclude.
