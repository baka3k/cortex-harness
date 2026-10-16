# Smoke report: repo scope trên store thật (Phase 07)

- **Date:** 2026-10-16
- **Script:** [`smoke_repo_scope.py`](./smoke_repo_scope.py)
- **Stores:** FalkorDBLite 0.10.0 (`.rdb`, dev default) + LadybugDB 0.20.4 (`.lbdb`) — fresh temp store mỗi lần chạy.
- **Result: 18/18 PASS (9 checks × 2 providers). Gate RT#3 (ENDS WITH trên falkordblite) = ĐÓNG.**

## Checks (cả 2 provider đều pass)

1. `ENDS WITH` suffix match trả cả 2 flavor (`/Users/dev/workspaces/s3` + `cplus_proj/s3`), loại decoy `xs3`.
2. Exact abspath match.
3. Unscoped no-op trả tất cả node (kể cả node không có `repo`).
4. Unknown repo → 0 kết quả (không lỗi).
5. Helper query `DISTINCT n.repo` (nhánh warning) trả đủ 4 giá trị known.
6. Tool layer thật (`listup_symbols_matching_file_path`, fastmcp) với `repo="s3"` → chỉ f1+f2.
7. Response có `scope` block (`repo:"s3"`, `filter_applied:true`).
8. `warnings` rỗng khi có kết quả.
9. Unknown repo qua tool → warning kèm known repo values (nhánh rỗng chạy query phụ).

## Deviation khỏi plan (đã disclose)

### 1. Fragment `$repo IS NULL` → `$repo = ''` (BUG thật, smoke bắt được)

Plan quy định fragment `($repo IS NULL OR X.repo = $repo OR X.repo ENDS WITH $repo_suffix)`.
Live probe cho thấy **LadybugDB bind parameter `None` thành BOOL**: nhánh
`$repo IS NULL` định kiểu `$repo` thành BOOL, khi engine đánh giá
`n.repo = $repo` (STRING so với BOOL) nổ
`Conversion exception: Value '...' is not a valid boolean` — ngay cả khi
nhánh OR đầu đã đúng (không short-circuit ở mức bind/check).

Pattern `project_id` sống sót vì nhánh IS NULL dùng **param khác**
(`$project_id`) so với nhánh so sánh (`$project_id_normalized`).

**Fix:** helper inject empty string thay vì `None`; fragment chuẩn:
`($repo = '' OR X.repo = $repo OR X.repo ENDS WITH $repo_suffix)` — mọi
reference giữ kiểu STRING trên cả 3 backend (đã verify trên
falkordblite + ladybug; Neo4j chấp nhận `''` tương đương). Chi tiết trong
`docs/REPO_QUERY_RULES.md` §2.

### 2. Ladybug seed smoke: `repo: NULL` đầu tiên làm tự-tạo bảng infer sai kiểu

Seed script tự CREATE node qua raw Cypher (không qua manifest) — nếu dòng
đầu tiên có `repo: NULL`, cột bị infer thành BOOL. Seed đã sắp xếp dòng
`repo`-less xuống cuối. Đây là artifact của smoke, không phải sản phẩm:
writer thật (`language_writer`) tạo bảng theo `ladybug_schema` với `repo`
STRING.

## Kết luận

- `ENDS WITH` hoạt động native trên falkordblite (không cần fallback `RIGHT()`).
- Parity falkordblite ↔ ladybug đạt trên cùng bộ check.
- Không block merge.
