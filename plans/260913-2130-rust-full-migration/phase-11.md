# Phase 11 — WAVE F1: MCP server khung (rmcp) — wire contract + tool catalog

## Mục tiêu

Dựng MCP server Rust (`rust/crates/cortex-mcp/`) qua SDK **`rmcp`** chính thức, thay
`fastmcp_server.py` (2.9k) + `unified_mcp.py` (3.2k), giữ **wire contract
`cortex.mcp.tool-result` v1.0 byte-compatible** — MCP clients (Claude/IDE) không được thấy
khác biệt.

## Scope

1. **Khung server**: rmcp transport (streamable HTTP :8788/:8789 như hiện tại),
   lifecycle, per-instance isolation gates (`CORTEX_STORAGE_INSTANCE`,
   `CORTEX_MCP_PAUSE_BY_INSTANCE` — đã thấy trong `dev mcp-gates`).
2. **Error/contract layer**: port `mcp_contract.py` — error code aliases
   (`unsupported_capability→capability_unavailable`...), `_INTERNAL_ERROR_DETAIL_KEYS`
   filtering, canonical envelope `{ok, data, error}`.
3. **Tool catalog**: `tool_metadata.py` (1k) — build_catalog, tool descriptions/annotations;
   `framework_registry.py` (963) — servlet_active_generation_predicate v.v.
4. **Project registry/scope**: mọi tool nhận `project_id` → `resolve_project_targets`
   (đọc config dir như đã phân tích — mỗi `*.json` một project); `prepare_project_scope_parameters`,
   `project_id_lookup_key`, `project_not_registered` error shape.
5. **Capability matrix**: tool khai báo support đúng như Python (`endpoints: none`,
   `database: none`, `symbols/calls: generic`) — port `capability` block byte-level.

## Golden contract tests (bắt buộc — nền cho Phase 12–13)

- Recorder: script gọi **MCP Python đang chạy** (8788/8789) với bộ query cố định cho mọi
  tool → lưu fixture JSON (đã test tay ở real-world test: explore/search/subgraph đều record được).
- Comparator: Rust server trả response → so **theo key, byte-level ở scalar fields**,
  mask các trường volatile khai báo (`updated_at`, `_graph_id`, elapsed).
- Fixture dùng cả 3 project: procsample (đang có), stock (mới ingest), testdata.

## Gate

- [ ] Server Rust lên được :8788 (hoặc port test), MCP client bắt tay + list tools khớp catalog.
- [ ] Contract fixtures cho error paths: project_not_registered, invalid_parameters,
      capability_unavailable — byte-match.
- [ ] `dev mcp start/stop/gates` quản được server Rust qua cùng interface.
