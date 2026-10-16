# Phase 05: `unified_mcp.py` pass-through + `tool_metadata.py` catalog + live-schema gate

- **Size:** M
- **Depends on:** Phase 02, 03, 04
- **BlockedBy:** none

## Deliverable

1. **Direct tools** của `unified_mcp.py` (~14 tool đăng ký trực tiếp): thêm `repo` cho các tool đọc trực tiếp (những tool chỉ proxy thì signature đã đến từ backend module — chỉ cần verify).
2. **Pass-through verify**: `_ProxyMiddleware` coerce theo schema backend nên tool proxy tự nhận repo sau Phase 02–04 — verify bằng test, không sửa code nếu test xanh (`unified_mcp.py:955-968`).
3. **Catalog**: `tool_metadata.py` — 34 entry đang mô tả `project_id`; thêm mô tả `repo` cho đúng các tool đã nhận param (entry nào không nhận → không thêm).
4. **Acceptance gate**: mở rộng `tests/test_unified_mcp_wrapper_signatures.py:90-111` — assert **mọi** tool query đọc trên unified surface expose `repo` trong JSON schema; tool metadata tool/write/project-context KHÔNG có (đảm bảo không lộ param vô nghĩa).

## Steps

1. Live-schema test mới: parametrize toàn bộ tool name từ registry → assert presence/absence `repo` theo 3 nhóm (query-read: có; metadata/write/project-context: không).
2. Sửa direct tools còn thiếu.
3. Catalog entries + docstring một dòng (`"Optional repo filter: exact or '/'-suffix match"`).

## Files

- `code-tiny/mcp/unified_mcp.py` (chỉ direct tools nếu cần)
- `code-tiny/mcp/tool_metadata.py`
- `tests/test_unified_mcp_wrapper_signatures.py` (mở rộng)

## Test scope

- Mapped acceptance criteria: acceptance gate toàn surface (plan.md RT#2 — tool thiếu repo = fail, không skip); catalog consistency (entry ↔ signature khớp).
- Expected size: **M** (~5–7 test functions, 1 parametrized chính + catalog check).
- Chạy: pytest file mở rộng + full unified wrapper test.

## Red-team notes

- Gate list "query-read" phải derive từ nguồn đáng tin (danh sách tool trong registry + nhóm loại trừ), không hardcode tên tool tràn lan — nếu hardcode thì gate tê liệt khi thêm tool mới. Đề xuất: marker/naming convention hoặc list rõ trong test kèm comment "thêm tool mới vào đây".
- Tool proxy trả schema từ backend: test phải gọi live schema (như pattern hiện có :90-111), không đọc signature Python (sai khi decorator đổi tên param).
