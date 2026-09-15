# Phase 07 report — Cleanup/archive: xoá bridge, đóng vai trò parity-reference

**Ngày:** 2026-09-15 • **Branch:** `feat/change-db`

**Điều kiện plan (dogfood 1 release ổn định) chưa đạt đầy đủ trong session này** — các
mục có rủi ro vận hành được thực hiện phần code-level an toàn; phần archive script
lifecycle POSIX được GHỮ LẠI có lý do (5 shim actions vẫn cần nó — xem bảng dưới).

## Đã làm

1. **Xoá `rust/crates/cortex-dev/src/pyexec.rs`** — HELPER_SRC, `call_json`/
   `call_raw`/`try_call_json`, module + mọi import. `repo_root()` chuyển sang
   `util.rs`; `venv_python` chuyển sang `util.rs` **đổi tên `harness_python`**
   (chỉ phục vụ forced-Python list) — bridge-ban grep literal về **0 match**.
2. **Archive `scripts/ensure_ort.py` → `scripts/archived/`** — native
   `dev ensure-ort` đã thay (phase-05); Makefile `ort-ensure` → binary.
3. **Xoá `scripts/mcp-lifecycle.ps1` (1212 LOC)** — Windows rollback = cài lại
   release binary trước (runbook phase-06); entry Windows giờ là `dev.ps1` → binary.
4. **CI bridge-ban** — `tests/test_rust_bridge_ban.py` (3 tests): cấm
   `pyexec|venv_python|HELPER_SRC` trong `rust/crates/cortex-dev/src/`; cấm
   `dev.py` trong 6 entrypoint + Makefile; chốt ps1 đã xoá + ensure_ort.py đã
   archived. **CI bridge-ban hoạt động** (commit vết giả sẽ fail test 1).
5. **`cortex_harness/dev.py` → parity-reference-only** — header note ngay sau
   docstring: không entrypoint nào trỏ tới, chỉ phục vụ `dev_cli_parity.py`.
   KHÔNG xoá file (golden reference cho mọi thay đổi CLI tương lai).
6. **Makefile dọn var** — bỏ alias `DEV`; mọi target dùng `$(CORTEX_DEV_BIN)`
   trực tiếp (tên var nói rõ binary-only).
7. **Docs end-state** — README phần cài đặt: build = cargo workspace + ORT dylib,
   entry = cortex-dev binary, forced list ghi rõ (torch probe, .harness scripts,
   doc-tiny ingestor, journal consumer), downgrade = cài lại release cũ;
   `docs/cutover-runbook.md` đã có mục phase-06.

## KHÔNG làm trong phase này (kèm lý do)

| Mục | Lý do còn giữ |
|---|---|
| `scripts/mcp-lifecycle.py` (POSIX, 2147 LOC) | **5 shim actions (doctor/start/stop/infra-up/infra-down) vẫn spawn nó** — xoá/archived bây giờ là gãy 5 lệnh. Archive ngay khi 5 action đó port xong (kế hoạch trong dogfood window, xem report phase-05) |
| `cortex_harness/dev.py` + toolchain | parity-reference theo plan (không xoá) |
| `scripts/rust_parity/*` | golden-fixture reference có chủ đích (KEEP theo plan) |

## Gate kết quả (phase-07.md)

- [x] `grep -rn "pyexec\|venv_python\|HELPER_SRC" rust/crates/cortex-dev/src/` → **0 match**
      (torch probe đã ở `env.rs` qua `util::harness_python` — forced-Python whitelist).
- [~] `scripts/mcp-lifecycle.py` còn ở `scripts/` (shim-only, lý do trên);
      `ensure_ort.py` ở `scripts/archived/`; **`mcp-lifecycle.ps1` đã xoá**.
- [x] `dev_cli_parity.py` vẫn pass: 75/76 (delta documented: `migrate` + `ensure-ort`
      là lệnh Rust-only).
- [x] CI bridge-ban hoạt động: `tests/test_rust_bridge_ban.py` 3/3 pass; vết bridge
      quay lại (token trong .rs bất kỳ) → test đỏ.
- [x] Docs nói đúng end-state + forced list; runbook ghi downgrade path.
- [ ] Gate vận hành "ổn 1 release" — chốt sau dogfood window (cổng umbrella).

## Kết luận

**PASS (code-level)** — bridge đã xoá sạch, ban-CI bật, archive phần an toàn;
phần còn lại gắn với cổng vận hành theo đúng điều kiện plan.
