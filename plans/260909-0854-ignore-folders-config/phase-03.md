# Phase 03 — code-tiny: env-var merge trong `scan_ignore.py` + `incremental_sync._SKIP_DIRS`

**Files:** `code-tiny/tools/common/scan_ignore.py`, `code-tiny/tools/sync/incremental_sync.py`, `code-tiny/tools/common/test_scan_ignore.py`, test mới cho incremental_sync

## 1. `scan_ignore.py` — một điểm thay đổi phục vụ cả 13 analyzer

Env var: `CORTEX_EXTRA_IGNORE_DIRS` (comma-separated; entry rỗng bị bỏ qua).

- Thêm `extra_ignore_dirs() -> frozenset[str]`: parse `os.environ.get("CORTEX_EXTRA_IGNORE_DIRS", "")`, lru_cache; cung cấp `reset_extra_ignore_dirs()` cho test.
- Effective set: `EFFECTIVE_SCAN_EXCLUDE = COMMON_SCAN_EXCLUDE | extra_ignore_dirs()`
  được tính **lazy** (first access), KHÔNG mutate `COMMON_SCAN_EXCLUDE` frozenset
  (nhiều module đã import trực tiếp constant này).
- Sửa các entry-point `is_excluded_dir:156`, `has_excluded_parent:179`,
  `filter_paths:198` (+ `_matches_pattern:171`) để match trên effective set
  (exact + fnmatch glob như hiện có).
- Fallback an toàn: env lỗi/không set → behavior byte-for-byte như cũ.

## 2. `incremental_sync.py`

- `_SKIP_DIRS` (`:1790`) giữ nguyên là default; tại prune site
  `_walk_all_source_files:1798-1819` merge:
  `skip = _SKIP_DIRS | extra_ignore_dirs()` (import từ `tools/common/scan_ignore`
  — module này đã cùng repo tools, import path như các analyzer dùng).
  Blanket rule `not d.startswith(".")` giữ nguyên.

## 3. Analyzer local fallback lists

Rà 13 analyzer: nơi dùng list cục bộ làm **fallback** khi import
`scan_ignore` thất bại → giữ nguyên (không còn path nào chạy được nếu import
fail). Nơi dùng list riêng làm **primary** (java_analyzer fnmatch `:54`/prune
`:1492`, `android_common._ANDROID_SKIP_DIRS`, `vb_analyzer_base._SKIP_DIRS`,
các pipeline nhỏ perl/shell/jp1/database_schema/web_framework/servlet_jsp/
mybatis/spring/struts/flutter dart_parser) — mỗi site merge
`extra_ignore_dirs()` vào prune condition (1 dòng/site), hoặc chuyển hẳn qua
helper nếu tính chất cho phép. Mục tiêu: không còn lối prune nào đi vòng qua
ignore list của user.

## 4. Tests

- `code-tiny/tools/common/test_scan_ignore.py` — thêm:
  - `test_extra_ignore_dirs_from_env` (set env → `is_excluded_dir("sandbox")` True; reset sạch).
  - `test_extra_ignore_glob_from_env` (`CORTEX_EXTRA_IGNORE_DIRS=gen-*` khớp `gen-ui`).
  - `test_common_scan_exclude_not_mutated_by_env` (frozenset gốc bất biến).
  - `test_filter_paths_and_has_excluded_parent_respect_extra`.
- Test mới `code-tiny/tools/sync/test_incremental_sync_ignore.py`:
  `test_walk_all_source_files_skips_env_ignored_dirs` (cây tmp với folder ignore,
  monkeypatch env, assert file trong folder ignore không nằm trong kết quả;
  không set env → như cũ).
- Smoke import: chạy 1 analyzer với env var set trên fixture nhỏ để xác nhận
  đường scan phụ huynh nhận merge (python_analyzer là nhanh nhất).
