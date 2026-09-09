# Phase 05 — Docs + regression run

**Files:** `ReadMe.md`, `docs/specs/cli.md`, `code-tiny/tools/ReadMe.md`, `wiki/` (nếu có trang config tương ứng)

## 1. Docs

- `docs/specs/cli.md` — section config: thêm shape `ignore.folders`
  (top-level, áp cho cả code & doc sync), ví dụ JSON, ngữ nghĩa match
  (tên folder / fnmatch glob ở mọi độ sâu dưới scan root, bổ sung trên
  default, không un-ignore default), lưu ý env `CORTEX_EXTRA_IGNORE_DIRS`
  chỉ là kênh truyền nội bộ orchestrator→subprocess (người dùng cấu hình qua
  `dev init` / `dev ignore`, không cần set tay).
- `ReadMe.md` — bảng `dev init` (`:169-192`): thêm prompt ignore folders;
  thêm 3 lệnh `dev ignore add|remove|list` vào bảng lệnh.
- `code-tiny/tools/ReadMe.md` — section `_SCAN_SKIP_DIRS` (`:107-130`): ghi chú
  extension qua `CORTEX_EXTRA_IGNORE_DIRS` và điểm merge trong `scan_ignore.py`.
- `dev ignore --help` text khớp docs (viết trong phase-01, rà lại ở đây).

## 2. Regression run

Toàn bộ suite liên quan:

```
python -m pytest \
  tests/test_dev_init_storage_backend.py \
  tests/test_dev_init_graph_provider.py \
  tests/test_dev_ignore.py \
  tests/test_dev_sync_ignore.py \
  tests/test_dev_sync_reliability.py \
  tests/test_dev_lifecycle_commands.py \
  code-tiny/tools/common/test_scan_ignore.py \
  doc-tiny/test_ingest_input_ignore.py
```

Plus smoke end-to-end (thủ công, ghi kết quả vào `reports/`):

1. `dev init` project mẫu, nhập ignore `sandbox` → kiểm tra JSON config.
2. `dev sync code` (dry-run/full) → `sandbox/` không vào snapshot hash và
   không xuất hiện trong folder picker.
3. `dev sync doc` → `sandbox/notes.md` không được ingest.
4. `dev ignore add generated-tmp` → chạy lại sync, xác nhận áp dụng không cần init lại.

## 3. Done criteria

- Không có config ignore → mọi đường scan hành vi byte-for-byte như trước (test regression guard pass).
- Có config ignore → cả code sync (orchestrator + incremental_sync + analyzer) lẫn doc sync (incremental + full) đều bỏ qua.
- `dev ignore` thêm/xóa/list chạy được trên config env active, fail-closed khi chưa init.
