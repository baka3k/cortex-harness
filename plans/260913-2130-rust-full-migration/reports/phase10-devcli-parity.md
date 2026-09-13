# Phase 10 (Scope A) — dev CLI parity (cortex_harness/dev.py → cortex-dev)

- chạy: 2026-09-14
- python ref: `cortex_harness/dev.py` (5.167 dòng, 21 lệnh cấp 1 + nhóm con, Click)
- rust: `rust/crates/cortex-dev` (binary `cortex-dev`, cài đặt như `dev`)
- parity harness: `scripts/rust_parity/dev_cli_parity.py`
- binary test: `rust/target/debug/cortex-dev` (build từ workspace manifest)

## Kết quả tổng

| Gate | Nội dung | Kết quả |
|---|---|---|
| GATE 1 | Option surface của MỌI lệnh (55 path, gồm root + nhóm con): parse `--help` 2 bên, so tên option/alias + danh sách subcommand | **PASS 55/55** |
| GATE 2 | `status` / `doctor` / `storage-layout` chạy trên fixture project (so cấu trúc dòng sau khi chuẩn hoá path/timestamp/duration/pid) | **PASS 3/3** |
| GATE 3 | `dev init` trên 2 tmp dir sạch: danh sách file scaffold + nội dung file + `dev.json` byte-tương đương + active-flip (prod.json → inactive) | **PASS 5/5** |
| GATE 4 | `ignore add/list/remove` (thêm, trùng, xoá, xoá-missing): stdout + config sau mỗi bước | **PASS 1/1** |
| GATE 5 | `sync code/doc --help` + `sync code all --help` | **PASS 3/3** |
| **Tổng** | | **67/67 PASS** |

Kiểm tra bổ sung ngoài gate (so tay, không nằm trong harness):

- `sync code --dry-run`: dòng `[dry-run] <cmd>` **giống hệt** py từng token — cùng python venv,
  cùng `code-tiny/tools/sync/incremental_sync.py`, cùng tham số; token summary
  `dev_<sha256[:16]>_<pid>_<uuid>.json` khớp cả phần sha256 (uuid/pid là volatile theo thiết kế).
- `sync doc --dry-run`: stdout **giống hệt** sau khi normalise tmp-dir name.
- `mcp-gates`: stdout **giống hệt** (40 dấu `─`, giá trị gate).
- `cargo test -p cortex-dev`: 23/23 (parsing argparse-equivalent + config resolution +
  fnmatch + MD5 vectors + storage identity/aliases + `is_local` + state-key md5).
- `cargo clippy -p cortex-dev --all-targets -- -D warnings`: sạch.

Ghi chú GATE 5: chạy sync end-to-end được **cố ý bỏ qua** ở đây — `dev sync code` chỉ là
thin-shell gọi chính orchestrator Python (`incremental_sync.py`) với cùng tham số; parity
end-to-end thuộc phase-09 orchestrator harness. Env `CORTEX_RUST_ANALYZER=rust` hoán đổi
analyzer bên trong orchestrator Python nên không cần thay đổi ở lớp dev CLI.

## Kiến trúc port

- **Parser tự viết tương đương Click** (`src/parser.rs` + `src/tree.rs` + `src/help.rs`):
  cây lệnh khai báo tĩnh 1:1 với decorator Click của dev.py — tên lệnh, option/alias
  (`--database, --db`, `--verbose / --no-verbose`), choices, IntRange, positional variadic,
  option-nhóm phải đứng trước subcommand, exit code 2 cho usage-error / bare-group, 0 cho `--help`.
- **Config resolution thuần Rust** (`src/config.rs`): `.cortext-harness/config/*.json` sorted,
  active-project semantics + warn fallback, `_deactivate_other_envs`, `_source_projects`
  (legacy flat + new projects), ignore folders dedupe, `_graph_provider` (alias falkor/lbug/kuzu),
  `save_config` — `json.dump(indent=2, ensure_ascii=False)` byte-tương đương (serde_json
  `preserve_order` giữ thứ tự key chèn).
- **Shell-out đúng chỗ dev.py shell-out / dùng Python layer** (`src/pyexec.rs`, 1 helper
  dispatcher duy nhất chạy bằng `.venv/bin/python`):
  - `help/build/install/uninstall/infra-up/infra-down/storage-init/storage-layout/
    storage-migrate-layout/storage-backup/storage-stop/start/stop/doctor` →
    `scripts/mcp-lifecycle.py` đúng như `_run_lifecycle` (cwd=caller cho start/doctor,
    cwd=REPO cho phần còn lại, exit code propagate).
  - Môi trường process của sync/status/mcp (`_code_env_for_process`, `_doc_env_for_process`,
    `_mcp_env_from_config`) → gọi thẳng các hàm của dev.py (resolve_storage + storage_overlay +
    effective-topology fingerprint) để parity tuyệt đối cho tới khi lớp storage Rust riêng landed.
  - `journal status/purge` → `inspect_journal` / `SQLiteJournal` + `ProjectRunLock` (dev.py imports).
  - `export-db/import-db/export/import` → `cortex_harness.db_transfer.export_project/import_project`.
  - sync lifecycle (`stop_sync_processes`, `stop_embedded_falkordb`, `_mcp_pids/_mcp_stop_pattern/
    _mcp_start_one`) → helper; ngoài ra MCP pause/lock orchestration, run-lock (`File::try_lock`,
    thay portalocker), retry-runner, summary printer đều là Rust thuần.
- **Rust thuần**: init (scaffold + prompt flow + remote-loop + JSON key order), status
  (transcript + env từ helper), ignore, sync code/doc/all/stop/add (scan-root dedupe/validate,
  interactive select, `_run_with_retry`, doc change-detection git>hash>md5-state>mtime,
  state file `md5(folder)[:12].json`), mcp start/add (ps-scan, .mcp.json + agent configs),
  harness (init/status/task/run/context/verify + mini YAML parser), installer, mcp-gates.
- Repo root resolution: `CORTEX_HARNESS_REPO_ROOT` > walk-up từ cwd (tìm `cortex_harness/dev.py`)
  > compile-time fallback — phục vụ binary cài toàn cục.

## Exclusions / fallbacks (đã thoả thuận trong scope)

| Thành phần | Cách xử lý | Lý do |
|---|---|---|
| storage layer (resolve_storage/overlay/fingerprint) | shell-out Python qua helper | native layer đang làm ở workstream song song; giữ nguyên bề mặt lệnh |
| `journal status/purge` internals | shell-out Python | tương tự (journal Rust của phase 02 nằm sau trait riêng) |
| `db_transfer` | shell-out Python | thuần Python I/O, parity tuyệt đối |
| `_sync_folder`/`_run_analyzer` (legacy engine) | không port | dead code trong dev.py — không lệnh nào gọi |
| Windows lifecycle (PowerShell) | không port | macOS/POSIX only theo `_run_lifecycle`; nhánh win32 của dev.py giữ ở Python ref |
| `_normalize_embed_device` ("auto"/mps/cuda) | probe torch qua 1 tiến trình con venv | cần torch để quyết; kết quả same-as-python |

## Suspected shared bugs / quan sát

1. **`cortex-storage` manifest tạm thời làm gãy workspace** — trong lúc chạy, crate song song
   `rust/crates/cortex-storage` tồn tại thiếu target (chưa có `src/lib.rs`), khiến MỌI
   `cargo -p` trong workspace fail tạm thời; đã chờ và build lại khi crate hoàn thiện. Không
   phải bug của cortex-dev; đề xuất các agent tạo crate tạo `src/lib.rs` skeleton ngay khi
   thêm member vào `crates/*`.
2. **dev.py `_run_with_retry`**: `non_retryable_exit_codes` chỉ được kiểm tra khi KHÔNG có
   `result_path` (nhánh `typed_result is None`); khi có result artifact, exit code 2 vẫn đi
   vào vòng retry nếu parse được artifact (chỉ `should_retry` mới chặn). Đã port đúng theo
   hành vi này (không "sửa" chung) — lưu ý khi orchestration Rust hoán đổi.
3. **`sync code` — `--project-dir` sau subcommand**: Click từ chối `sync code all
   --project-dir X` (option của group). Rust parser mirror đúng; nếu script nào đang dùng
   dạng sau-subcommand thì cũng đang gãy với Python — không phải regression.
4. **`status` với provider không hợp lệ**: dev.py văng traceback (ValueError không bắt);
   Rust surface cùng hành vi quan sát được (stderr + exit 1) qua helper.

## Cách chạy lại gate

```bash
cargo build -p cortex-dev
.venv/bin/python scripts/rust_parity/dev_cli_parity.py           # mặc định dùng rust/target/debug/cortex-dev
.venv/bin/python scripts/rust_parity/dev_cli_parity.py --json    # bản machine-readable
```
