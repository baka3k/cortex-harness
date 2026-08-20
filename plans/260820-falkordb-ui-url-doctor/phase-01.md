# Phase 01 — infra-up output: redis + Browser UI URL

## Mục tiêu
Khi ensure container `cortex-falkordb`, output hiện cả 2 endpoint thay vì chỉ
primary (UI). Qdrant giữ nguyên.

## Changes
File: `scripts/mcp-lifecycle.py`

1. Thêm helper module-level:
   ```python
   def falkordb_ui_port() -> int:
       """Host-side Browser UI port (env FALKORDB_UI_PORT, default 3000)."""
       raw = os.environ.get("FALKORDB_UI_PORT")
       try:
           return int(raw) if raw else 3000
       except ValueError:
           return 3000
   ```
   Refactor `_resolved_ports` để dùng chung fallback pattern (optional, nhỏ).
2. `_ensure_service`: sau dòng `[ok] ... running/created`, nếu spec có nhiều
   hơn 1 port (falkordb), in thêm dòng phụ:
   - port 6379 → `      redis : redis://127.0.0.1:6379`
   - port 3000 → `      UI   : http://127.0.0.1:3000 (Browser UI)`
   Cách gọn: thêm optional key `"labels"` vào port tuples hoặc in generic
   `[ok] {name} endpoints: redis://127.0.0.1:6379, Browser UI http://127.0.0.1:3000`.
   Chọn cách explicit cho falkordb bằng cách thêm `"ui_port": True` flag trên
   port tuple để tránh hardcode tên container trong `_ensure_service`.

## Acceptance
- `dev infra-up` với container đang chạy in:
  `[ok] cortex-falkordb running (http://127.0.0.1:3000)` **kèm** dòng Browser
  UI + redis URL rõ ràng.
- Container stopped / newly created path cũng in đủ (in sau khi state đạt
  running).

## Tests (`tests/test_docker_ensure.py`)
- `test_falkordb_output_includes_browser_ui_url`: fake_run với container
  running → stdout chứa `Browser UI` và `redis://127.0.0.1:6379`.
- `test_qdrant_output_has_no_ui_line`: qdrant không in dòng UI.
