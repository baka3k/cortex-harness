# Phase 02: Backend `fastmcp_server.py` — repo param + predicate

- **Size:** L
- **Depends on:** Phase 01
- **BlockedBy:** none

## Deliverable

Toàn bộ tool query đọc của `code-tiny/mcp/fastmcp_server.py` nhận `repo: Optional[str] = None` và áp predicate repo lên node anchor. Bao gồm: các workflow tools của fastmcp (3 tool cuối theo research-digest) + mọi query template Cypher trong file đang có predicate `project_id` (~12 template, `fastmcp_server.py:1600-2451, 2604`).

## Anchor rules (bảng bắt buộc đối chiếu trước khi merge)

| Query nhóm | Alias anchor đặt predicate | Lý do |
|---|---|---|
| Search node theo tên/code | node kết quả chính | filter trực tiếp |
| Callers/Callees (`:1600-1601`, s/t) | **chỉ phía node được hỏi** (anchor), phía kia free | cross-repo CALLS là kết quả hợp lệ (plan.md RT#1) |
| Files-by-… | File node | — |
| Workflowfinder (`:2604`, w) | w | — |
| Fallback contains (`:2391`, n) | n | — |

> Implementer bổ sung từng tool vào bảng này khi sửa; tool nào không chắc anchor → ghi vào summary để reviewer quyết, **không tự đặt cả 2 phía**.

## Steps

1. Mọi tool function query đọc: thêm `repo: Optional[str] = None` vào signature (FastMCP tự derive schema).
2. Mỗi query template: chèn `AND ` + `repo_scope_predicate(<anchor_alias>)` ngay sau predicate project scope tương ứng.
3. Params đi qua `prepare_repo_scope_parameters` (chain sau `prepare_project_scope_parameters` — thứ tự không quan trọng vì key khác nhau, nhưng nhất quán hóa: project trước, repo sau).
4. Response: thêm block `scope` (`{"repo": <giá trị đã normalize: str|null>, "filter_applied": bool}`) + `warnings` mirror vào `data` của các tool trả list kết quả. Tool trả node đơn → vẫn thêm `scope`.
5. Warning khi `filter_applied` và kết quả rỗng: `"repo '<x>' matched 0 nodes"` (chốt Open question #1: kèm top-5 DISTINCT repo nếu query rỗng — 1 query phụ chỉ chạy nhánh rỗng).

## Files

- `code-tiny/mcp/fastmcp_server.py`
- `tests/` — test file cho backend fastmcp (tìm test hiện có của các tool này để mở rộng; nếu chưa có → file mới `tests/test_repo_scope_fastmcp.py`, dùng RecordingRunner pattern từ `test_project_context_service.py:142`).

## Test scope

- Mapped acceptance criteria: RR1 (không repo → Cypher không đổi structure: assert params không chứa `repo_suffix`), RR2/RR3 (repo="s3" → cả abspath-flavor lẫn basename-flavor node được trả; "xs3" không được trả), scope block hiện diện + warnings rỗng-khi-có-kết-quả.
- Expected size: **M–L** (~8–12 test functions; 1 query template đại diện mỗi nhóm anchor + 1 template mỗi biến thể predicate 2-alias).
- Chạy: pytest file mới + `tests/test_unified_mcp_wrapper_signatures.py` (chưa assert repo ở phase này — gate tổng ở Phase 05).

## Red-team notes

- Template nào quên chèn predicate → tool vẫn chạy nhưng filter lơ lửng (param bị bỏ qua) — tệ hơn lỗi. Checklist: đếm template có `project_id IS NULL` trước/sau patch, số phải khớp số template có repo predicate (script nhỏ trong test: grep-based assertion hoặc introspect source).
- `get_ipc_message` / `find_screen_workflows`: xác minh node có property `repo` không; không có → bỏ qua tool, ghi kết quả xác minh vào phase summary (feeds Phase 07 docs).
