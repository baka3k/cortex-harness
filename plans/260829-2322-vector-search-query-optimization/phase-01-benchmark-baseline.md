# Phase 01 — Benchmark harness + instrumentation + baseline

Goal: đo được latency `semantic_search` theo stage (embed / collection
metadata / qdrant merge / graph expansion / total) trước khi có thay đổi,
để mọi phase sau có số đối chứng. Không đổi behavior.

**Bề mặt đo = đúng bề mặt production:** `dev.py` launch
`mcp/unified_mcp.py` (`dev.py:171-177`); `semantic_search` không nằm trong
`_FANOUT_SEARCH_TOOLS` nên `_dispatch_tool` route về default backend
`cplus` → `cplus_mcp.tool_semantic_search` (`unified_mcp.py:150-166`,
`:451`, `:531-536`). Benchmark phải đo qua dispatch đó, không gọi thẳng
`fastmcp_server`.

## Changes

### 1. Benchmark script — `scripts/benchmark_semantic_search.py` (mới)

Đặt cạnh `scripts/validate_retrieval.py` (cùng nhu cầu wiring
`mcp_runtime_config.runtime_environment` + storage resolution). Hai mode:

- **Synthetic (mặc định, deterministic):** dựng local Qdrant tạm trong tmp
  dir với 3 collection giả lập (2 kiểu payload: legacy-analyzer-style và
  primary_vector_sync-style; vector size 8, vector random cố định seed).
  Embed query dùng stub (không tải model) khi đo stage-đường-ống, và dùng
  model thật khi đo stage-embed.
- **Live (`--live`):** chạy trên store thật của project config hiện tại
  (local hoặc remote theo config) — đo end-to-end **qua
  `unified_mcp._dispatch_tool("semantic_search", ...)` in-process** (đúng
  đường production). Ghi thêm trường `backend_resolved` để thấy dispatch
  đi về đâu.

Kịch bản đo (mỗi kịch bản N=20 query, warmup 3):
1. `scoped-repeat` — 1 collection, cùng 1 query lặp (đo cache-hit tiềm năng).
2. `scoped-cold` — 1 collection, query khác nhau (đo cold embed + qdrant).
3. `unscoped-multi` — không `collection`, đủ collection (đo metadata +
   merge path).
4. `expand-graph` — `expand_graph=true` trên scoped (đo chi phí expansion
   tách khỏi vector path).

Output: JSON + markdown vào
`plans/260829-2322-vector-search-query-optimization/reports/`
(`baseline.json`, `baseline.md`) với p50/p95 từng stage per kịch bản.
Flag `--json-out` để phase sau ghi file so sánh riêng.

### 2. Timing instrumentation — cả 4 backend copy

Trong `tool_semantic_search` của `fastmcp_server.py` (:1365-1517),
`cplus_mcp.py` (~:1765), `android_mcp.py` (~:1440), `java_mcp.py`
(~:1187): bọc `time.perf_counter()` quanh 4 đoạn — `_embed_query`,
resolve+filter collections, `_merge_qdrant_results`,
`expand_semantic_results` — emit **`logger.info`** (không dùng debug —
level mặc định sẽ nuốt số baseline) kèm tên module:

```
semantic_search timing[cplus]: embed=%.3f resolve=%.3f qdrant=%.3f expand=%.3f total=%.3f
```

Gate bởi env `MCP_SEARCH_TIMING` (mặc định "1", "0" tắt). Không đổi
signature, không đổi return. (Phase 04 sẽ gom 4 bản sao này qua helper
chung — instrumentation tạm thời nhân bản, xóa khi phase 04 merge.)

Tương tự 1 dòng timing trong `intelligent_retrieval.search()` đã có
elapsed (`:544`, `:626-629`) — giữ nguyên, không đụng.

## Tests

- `tests/test_benchmark_semantic_search.py` (mới): chạy script synthetic
  mode với stub embedder trên tmp store; assert sinh JSON đủ 4 kịch bản,
  các trường p50/p95 số học hợp lệ. Đặt `MCP_PRELOAD_EMBEDDER=0` như
  `test_qdrant_project_scope.py:15-24`.

## Acceptance

- [ ] Baseline report tồn tại với số p50/p95 4 kịch bản, đo qua unified
      dispatch (commit vào `reports/`).
- [ ] Số gate tạm trong plan.md được chốt lại (hoặc xác nhận) dựa trên
      baseline — ghi vào `reports/baseline.md`.
- [ ] Suite hiện tại pass; `git diff` không đụng behavior search.

## Rollback

Xoá script + revert instrumentation (độc lập, không dependency ngược).
