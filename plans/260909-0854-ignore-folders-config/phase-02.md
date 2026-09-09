# Phase 02 — Thread effective exclude set qua dev.py sync flows + env var tại subprocess spawn

**Files:** `cortex_harness/dev.py`, `tests/test_dev_sync_ignore.py` (mới), `tests/test_dev_sync_reliability.py`

## 1. Param `extra_ignores` (default `frozenset()`)

Thêm param vào các helper duyệt filesystem,_backward-compatible:

- `_is_excluded_path(path, root, extra_ignores=frozenset())` (`dev.py:825-830`) —
  match `extra_ignores` cùng kiểu với `_SCAN_EXCLUDE`: intersection trên
  `rel.parts[:-1]` + fnmatch glob trên từng part (dựng `_EXCLUDE_PATTERNS` cached
  từ tuple để không compile fnmatch mỗi lần; tách helper `_match_ignore(name, patterns)`).
- `_discover_folders(..., extra_ignores=...)` (`dev.py:749-760`).
- `_detect_langs(..., extra_ignores=...)` (`:833`).
- `_mtime_changed_files(..., extra_ignores=...)` (`:982`).
- `_find_doc_files(..., extra_ignores=...)` (`:1012-1018`).
- `_detect_changed_docs(..., extra_ignores=...)` (`:1021-1065`).
- `_run_analyzer(..., extra_ignores=...)` — full-scan snapshot rglob `:1508-1514`.
- `_sync_folder(..., extra_ignores=...)` (`:1608`) và `_sync_doc_folder(..., extra_ignores=...)` (`:1076-1230`).

## 2. Nối vào sync commands

- `sync_code` (`dev.py:3201`): sau `_load_active_config` (`:3254`) tính
  `extra = frozenset(_ignore_folders(cfg))`; truyền vào `_discover_folders`
  (folder picker), `_run_analyzer`, `_sync_folder`; **set env var**
  `CORTEX_EXTRA_IGNORE_DIRS = ",".join(sorted(extra))` vào env dict dùng khi
  spawn `code-tiny/tools/sync/incremental_sync.py` (cmd/env build `:3289-3307`).
- `sync_doc` (`dev.py:3464-3528`): tương tự — `_find_doc_files`,
  `_detect_changed_docs`, `_sync_doc_folder`, và env cho spawn doc-tiny ingest
  (full mode).
- Warn một lần nếu một scan root được chọn match ignore pattern
  (`_match_ignore` trên tên scan root) — không chặn, chỉ cảnh báo mâu thuẫn config.

## 3. Tests

`tests/test_dev_sync_ignore.py` (mới; mock `_load_active_config` theo
`tests/test_dev_lifecycle_commands.py:371`):

- `test_is_excluded_path_respects_extra_ignores` — folder `legacy/` dưới root bị loại, folder thường không.
- `test_extra_ignores_support_fnmatch_glob` — `generated-*` khớp `generated-ui`.
- `test_discover_folders_hides_user_ignored_dir` — folder ignore không xuất hiện trong picker list.
- `test_find_doc_files_respects_extra_ignores` — doc sync incremental bỏ qua folder ignore.
- `test_mtime_changed_files_respects_extra_ignores`.
- `test_sync_code_passes_env_var_to_incremental_sync` — spawn env chứa `CORTEX_EXTRA_IGNORE_DIRS` đúng danh sách (mock subprocess).
- `test_sync_doc_passes_env_var_to_doc_ingest`.
- `test_no_config_ignore_keeps_behavior_identical` — không có `ignore.folders` → env var rỗng/không set, mọi helper hành vi như trước (regression guard).
- `test_scan_root_matching_ignore_warns_but_runs`.

Update `tests/test_dev_sync_reliability.py` nếu fixture scan-root bị đụng bởi
default param (không mong đợi, chỉ verify).
