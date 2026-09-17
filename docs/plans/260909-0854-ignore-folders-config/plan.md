---
title: "User-configurable ignore folders — config trong dev init, sync code/doc đọc config và bỏ qua"
status: active
created: 2026-09-09
mode: hi-plan --fast
scope: "cortex_harness/dev.py (init config schema + ignore prompt + dev ignore command + threading effective exclude set qua mọi sync flow), code-tiny/tools/common/scan_ignore.py (env-var merge), code-tiny/tools/sync/incremental_sync.py (_SKIP_DIRS), doc-tiny/graphrag_ingest_langextract.py (_iter_input_files), tests, docs"
relatedPlans:
  - 260821-2115-dev-sync-code-windows
  - 260820-dev-init-backend-selection
  - 260813-2152-code-sync-phase-modes
blockedBy: []
blocks: []
---

# User-configurable ignore folders

## Goal

Người dùng chủ động khai báo các folder muốn **không quét** (ignore) qua config
thay vì phải sửa code. Cụ thể:

1. `dev init` hỏi và lưu danh sách ignore folder vào config
   `.cortext-harness/config/{env}.json` (section mới `ignore.folders`).
2. Khi chạy `dev sync code` hay `dev sync doc`, tool đọc config và **bỏ qua** các
   folder này ở mọi điểm duyệt filesystem (orchestrator dev.py, incremental_sync,
   13 language analyzer, doc-tiny ingest).
3. Cung cấp lệnh `dev ignore add|remove|list` để add/bỏ ignore sau khi init mà
   không cần chạy lại `dev init` hay sửa JSON tay.

Nguyên tắc: ignore của người dùng là **bổ sung** trên tầng default đã có sẵn
(`_SCAN_EXCLUDE`, `COMMON_SCAN_EXCLUDE`, `_SKIP_DIRS`) — không thay thế, không
cho phép un-ignore default.

## Codebase findings (đã verify 2026-09-09)

### Config
- Config JSON per-env: `.cortext-harness/config/{env}.json`. Constants
  `HARNESS_CONFIG_DIR` `dev.py:64`, writer `_save_config` `dev.py:313-317`,
  loader `_load_active_config` `dev.py:289-310` (fail-closed nếu chưa init).
- Cấu trúc config hiện tại dựng tại `dev.py:2904-2929` (`active`, `project`,
  `storage_backend`, `remote?`, `code.{env,source}`, `doc.{env,source}`) —
  **chưa có key ignore nào**. Loader round-trip JSON keys tuỳ ý; project
  registry đọc mọi `*.json` và tolerate extra keys
  (`docs/PROJECT_REGISTRY.md:80-93`) → thêm key không cần migration.

### Điểm duyệt filesystem & ignore hiện có (điểm cắm)
- **Shared ignore helper (code-side)**: `code-tiny/tools/common/scan_ignore.py`
  — `COMMON_SCAN_EXCLUDE` (~90 entries, có hỗ trợ fnmatch glob qua
  `_matches_pattern:171`), `is_excluded_dir:156`, `has_excluded_parent:179`,
  `filter_paths:198`. **Cả 13 analyzer import từ đây** (go/js/python/rust/java/
  cplus/kotlin/php/flutter/swift/csharp/plsql + ts/utils/file_utils).
- **Orchestrator**: `_SCAN_EXCLUDE` `dev.py:256-274` (dev.py-side mirror, gồm
  `*.egg-info` glob), tiêu thụ qua:
  - `_discover_folders` `dev.py:749-760` (folder picker cho sync_code) —
    filter bằng `set(rel.parts).intersection(_SCAN_EXCLUDE)`.
  - `_is_excluded_path` `dev.py:825-830` — dùng tại `_detect_langs:839`,
    `_mtime_changed_files:982`, `_find_doc_files:1012-1018`,
    `_run_analyzer:1508-1514` (full-scan snapshot hash).
  - `.gitignore` seeding `dev.py:940-943` từ `sorted(_SCAN_EXCLUDE)`.
- **Code-sync subprocess**: `code-tiny/tools/sync/incremental_sync.py` —
  `_SKIP_DIRS` `:1790-1795`, prune tại `_walk_all_source_files:1798-1819`
  (os.walk topdown + `not d.startswith(".")`).
- **Doc-sync**: incremental detection đi qua `_find_doc_files` (đã nêu). Full
  mode dev.py truyền `--folder` sang `doc-tiny/graphrag_ingest_langextract.py`
  — `_iter_input_files:639-655` chỉ lọc file (`~$` + extension), **không có
  directory exclude nào** (gap).
- **Sync entrypoints**: `sync_code` `dev.py:3201` (load config `:3254`, chọn
  folder `_select_folders_interactive:1456`, build cmd/env spawn
  incremental_sync `:3289-3307`); `sync_doc` `dev.py:3464-3528` (load config
  `:3454`); helpers `_sync_folder:1608`, `_sync_doc_folder:1076-1230`,
  `_detect_changed_docs:1021-1065`.
- **Analyzer local fallback lists**: đa số analyzer có list cục bộ chỉ dùng khi
  import `scan_ignore` thất bại; một số nơi dùng list riêng làm primary
  (java_analyzer fnmatch `:54` prune `:1492`, `android_common._ANDROID_SKIP_DIRS:142`,
  `vb_analyzer_base._SKIP_DIRS:69`, các pipeline nhỏ perl/shell/jp1/database_schema/
  web_framework/servlet_jsp/mybatis/spring/struts/flutter dart_parser).

### Tests hiện có (mẫu để mở rộng)
- Init config generation: `tests/test_dev_init_storage_backend.py` (prompt-tail
  pattern `_LOCAL_TAIL`), `tests/test_dev_init_graph_provider.py`.
- scan_ignore: `code-tiny/tools/common/test_scan_ignore.py`.
- Sync flow: `tests/test_dev_sync_reliability.py`,
  `tests/test_dev_lifecycle_commands.py:371` (mock `_load_active_config`).
- **Gap**: chưa có test nào cho `_SCAN_EXCLUDE`/`_is_excluded_path`/
  `_find_doc_files`/`_discover_folders`.

## Thiết kế

### 1. Config schema (quyết định)
Thêm section top-level vào config, áp dụng cho **cả** code và doc sync:

```json
{
  "ignore": {
    "folders": ["legacy", "generated-*", "sandbox", "third_party"]
  }
}
```

- Mỗi entry = **tên folder** (exact) hoặc **fnmatch glob** (`generated-*`,
  `*.tmp-out`), match theo tên folder ở **mọi độ sâu** dưới scan root — cùng
  ngữ nghĩa với `_SCAN_EXCLUDE`/`COMMON_SCAN_EXCLUDE` hiện có (không hỗ trợ
  path tương đối `a/b` ở v1; ghi rõ trong docs).
- Merge: effective excludes = defaults ∪ user list. User chỉ **add**, không
  remove default.
- Tách helper đọc config: `_ignore_folders(cfg) -> tuple[str, ...]` trong dev.py
  (tolerate missing/`None`/kiểu sai, dedupe, giữ nguyên thứ tự khai báo).

### 2. Propagation (3 kênh)
1. **In-process dev.py** (chỉ orchestrator): tính effective set
   `_SCAN_EXCLUDE | frozenset(user_folders)` một lần trong `sync_code`/
   `sync_doc`, thread xuống qua param `extra_ignores` (default `frozenset()`)
   của `_discover_folders`, `_is_excluded_path`, `_detect_langs`,
   `_mtime_changed_files`, `_find_doc_files`, `_detect_changed_docs`,
   `_run_analyzer`, `_sync_folder`, `_sync_doc_folder`. Backward-compatible
   (default param) nên caller ngoài sync flow không vỡ.
2. **Env var qua boundary subprocess** (incremental_sync, analyzer, doc-tiny):
   `CORTEX_EXTRA_IGNORE_DIRS` — comma-separated. dev.py set vào env khi spawn
   incremental_sync (`:3289-3307`) và doc-tiny ingest trong `_sync_doc_folder`;
   subprocess con kế thừa tự động.
3. **Thư viện consumers**: `scan_ignore.py` đọc env var (lazy, cached) và merge
   vào effective set dùng bởi `is_excluded_dir`/`has_excluded_parent`/
   `filter_paths` → 13 analyzer nhận ignore không cần sửa từng file.
   `incremental_sync._SKIP_DIRS` merge thêm tại prune site `_walk_all_source_files`.
   doc-tiny `_iter_input_files` prune directory cùng env var.

### 3. CLI surface
- `dev init`: prompt mới (sau phần source folder): "Folders to ignore when
  scanning (comma-separated, glob allowed) [Enter = skip]" → ghi `ignore.folders`.
  Re-init giữ giá trị cũ làm default (pattern như `test_reinit_remote_reuses_remote_defaults`).
- `dev ignore add <folder>...` / `dev ignore remove <folder>...` /
  `dev ignore list`: sửa trực tiếp `ignore.folders` của config env đang active
  qua `_load_active_config`/`_save_config`. Group command cùng pattern với
  `sync_code add`.

### 4. Non-goals
- Không cho phép un-ignore các default excludes.
- Không thêm user ignores vào `.gitignore` seeding (`dev.py:940-943` giữ nguyên
  `_SCAN_EXCLUDE` — ignore quét ≠ ignore git).
- Không hỗ trợ ignore theo **file** pattern hay path tương đối `a/b` (v1).
- Không đụng `SENSITIVE_PATTERNS` (file-level, `dev.py:137-143`).
- Không thay đổi hành vi Windows plumbing của
  `260821-2115-dev-sync-code-windows` (chỉ cùng file, khác khu vực; thêm env
  var vào spawn env là additive).

### 5. Edge cases
- Ignore entry trùng/shortcut là **scan root đã chọn**: tại sync time, warn nếu
  một scan root được chọn bị match bởi ignore list (scan root vẫn chạy — user
  đã chọn tường minh; warn để lộ cấu hình mâu thuẫn).
- Folder picker (`_select_folders_interactive`) tự động ẩn folder bị ignore vì
  `_discover_folders` filter trước.
- `CORTEX_EXTRA_IGNORE_DIRS` rỗng/không set → hành vi byte-for-byte như hiện tại
  (điều kiện bắt buộc của pattern "default param + env optional").

## Phases

| Phase | File | Nội dung |
|-------|------|----------|
| [01](phase-01.md) | dev.py, tests | Config schema `ignore.folders` + init prompt + `dev ignore add/remove/list` |
| [02](phase-02.md) | dev.py, tests | Thread effective exclude set qua sync code/doc in-process + set env var tại subprocess spawn |
| [03](phase-03.md) | scan_ignore.py, incremental_sync.py, tests | Env-var merge trong shared scan_ignore + `_SKIP_DIRS` |
| [04](phase-04.md) | graphrag_ingest_langextract.py, tests | doc-tiny `_iter_input_files` directory excludes |
| [05](phase-05.md) | docs, full test run | Docs (ReadMe, docs/specs/cli.md, code-tiny/tools/ReadMe.md) + regression run |

Thứ tự tuỳ ý chạy 01→05 tuần tự; 03/04 độc lập với 02 nhưng cần schema ở 01 để
test end-to-end.

## Validation

- `python -m pytest tests/test_dev_init_storage_backend.py tests/test_dev_init_graph_provider.py tests/test_dev_ignore.py tests/test_dev_sync_reliability.py tests/test_dev_lifecycle_commands.py` (mới + cũ)
- `python -m pytest code-tiny/tools/common/test_scan_ignore.py` + test mới cho incremental_sync & doc-tiny
- Smoke: init project mẫu với `ignore.folders = ["sandbox"]`, `dev sync code` +
  `dev sync doc` dry-run xác nhận folder `sandbox` không vào snapshot/hash list
  và không bị pick trong folder picker.
