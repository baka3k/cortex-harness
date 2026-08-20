# Phase 03 — Tests tổng hợp + Docs

## Mục tiêu
Chạy full test suites liên quan, cập nhật docs.

## Tasks
1. Chạy:
   - `python -m pytest tests/test_doctor_remote.py tests/test_docker_ensure.py tests/test_infra_remote.py -q`
   - Smoke: `python scripts/mcp-lifecycle.py doctor` (local repo) — không
     regression ở các check hiện có.
2. `ReadMe.md` mục infra/ports: bổ sung dòng mô tả FalkorDB Browser UI
   (image `falkordb/falkordb:latest` đã kèm UI, mặc định
   `http://127.0.0.1:3000`, override `FALKORDB_UI_PORT`, doctor hiển thị URL
   cho remote project).
3. Cập nhật frontmatter các plan liên quan:
   - `260820-infra-up-docker-idempotent`: không đổi (completed, chỉ output).
   - Plan này giữ `relatedPlans` như hiện tại.

## Acceptance
- Toàn bộ tests pass.
- `ReadMe.md` phản ánh UI URL + env override.
