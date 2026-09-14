# Phase 07 — Wave C: Cleanup/archive — xoá bridge, đóng vai trò parity-reference

## Điều kiện

Phase-06 dogfood 1 tuần sạch lỗi + 1 release ổn định với entrypoint Rust (chính sách phase-14
umbrella). Rollback python đã bỏ từ phase-06 (validate) — phase này chỉ archive phần còn lại.

## Scope

1. **Xoá `pyexec.rs`** (HELPER_SRC, `call_json`/`call_raw`/`try_call_json`, `venv_python`,
   `repo_file`) + mọi import còn sót. `repo_root()` nếu còn dùng hợp lệ → chuyển `util.rs`.
2. **Archive `scripts/mcp-lifecycle.py`** (2147 LOC) + **`scripts/ensure_ort.py`** (thay bằng
   native `ensure-ort` từ phase-05): move sang `scripts/archived/` sau khi 14/14 actions native
   ổn 1 release (red-team #15 — rev 1 nói "port rồi archive" nhưng không schedule).
3. **Xoá `scripts/mcp-lifecycle.ps1`** (1212 LOC): bản copy gần-full thứ hai của lifecycle;
   rollback Windows = release binary trước (không giữ vô thời hạn — cut-maximal).
4. **`cortex_harness/dev.py` → verify parity-reference-only**: header note đã đặt từ phase-06
   (không còn runtime path, chỉ phục vụ `scripts/rust_parity/dev_cli_parity.py` + golden
   fixtures). **Không xoá** — mọi thay đổi CLI Rust tương lai vẫn đối chiếu được với reference.
5. **Makefile dọn var**: xoá `DEV`; comment target export/import ghi rõ đi qua binary.
6. **Docs**: ReadMe, `INSTALLER_GUIDE.md`, `wiki/`, `docs/cutover-runbook.md` — end-state:
   "dev/make layer = Rust; Python = parity reference + forced list (torch probe, harness
   project-scripts, embed sidecar default)". Áp quyết định validate (ensure-ort đã native,
   rollback đã bỏ từ phase-06) vào docs.
7. **CI bridge-ban**: check `grep pyexec\|venv_python\|HELPER_SRC` trong
   `rust/crates/cortex-dev/` fail build nếu match (không tính whitelist đã ghi).

## Gate

- [ ] `grep -rn "pyexec\|venv_python\|HELPER_SRC" rust/crates/cortex-dev/src/` → 0 match.
- [ ] `scripts/mcp-lifecycle.py` + `ensure_ort.py` ở `scripts/archived/`; `scripts/mcp-lifecycle.ps1` đã xoá.
- [ ] `dev_cli_parity.py` vẫn pass (reference còn nguyên, binary đối chiếu được).
- [ ] CI bridge-ban hoạt động (unit test hoặc commit vết giả thấy fail).
- [ ] Docs nói đúng end-state + forced list; runbook ghi downgrade path (previous release binary).

**Trạng thái:** draft (rev 2 — trước đây là phase-06)
