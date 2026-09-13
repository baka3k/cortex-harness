# Phase 05: Win32 default flip — platform resolution + deps + docs

## Context

Mục tiêu kinh doanh của cả plan: Windows không cần server. Sau phase 04 (driver đủ chức năng), phase này làm win32 resolve local mode → ladybug thay vì ép remote, và dọn các nhánh "graphless on win32".

## Requirements

### `cortex_harness/storage/config.py` (vùng dòng 478)
- Resolution matrix local mode:
  - `GRAPH_PROVIDER` set tường minh → theo provider đó (tất cả platform).
  - Không set: win32 → `ladybug`; khác → `falkordb` (giữ nguyên hành vi cũ).
  - `LocalPathConfig` comment "unavailable on win32" cập nhật.
- Path default win32: `LADYBUG_PATH` unset → cùng root convention với falkordb (`resolve_storage`), directory `<root>/code.lbug/hyper_graph`.

### `tools/graph/cli.py` (dòng ~222)
- Bỏ graphless-fallback điều kiện "FalkorDBLite may not even be installable on this platform (win32)" — ladybug có wheel win32; fallback giờ chỉ khi package chưa cài (mọi platform).

### `cortex_harness/dev.py`
- Review từng nhánh `sys.platform == "win32"` (197, 1947, 1996, 2063, 2076, 2474, 4726, 4883): chỉ giữ những cái liên quan shell/ANSI/procloop; các nhánh vô hiệu hóa graph trên win32 phải mở đường ladybug (xác định bằng grep "win32" context quanh từng dòng).

### Dependencies (`pyproject.toml`, 2 requirements.txt)
- `ladybug` đã vào dependencies chính (Phase 02).
- `falkordblite==0.10.0 ; sys_platform != 'win32'` + `falkordb ; sys_platform == 'win32'` → đổi thành optional extras: `falkordb-local = ["falkordblite"]`, gộp `falkordb` vào `falkordb-remote` (rollback path trên win32 = cài extra + `GRAPH_PROVIDER=falkordb` + remote URI).
- Ghi rõ trong ReadMe cách rollback.

### Doctor / lifecycle
- `scripts/mcp-lifecycle.py`: probe ladybug directory trên win32; nếu thấy config legacy `FALKORDB_URI` → report hướng dẫn (dùng remote rollback hoặc chuyển local ladybug).

### Docs
- ReadMe: Windows quickstart mới (không Docker); bảng env vars ladybug; platform support matrix (Windows 10/11 x64+ARM64, macOS 15+ per PyPI floor — verify lại bằng spike note, Linux glibc 2.26+).
- `code-tiny/Design.md`/`STRUCTURE.md`: KuzuDriver placeholder → LadybugDriver thực tế (cập nhật diagram dòng 79-91).

## Implementation steps

1. Resolution matrix + tests (monkeypatch `sys.platform` / env matrix: win32+falkordb-explicit, win32+default, mac+default, mac+ladybug-explicit).
2. cli.py + dev.py branch cleanup; chạy `python -c "import cortex_harness.dev"` smoke.
3. Deps extras restructure; `pip install -e .[falkordb-local]` verify path cũ còn hoạt động.
4. Doctor + ReadMe + Design docs.

## Acceptance

- Trên win32-mô-phỏng (tests): `resolve_storage` → ladybug local, không yêu cầu `FALKORDB_URI`.
- Trên macOS hiện tại (không đổi env): hành vi không đổi chút nào (falkordblite default) — regression suite xanh.
- `pip install -e .` trần (không extras) đủ chạy local trên cả 3 OS.

## Rollback plan

`GRAPH_PROVIDER=falkordb` (+ `FALKORDB_URI` remote trên win32, hoặc extra `falkordb-local` trên POSIX) — không cần revert code.
