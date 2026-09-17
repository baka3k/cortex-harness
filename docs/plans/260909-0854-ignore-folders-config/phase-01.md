# Phase 01 — Config schema `ignore.folders` + init prompt + `dev ignore` command

**Files:** `cortex_harness/dev.py`, `tests/test_dev_ignore.py` (mới), `tests/test_dev_init_storage_backend.py`, `tests/test_dev_init_graph_provider.py`

## 1. Helper đọc/validate ignore list

Thêm vào dev.py (khu vực config helpers, cạnh `_source_projects:320`):

```python
def _ignore_folders(cfg: dict) -> tuple[str, ...]:
    """Read user-configured ignore folder patterns from active config.

    Entries are folder names or fnmatch globs matched at any depth.
    Tolerates missing key / wrong type; dedupes preserving order.
    """
    raw = (cfg.get("ignore") or {}).get("folders")
    if not isinstance(raw, (list, tuple)):
        return ()
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        if isinstance(item, str) and item.strip() and item.strip() not in seen:
            seen.add(item.strip())
            out.append(item.strip())
    return tuple(out)
```

## 2. Init prompt + ghi config

Trong `init()` (`dev.py:2632-2949`), sau phần chọn source folders:

- Prompt: `"Folders to ignore when scanning (comma-separated, glob allowed) [Enter = none]: "`
  — parse theo dấu phẩy, strip, bỏ entry rỗng.
- Re-init: default của prompt = các entry hiện có trong config cũ (pattern
  giống `test_reinit_remote_reuses_remote_defaults`).
- Thêm vào cfg dict tại `dev.py:2904-2929`:

```python
cfg["ignore"] = {"folders": list(ignore_folders)}
```

Chỉ ghi section khi list khác rỗng **hoặc** config cũ đã có key (re-init xóa
được entry cuối cùng → lưu `[]` thay vì drop key).

## 3. `dev ignore` command group

Group Click mới cạnh `sync_code` group (`dev.py:3148`), pattern theo
`sync_code add`:

- `dev ignore add <FOLDER>...` — merge vào `ignore.folders` của config env
  đang active (`_load_active_config` → sửa → `_save_config`); báo entry mới
  thực sự được thêm vs đã tồn tại.
- `dev ignore remove <FOLDER>...` — xóa exact-match; báo entry nào không tìm thấy.
- `dev ignore list` — in từng entry một dòng; thông báo "no ignore folders
  configured" khi rỗng.
- Cả 3 đều fail-closed với thông báo "Run 'dev init' first" khi chưa có config
  (tái sử dụng hành vi `_load_active_config`).
- Sau khi sửa, in gợi ý: thay đổi áp dụng cho lần `dev sync code`/`dev sync doc`
  tiếp theo.

## 4. Tests

`tests/test_dev_ignore.py` (mới, CliRunner pattern theo
`test_dev_init_storage_backend.py`):

- `test_init_persists_ignore_folders_to_config` — prompt nhập `"legacy, generated-*"` → config JSON có `ignore.folders == ["legacy", "generated-*"]`.
- `test_init_blank_ignore_prompt_writes_empty_or_absent_section` — Enter trống → không break, config không chứa entry rỗng.
- `test_reinit_keeps_existing_ignore_folders_as_default` — init lần 2, Enter trống tại prompt ignore → giữ list cũ.
- `test_ignore_add_merges_and_dedupes` / `test_ignore_remove_reports_missing` / `test_ignore_list_prints_entries`.
- `test_ignore_commands_fail_closed_without_init`.
- `test_ignore_folders_helper_tolerates_bad_types` — `ignore: null`, `folders: "str"`, entry non-string → `()`.

Thêm assert ignore section trong 1 test init hiện có để chống regression shape.
