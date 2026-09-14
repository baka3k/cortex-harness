# Phase 03 — Wave A: Journal + db_transfer native (`journal_status`, `journal_purge`, `db_export`, `db_import`)

## Scope

1. **`journal_status`** → `cmds/journal.rs`: port `inspect_journal()` của `dev.py` — đọc
   SQLite journal, dựng run summaries (JSON schema y HELPER_SRC `pyexec.rs:241-251`,
   error codes `JournalError.code.value` giữ nguyên chuỗi).
2. **`journal_purge`** → port khối validation của HELPER_SRC `pyexec.rs:252-317` nguyên văn:
   `scan_scope_id`, path-scope check (`resolved.parent == cache_root/graph-write-journal/scope_id`),
   `ProjectRunLock` (timeout=0, busy → exit 1 với đúng error line), filename-parser check,
   `purge_run(ownership_confirmed=True)`. Lock primitive dùng sẵn của Rust (fs2/fd-lock —
   pattern đã học ở umbrella storage lease).
3. **`db_export` / `db_import`** → port `cortex_harness/db_transfer.py` (archive
   `.cortexdb`, role code|doc|both, `--overwrite`, `DbTransferError` → `[error]` line + exit 1)
   thành module `src/db_transfer.rs`; `cmds/db.rs` gọi native.

## Work item chung (D2)

- Mở rộng `dev_cli_parity.py`: fixture `journal status|purge` (kèm 4 nhánh lỗi) +
  `export-db → import-db` round-trip (so member list + sizes).

## Gate

- [ ] Round-trip `dev export-db` → `dev import-db --overwrite` trên stock: archive byte-level
      so được với Python export (tar/zip member list + sizes khớp).
- [ ] `dev journal status` + `dev journal purge` trên journal thật của stock: output khớp
      Python; các nhánh lỗi (path sai scope, run không tồn tại, filename sai parser, lock busy)
      → đúng stderr line + exit code.
- [ ] Purge khi sync đang chạy (giữ lock) → bị chặn với đúng error.
- [ ] Parity harness mở rộng (D2) pass; `cargo test -p cortex-dev` pass.
- [ ] Sau phase này: `grep "call_json\|call_raw\|try_call_json" rust/crates/cortex-dev/src/cmds/`
      → 0 match (toàn bộ caller đã native; HELPER_SRC xoá ở phase-07).

**Trạng thái:** draft (rev 2)
