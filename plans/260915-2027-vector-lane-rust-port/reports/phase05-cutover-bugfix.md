# Phase 05 — Cutover housekeeping + bugfix chặn — BÁO CÁO

Ngày: 2026-09-15.

## 1. Fix `list_source_ids` / binder exception (chặn cutover)

**Triệu chứng live**: `mind_mcp.list_source_ids` lỗi
`Binder exception: Cannot change parameter expression data type from BOOL to STRING`
(Ladybug binder suy kiểu param NULL thành BOOL rồi từ chối chỗ `STARTS WITH`).

**Root cause thứ hai lộ ra sau fix**: fan-out nhiều project — project smoke cũ
`phase06go` (đăng ký trong `.cortext-harness/config/phase06-smoke.json`) không có doc
store sau migration → lỗi "database does not exist" làm hỏng toàn bộ tool dù store
`cortext_doc` đọc tốt.

**Fix (mirror 2 phía)**:
- Python `doc-tiny/mcp_graph_rag.py` — 4 query site (fetch_entities_by_ids,
  fetch_relations_by_entity_ids, get_paragraph_text, list_source_ids): tách nhánh
  query, không bao giờ truyền param NULL; `list_source_ids` thêm fail-soft
  per-store (skip store lỗi, trả phần đọc được).
- Rust `mind/graphstore.rs` — cùng 4 query site, tách nhánh `match project_key` +
  fail-soft fan-out ở `list_source_ids_graph`.

**Verify**:
- Server live doc-tiny restart với code mới → `list_source_ids` trả `ok: true`
  (trước: binder exception / database-not-found).
- `compare_mind.py`: **36/36 PASS** (không regression trên fixture falkordb).
- Toàn bộ pytest MCP suite: **162 passed, 3 skipped, 0 failed** (trước: 24 failed).

## 2. Fix `test_mcp_acceptance_matrix.py` (24 fail)

- `test_every_primary_language_parser_maps_to_a_profile_and_real_entrypoint`:
  contract "real entrypoint" đổi sang `_RUST_ANALYZER_BINARIES` (python scripts đã
  bị xoá ở analyzer cutover — test còn assert thế giới cũ).
- Row `csharp` trong ACCEPTANCE_MATRIX: cập nhật `endpoints/database = partial`
  theo registry (commit 588999f "Roslyn-first" nâng capability nhưng quên matrix).

**Verify**: `pytest tests/test_mcp_acceptance_matrix.py` → 3 passed, 78 subtests,
0 fail.

## 3. Cutover vận hành

- Binary `cortex-mcp` build lại cả debug + release (chứa vector lane + mirror fix).
- `dev mcp start` auto-flip đã sẵn sàng chọn Rust khi binary tồn tại; runbook có
  ma trận env đầy đủ. **Lưu ý chưa flip mặc định toàn bộ**: 6 case explore/expand
  còn divergence (report phase03-04) — flip thống nhất chờ graph-track xử lý 2 nhóm
  đó; flip từng server vẫn khả dụng qua `CORTEX_MCP_BACKEND`.

## Gate

- [x] 2 bug chặn fix + mirror 2 phía + test xanh (pytest 162 passed / 0 failed).
- [x] E2E dual-run snapshot: golden replay (compare_vector) — kết quả trong
      report phase03-04.
- [~] Flip default: để follow-up cho graph-track (6 case explore/expand còn
      divergence); cờ rollback test tồn tại (`CORTEX_MCP_BACKEND=python`).
- [x] Runbook + reports cập nhật.
