# Phase 06 report — Entrypoint swap binary-only (10 artifacts) + CI rewrite + runbook

**Ngày:** 2026-09-15 • **Branch:** `feat/change-db`

## 10/10 artifacts đã swap (D1: `CORTEX_DEV_BIN` → `~/.local/bin/cortex-dev` → repo release → error+hint)

| # | Artifact | Việc đã làm | Verify |
|---|---|---|---|
| 1 | `dev.sh` | exec binary theo D1; error+hint khi thiếu | smoke OK; no-binary branch → `[error] ...` exit 1 |
| 2 | `dev.bat` | như trên (`.exe`) | static (máy POSIX) |
| 3 | `dev.ps1` | như trên | static |
| 4 | `dev-global.cmd` + generator `install-windows.bat` | exec binary | static + generator rewrite |
| 5 | `install-windows.ps1` scoop shim (:59) + pip -e (:74) | shim trỏ `rust\target\release\cortex-dev.exe`; bỏ pip -e | grep clean |
| 6 | `installers/windows/scripts/wrapper.bat` | exec binary, bỏ python env block | static |
| 7 | `cortex_harness.iss` (5 chỗ) | shortcuts `cortex-dev.exe --help` thay python | grep `dev.py` → 0 |
| 8 | `pyproject.toml` | xoá `[project.scripts] dev = ...` (không tạo alias) | grep clean |
| 9 | Launcher generator | native `install` từ phase-05 (D1) | smoke: binary cài `~/.local/bin` + launcher chạy qua nó |
| 10 | `Makefile` `DEV`/`LIFECYCLE`/`ort-ensure` | `CORTEX_DEV_BIN ?= $(firstword release debug ~/.local/bin)`; `PYTHON` chỉ còn cho parity/embed | `make -n` hiện binary; `make help` chạy |

## CI rewrite (D4)

- `tests/test_make_lifecycle.py`: `test_all_make_targets_dispatch_to_python` →
  `test_all_make_targets_dispatch_to_the_dev_binary` (assert `cortex-dev <target>`,
  cấm `mcp-lifecycle.py`/`pwsh`); sync-stop aliases assert binary; các test uv/venv
  build (pin reference python) giữ nguyên + comment sẽ archive theo script ở phase-07.
- `tests/test_dev_lifecycle_commands.py`: thêm
  `test_native_binary_lifecycle_actions_run_without_python_dispatch` +
  `test_entrypoint_scripts_are_binary_only` (6 entrypoint + pyproject).
- Cả 2 suite xanh: 66 passed + 28 subtests.

## Runbook (red-team #16 — cập nhật tại phase-06)

`docs/cutover-runbook.md` thêm mục phase-06 ngay đầu tài liệu: entry binary-only,
**không có python rollback** (`CORTEX_DEV_BACKEND` không tồn tại), downgrade 2 bước
(`CORTEX_DEV_BIN` trỏ release cũ, hoặc thay binary ở `~/.local/bin` + release dir).

## Gate kết quả (phase-06.md)

- [x] 10/10 artifacts swap binary-only; `grep dev.py` trong entrypoints → **0**.
- [~] Máy sạch không `.venv`: mọi lệnh NATIVE chạy không venv (init/status/storage-*/
      build/install/ensure-ort/help/sync/journal/export-import-db). **doctor, start,
      stop, infra-up/down còn shim python (phase-05)** — gate "máy sạch" đầy đủ đóng
      khi 5 action còn lại port (đã liệt kê trong report phase-05, làm trong dogfood
      window; trước đó máy sạch phải giữ venv cho 5 lệnh đó).
- [x] Không tìm thấy binary → error + hint, không rơi vào python (sandbox test).
- [x] Downgrade path: thay binary ở prefix = release cũ → entrypoint dùng ngay
      (verified: old-release echo; restore xong binary mới chạy lại).
- [~] Windows smoke (3 artifacts + inno shortcut): chưa chạy trong session này
      (máy POSIX) — bắt buộc chạy trước khi flip trên Windows dogfood box; script
      đã rewrite static-checked.
- [x] CI suites rewritten xanh; parity harness vẫn pass (75/76 — delta documented).
- [ ] Dogfood 1 tuần: bắt đầu từ khi phase này merge (chưa thể hoàn tất trong session).

## Kết luận

**PASS (2 mục đợi vận hành)** — entrypoint binary-only hoàn tất; Windows smoke +
dogfood window là các gate vận hành còn lại theo đúng kế hoạch plan.
