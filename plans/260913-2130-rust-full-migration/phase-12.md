# Phase 12 — WAVE F2: MCP graph tools (graph_mcp surface)

## Scope — port các graph tools đã thấy hoạt động thật trên stock

| Tool group | Python nguồn | Ghi chú |
|---|---|---|
| explore_graph (hybrid fusion) | `services/explore_service.py` + `intelligent_retrieval` ported | Rust fusion đã có (phase 04-1715); port phần network IO: `_qdrant_search`, `_graph_keyword_search`, `graph_expander` (uses traits đã预留) |
| search_functions + query_subgraph | `unified_mcp.py` (3.2k) tool defs | parser fanout semantics (parsers_searched android/cplus như thấy thật) — replicate đúng fanout + dedup (`dedup_removed`) |
| find_callers_of_endpoint / get_endpoints / find_screen_workflows / analyze_workflow_impact | `services/*` (2.9k) + impact_service | capability matrix giữ nguyên (`endpoints: none` → trả unsupported như Python) |
| flow_reconstructor | `services/flow_reconstructor` | |
| framework overlays query tools | `mcp/{java,cplus,android}/*_mcp.py` (8.6k) | các tool riêng per-language server; port theo overlay đã có ở Wave D |
| falkordb/ladybug discovery | 2 file nhỏ | discovery data files |

## Điểm cần preserve (đã quan sát thật trên stock)

- `search_functions` fanout: parsers_searched list, per-parser `db` name, `dedup_removed`.
- `explore_graph` response: `matched_nodes[].reason` text format, `signals.freshness`,
  `query_analysis` (intent/entities/keywords/domain_signals/embedding_text — **đã port ở
  phase-03-1715, reuse**), `retrieval` block (graph_database/qdrant_collections), `capability`.
- `query_subgraph`: nodes kèm full `note` (Summary inferred + Code block) — format byte-level.

## Parity

- Golden contract fixtures Phase 11 (procsample + stock + testdata) phải byte-match cho
  toàn bộ graph tools, gồm error paths.
- Thêm fixture "sau sync mới": sync stock 1 commit thật rồi gọi lại explore — kết quả
  phản ánh dữ liệu mới (khớp Python cùngMoment).

## Gate

- [ ] Toàn bộ graph tools pass golden contract (byte-level, mask khai báo).
- [ ] `CORTEX_MCP_BACKEND=rust` dogfood 1 tuần trên stock: không có report lệch kết quả.
