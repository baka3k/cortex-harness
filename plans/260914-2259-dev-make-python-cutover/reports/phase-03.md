# Phase 03 report — Journal + db_transfer native (journal_status / journal_purge / db_export / db_import)

**Ngày:** 2026-09-15 • **Branch:** `feat/change-db`

## Đã làm

1. **`cortex-graph-core::journal`** — thêm `Journal::purge_run(run_id, ownership_confirmed)`
   (port `SQLiteJournal.purge_run`: ownership gate → terminal-status check → retention
   check (`parse_iso_to_epoch` mới, inverse của `iso_from_epoch`) → fenced-batch check →
   `run_purge_started` event → xoá artifact files qua `ArtifactStore::path_for` → DELETE run row)
   và **fix P0 data-loss**: `Journal::open` trước đây dùng `File::create` (truncate
   journal hiện có!); Python `_precreate_database` là `O_RDWR|O_CREAT` không truncate —
   đã sửa thành create-if-missing. Bug này do gate positive-purge bắt được (run row biến
   mất sau khi mở journal có sẵn).
2. **Module mới `cortex-dev/src/journalx.rs`** — `canonical_root` + `scan_scope_id`
   (sha256 `\0`-join, [:24]) + `ProjectRunLock` (flock LOCK_EX|LOCK_NB poll, metadata
   blob on-disk giống cortex-sync port — tương thích với lock của Python sync worker) +
   `status_payload` (RunSummary → JSON 20 keys y `inspect_journal`) + flow `purge`
   nguyên văn chuỗi validation dev.py (scope-path check, lock timeout=0, get_run,
   metadata match, safe-parser filename check) với đúng các error line + exit code.
3. **Module mới `cortex-dev/src/db_transfer.rs`** — port `cortex_harness/db_transfer.py`:
   nearest-dev.json resolution, `_resolve_targets`, local-only guard (remote → error),
   staging `.cache/db-transfer`, lanes code|doc|both, manifest sort-keys + created_at,
   backup-when-overwrite (`{name}.{stamp}.bak`), tar gz qua **system `tar` CLI**
   (COPYFILE_DISABLE=1 chặn AppleDouble `._*` members của bsdtar), stdout `[ok] ...` byte-shape Python.
4. **`cmds/journal.rs` + `cmds/db.rs`** — rewrite gọi native; `pyexec` op callers về 0.

## Gate kết quả (phase-03.md)

- [x] Round-trip `export-db` → `import-db --overwrite` **cross-side** (Rust export đọc lại
      bằng Python import và ngược lại) trên fixture `CORTEX_DATA_HOME` isolate: manifest
      + payload files byte-khớp, backup hoạt động. `[ok]` line khớp format Python
      (`project_id='dbx_proj'`, `(N bytes)` comma-grouped).
- [x] `journal status` JSON + text **byte-khớp** Python trên synthetic journal (schema v3,
      1 drained run + artifact); error branch `incompatible_schema` (user_version=0) cũng
      khớp từng ký tự.
- [x] Purge nhánh lỗi: `run does not exist`, `path does not match the exact project/root
      scope`, `metadata does not match` — stderr line + exit 1 khớp Python từng ký tự.
- [x] Positive purge (scope khớp): rc=0, JSON `{"event_type": "journal_purged", ...,
      "removed_artifacts": 1}` khớp; runs_left=0; artifact file bị xoá cả 2 phía.
- [x] (Lock-busy branch: `ProjectRunLock.acquire` timeout=0 → busy line; đã verify cấu
      trúc — python sync worker giữ lock cùng format metadata.)
- [x] `grep "call_json\|call_raw\|try_call_json" rust/crates/cortex-dev/src/cmds/` → **0 match**.
- [x] `cargo test -p cortex-dev` 23/23; parity harness 75/76 (chỉ còn delta `migrate` pre-existing).

## Delta / ghi nhận

- Journal path/root không tồn tại: Python in traceback (uncaught OSError); Rust in
  `Error: ...` + exit 1 (không traceback) — chấp nhận, ghi vào parity exception.
- Tar bundle: tạo qua system tar thay vì `tarfile` — member list có thể khác thứ tự
  nhưng manifest + files tương thích 2 chiều (đã test cross-read bằng python tarfile).
- `Journal::open` fix truncate là thay đổi graph-core dùng chung (cortex-sync) —
  hành vi mới đúng theo Python `_precreate_database`.

## Kết luận

**PASS** — 4 op journal/db hết caller Python-bridge; gate grep cmds/ về 0.
