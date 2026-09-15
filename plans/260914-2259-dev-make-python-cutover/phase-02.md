# Phase 02 — Wave A: Process ops native (`stop_sync_workers`, embedded falkordb, `mcp_pids`/`mcp_uptime`/`mcp_stop`/`mcp_start`)

## Scope

Port phần process management của `cortex_harness/sync_processes.py` + các `_mcp_*` helper của
`dev.py` sang Rust (module `src/procinfo.rs` mới + mở rộng `cmds/mcp.rs`, `cmds/sync.rs`).
Process-listing parser tham chiếu có sẵn tại `util.rs:495` — tái dùng.

1. **`stop_sync_workers`** → port `stop_sync_processes(owner, root, include_launchers)`:
   match theo cmdline pattern, TERM→đợi→KILL, trả `{matched, terminated, forced, remaining}`;
   nhánh embedded: `stop_embedded_falkordb(db_path)` + `embedded_falkordb_pids(db_path)`
   (match pid giữ lock/flock trên db file — đọc kỹ Python trước khi port, đây là chỗ dễ sai
   signal-lệnh nhất).
2. **`mcp_pids` / `mcp_uptime` / `mcp_stop`** → port `_mcp_pids(pattern, instance_id)`,
   `_mcp_uptime(pid)`, `_mcp_stop_pattern(...)` — ps parsing + kill; giữ contract
   instance-id trong pattern.
3. **`mcp_start`** → port `_mcp_start_one(name, svc, extra_env)`: spawn binary theo
   `MCP_SERVICES` catalog, log redirect, pid file. **Lưu ý**: phase-14 umbrella đã có
   auto-rust backend path (`CORTEX_MCP_BACKEND` unset → binary Rust, `--server unified`
   :8788 / `mind` :8789) — phase này nối thẳng vào path đó, bỏ bước mồi Python.
   `python` flag vẫn spawn Python script (rollback contract giữ nguyên).

## Work item chung (D2)

- Mở rộng `dev_cli_parity.py`: fixture `sync code stop` / `mcp start|stop|status` (spawn thật
  trên stock, đối chiếu output + exit code Python vs Rust). Hiện harness không cover các path này.

## Gate

- [ ] Parity harness mở rộng (D2) pass: với stock đang có sync worker + MCP chạy thật,
      `dev sync code stop` + `dev mcp stop`/`dev mcp status` cho output + exit code byte-khớp Python.
- [ ] Embedded falkordb: stop → `embedded_falkordb_pids` trả rỗng; restart lại được store.
- [ ] `dev mcp start` (auto-rust) lên server, `dev doctor` thấy healthy; flip
      `CORTEX_MCP_BACKEND=python` vẫn chạy (rollback không gãy).
- [ ] Kill -9 pid giữa stop-sequence: lần chạy sau vẫn dọn sạch (so Python behavior).
- [ ] `cargo test -p cortex-dev` pass.

**Trạng thái:** ready (rev 2) — **DONE 2026-09-15** (reports/phase-02.md)
