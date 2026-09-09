# Phase 04 — doc-tiny: `_iter_input_files` directory excludes

**Files:** `doc-tiny/graphrag_ingest_langextract.py`, test mới

## 1. Thay đổi

- `_iter_input_files` (`doc-tiny/graphrag_ingest_langextract.py:639-655`) hiện
  chỉ lọc **file** (`~$` office temp + extension allowlist) trên
  `folder.rglob("*")` — không có directory exclude nào.
- Đổi sang duyệt prune được (hoặc filter theo parts, chọn cách ít xâm phạm):
  file bị loại khi **bất kỳ part nào** (ngoài root) match
  `COMMON_SCAN_EXCLUDE | extra_ignore_dirs()` — exact + fnmatch.
  - Tái sử dụng `code-tiny/tools/common/scan_ignore.py` nếu import được
    (doc-tiny và code-tiny cùng cây tools; kiểm tra sys.path khi chạy
    standalone — nếu import chéo không khả thi, copy pattern tối thiểu: hàm
    `_is_ignored_dir(name)` + đọc `CORTEX_EXTRA_IGNORE_DIRS`, và ghi chú mirror
    như comment trong `scan_ignore.py:25-26`).
  - Giữ nguyên lọc file-level hiện có (`~$`, extension).

## 2. Tests

Test mới `doc-tiny/test_ingest_input_ignore.py` (hoặc cạnh test hiện có của
doc-tiny nếu đã có thư mục test — rà trước khi đặt tên):

- `test_iter_input_files_skips_common_excluded_dirs` — cây tmp chứa
  `node_modules/x.md`, `.git/y.md`, `docs/keep.md` → chỉ `docs/keep.md`.
- `test_iter_input_files_skips_env_extra_ignore_dirs` — env
  `CORTEX_EXTRA_IGNORE_DIRS=archive` → `archive/old.md` bị loại.
- `test_iter_input_files_without_env_unchanged` — không env → kết quả như
  hành vi hiện tại (regression guard cho doc full-sync).
