# Dev/Make Entrypoint Cutover sang cortex-dev Binary — 2026-09-15

## Context

Plan `plans/260914-2259-dev-make-python-cutover/` (rev 2, đã red-team + validate):
sau khi umbrella `260913-2130-rust-full-migration` code-complete P01–P14, toàn bộ
luồng người dùng vẫn chạy qua source Python (`dev.sh` exec `dev.py`, Makefile gọi
`mcp-lifecycle.py`, binary Rust còn 15 op cầu Python qua `pyexec.rs`). Nguyên tắc
cut-maximal: chỗ nào Rust đã có → bỏ Python hoàn toàn; chỗ buộc mới giữ.

## Change

- **Phase 01–03 — hết cầu Python cho env/process/journal/db:**
  `rust/crates/cortex-dev/src/env.rs` (status/code/doc/mcp env qua
  `cortex_storage::resolve_storage`+`storage_overlay`), `procinfo.rs` (psutil →
  `ps`+libc::kill; fix parse ps right-align làm 775 dòng chỉ đọc được 55),
  `journalx.rs` (scan_scope_id + ProjectRunLock flock + purge flow),
  `db_transfer.rs` (`.cortexdb` bundle, tar system CLI + COPYFILE_DISABLE).
  `cortex-graph-core::Journal::purge_run` mới + **fix P0 mất dữ liệu**:
  `Journal::open` từng `File::create` (truncate journal có sẵn!) — giờ
  create-if-missing đúng `_precreate_database` (`rust/crates/cortex-graph-core/src/journal.rs:745`).
- **Phase 04 — `dev sync code/all` → binary `cortex-sync`** (`cmds/sync.rs:23`,
  xoá bảng `LANG_ANALYZERS` .py); doc ingest + journal-consumer tách module
  forced (`cmds/docsync.rs`, `journalx.rs:recover_required_lane`); fix gap
  group-option `Matches::merged` (parser.rs) — `sync code all/stop/add` trước đây
  không thấy `--project-dir` cấp group.
- **Phase 05 — 9/14 lifecycle actions native** (`cmds/lifecycle.rs`): help
  byte-identical, storage-init/layout/migrate/backup/stop, build = cargo
  workspace (`RUST_LINK_ENV` macOS) + `ensure-ort` native (ORT_DYLIB_PATH →
  cache → venv wheel → PyPI pin 1.29.0 + sha256), install/uninstall launcher D1.
  doctor/start/stop/infra-* còn shim python — lý do ghi trong reports/phase-05.md.
- **Phase 06 — 10/10 entrypoint binary-only** (dev.sh/bat/ps1, dev-global.cmd,
  wrapper.bat, inno ×5, scoop shim, pyproject, Makefile `CORTEX_DEV_BIN`);
  CI rewrite dispatch binary; runbook downgrade path (docs/cutover-runbook.md).
- **Phase 07 — xoá `pyexec.rs`** (`repo_root`/`harness_python` về util.rs),
  archive `ensure_ort.py` → `scripts/archived/`, **xoá `mcp-lifecycle.ps1`**,
  bridge-ban CI (`tests/test_rust_bridge_ban.py`), dev.py = parity-reference-only.
- **Review (PASS 9/10 sau 2 fix cycles):** C1 security — `import-db` giờ từ chối
  member không phải file/thư mục (tar `-tv` type gate, tương đương
  `filter="data"` của python — `db_transfer.rs:633`); parity gate có allowlist
  `RUST_ONLY_ROOT_SUBS` (76/76); synthetic-table discovery parity test; CI build
  debug+release.

## Impact

Risk level: **medium-high** (đổi entrypoint cho mọi dev/operator). `dev` +
`make <lifecycle>` giờ chạy binary `cortex-dev` — không python rollback ở tầng
entrypoint; downgrade = cài lại release binary trước (`CORTEX_DEV_BIN` hoặc thay
`~/.local/bin/cortex-dev`). Máy sạch không cần `.venv` cho các lệnh native;
doctor/start/stop/infra-* vẫn cần venv cho tới khi 5 shim action port xong.
Parity: env payload + status + storage-layout/migrate + help + journal/db ops
byte-khớp Python (harness `scripts/rust_parity/dev_cli_parity.py` **76/76**,
có GATE 6/7 mới); independent review PASS 9/10.

## Decision

- Cho `sync doc`, journal-consumer, torch probe, `.harness` scripts giữ Python —
  cortex-doc thiếu embedded-FalkorDB + vector stage; consumer live chưa có bản
  Rust (cortex-sync tự delegate required-lane); chỉ torch biết MPS/CUDA. Danh
  sách forced nằm ở reports/phase-04.md.
- `mcp-lifecycle.py` (POSIX) CHƯA archive — 5 shim action vẫn gọi; archive khi
  port xong (dogfood window). `ps1` xoá ngay (rollback Windows = release cũ).
- Entry binary-only KHÔNG python rollback (validate 2026-09-14) — downgrade =
  cài lại release, flag `CORTEX_DEV_BACKEND` không tồn tại.

## References

- plan: ./plans/260914-2259-dev-make-python-cutover/plan.md
- reports: ./plans/260914-2259-dev-make-python-cutover/reports/ (phase-01..07 + review.md)
- commits: 59330ba, 03c5b31, 5485526, 7c7e1ff, c01103e, 363e874, 97892f3, 0ec6a52, bb50d94
- parity harness: scripts/rust_parity/dev_cli_parity.py (GATE 6/7 mới)
- bridge-ban: tests/test_rust_bridge_ban.py
