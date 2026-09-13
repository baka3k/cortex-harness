# Phase 03: Named-graph → sub-directory mapping + sibling read-only + inventory

## Context

Ladybug không có multi-named-graph trong 1 file (khác `select_graph(name)` của FalkorDB). Mỗi graph name = một database directory riêng. Driver giữ vai trò router: `execute_query(..., database="doc_graph")` → open/cache `Database(dir_for("doc_graph"))`. Sibling stores (fan-out đọc đa instance hiện dùng `additional_paths` qua socket redislite, `falkordb_driver.py:394-427`) chuyển sang mở `Database(read_only=True)` — Ladybug cho phép nhiều process mở read-only cùng lúc.

## Requirements

### `cortex_harness/storage/layout.py`
- Hàm canonical path: `ladybug_graph_dir(root, owner_role, graph_name) -> Path` = `<resolved>/<owner_role>.lbug/<sanitized-graph-name>/` (sanitize: reject path separators, giữ casefold — đối chiếu chuẩn đặt tên hiện có trong layout).
- `ResolvedStorage` thêm field `ladybug_code_path` / `ladybug_doc_path` (đường dẫn tới directory của graph code/doc), tính từ cùng root với `falkordb_code_path`.

### Driver routing (`ladybug_driver.py`)
- `_db_by_graph: dict[str, Connection]` cache; `database=` param ở mọi method route qua `_connection_for(database)` — tạo lazy: chưa có dir → cho read (lỗi "database not found" chuẩn hóa qua `is_database_not_found_error`), có write intent → bootstrap dir (Phase 04 hooks schema bootstrap tại đây).
- `additional_paths` → mỗi path mở `Database(path, read_only=True)` KHÔNG lease (như falkordb sibling, `falkordb_driver.py:394-427`); graph trùng tên → primary thắng.

### `cortex_harness/storage/migration.py`
- `_reopen_inventory` (dòng 47-65): branch `backend == "ladybug"` → open `Database(path, read_only=True)` + `CALL show_tables()` → tuple tên bảng; marker/digest logic giữ nguyên (một directory = một item, đối chiếu `_tree_digest` đã handle dir).

### `doc-tiny/graph_store.py`
- Bỏ trực tiếp `self._driver.driver.select_graph(self._database)` (dòng 99) — gọi qua driver method chuẩn (`execute_query(database=...)`, index qua `driver.create_indexes`); GraphStore nhận driver instance do storage factory cung cấp theo provider đang active.

### `scripts/mcp-lifecycle.py`
- Doctor probe (dòng ~1406 `select_graph("doctor")`): branch theo provider — ladybug thì probe `RETURN 1` trên directory doctor riêng hoặc chỉ primary connection; vẫn report đúng trạng thái.

## Implementation steps

1. layout.py: sanitize + path hàm + ResolvedStorage fields; update `tests/test_storage_layout.py`.
2. Driver routing + connection cache + sibling read-only; unit tests multi-graph isolation (graph A ghi không lộ sang graph B) và sibling trùng tên.
3. migration.py branch + test inventory với fake Database.
4. doc-tiny graph_store + mcp-lifecycle rewiring; chạy test doc-graph hiện hữu (`tests/test_doc_graph_store.py` — cập nhật fake shape).

## Acceptance

- Cùng 1 driver instance phục vụ ≥2 graphs với dữ liệu isolate hoàn toàn.
- `StorageLease` chỉ giữ trên primary write DB; sibling read-only không acquire lease (mirror `tests/test_mcp_sibling_no_lease.py` cho ladybug).
- `resolve_storage(...)` trả `ladybug_code_path` hợp lệ, deterministic giữa 2 lần gọi.
