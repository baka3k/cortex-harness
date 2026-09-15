# Phase 06 — Wave C: Entrypoint swap binary-only (10 artifacts) + CI rewrite

## Điều kiện tiên quyết

Phase 01–05 pass gate. Lên lịch **bên trong dogfood window** umbrella
(`docs/cutover-runbook.md`) — flip entrypoint là 1 điểm của runbook.

## Quyết định validate 2026-09-14: KHÔNG có python rollback

Entrypoint là **binary-only** từ phase này — flag `CORTEX_DEV_BACKEND` **không tồn tại**.
Downgrade path = cài lại release binary trước (installer giữ previous release); runbook ghi
2 bước downgrade. `dev.py` từ đây là parity-reference-only (không entrypoint nào trỏ tới).

## Scope — inventory đầy đủ 10 artifacts (rev 2, red-team #9)

| # | Artifact | Việc |
|---|---|---|
| 1 | `dev.sh` | exec binary theo D1; không tìm thấy → error + hint cài đặt |
| 2 | `dev.bat` | như trên (`rust\target\release\cortex-dev.exe`) |
| 3 | `dev.ps1` | như trên |
| 4 | `dev-global.cmd:5-11` (+ generator `install-windows.bat:63-71`) | exec binary |
| 5 | `install-windows.ps1:58-60` scoop shim (đang trỏ `.venv\Scripts\dev.exe`) | trỏ binary; bỏ `pip install -e` tại `:73` |
| 6 | `installers/windows/scripts/wrapper.bat:54` | exec binary |
| 7 | `installers/windows/inno_setup/cortex_harness.iss:83,88-89,211,220` | shortcuts exec binary thay `.venv\Scripts\python.exe … dev.py` |
| 8 | `pyproject.toml:37-38` console script `dev = cortex_harness.dev:cli` | xoá entry (binary thay; không tạo `dev-py` alias — không rollback) |
| 9 | Launcher generator (native `install` từ phase-05) | implement D1 — binary-only |
| 10 | `Makefile:12` `DEV` + `Makefile:3,7` `LIFECYCLE` | `DEV := <bin>`; `LIFECYCLE := <bin>`; `ort-ensure` → `<bin> ensure-ort` (phase-05); giữ `PYTHON` chỉ cho target parity/embed |

Path resolution binary cho end-user: `CORTEX_DEV_BIN` env → installed prefix → repo
`rust/target/release` → error + hint (D1).

## CI rewrite (red-team #8 — không làm CI sẽ đỏ ngay khi flip)

- Rewrite `tests/test_make_lifecycle.py:115,148,165` (assert dispatch python) → assert dispatch
  binary; `tests/test_dev_lifecycle_commands.py:141` tương tự.
- Workflow `lifecycle-macos.yml` trigger path `cortex_harness/dev.py` giữ (parity reference vẫn đổi).

## Runbook

- Cập nhật `docs/cutover-runbook.md:12-23` **tại phase này** (red-team #16): dev entry là
  binary; **không có python rollback**; downgrade = cài lại previous release binary
  (2 bước: uninstall → install release cũ, hoặc giữ `CORTEX_DEV_BIN` trỏ release cũ).

## Gate

- [ ] 10/10 artifacts swap binary-only; grep còn `dev.py` trong entrypoints → 0.
- [ ] Máy sạch không `.venv`: `dev init status doctor storage-init` + `make build install`
      (gồm native ensure-ort) chạy, không spawn python.
- [ ] Không tìm thấy binary (rename tạm): entrypoint báo error + hint, không rơi vào python.
- [ ] Downgrade path: cài lại previous release binary chạy được (test 1 lần trên 1 OS).
- [ ] Windows smoke: 3 artifacts Windows + inno shortcut launch OK, trước khi flip.
- [ ] CI suites rewritten xanh; parity harness vẫn pass (dev.py reference chạy độc lập).
- [ ] Bắt đầu dogfood 1 tuần (gộp window umbrella) — gate đóng khi sạch lỗi.

**Trạng thái:** ready (rev 2) — **DONE (code) 2026-09-15**; Windows smoke + dogfood còn (reports/phase-06.md)
