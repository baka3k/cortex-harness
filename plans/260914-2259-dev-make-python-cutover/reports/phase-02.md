# Phase 02 report — Process ops native (stop_sync_workers / embedded falkordb / mcp_pids·uptime·stop·start)

**Ngày:** 2026-09-15 • **Branch:** `feat/change-db`

## Đã làm

1. **Module mới `rust/crates/cortex-dev/src/procinfo.rs`** — port `cortex_harness/sync_processes.py`:
   - `process_table()` qua `ps -ax -o pid=,ppid=,command=` (fallback `-e` Linux) + shlex
     parse. **Bug parity bắt được nhờ gate D2**: bản đầu dùng
     `splitn(3, is_whitespace)` — ps right-align cột pid/ppid nên giữa 2 số có nhiều dấu
     cách → field rỗng → 775 dòng chỉ parse được 55 → `matched=0`. Sửa bằng parse
     field-by-field (whitespace runs) → 780/775+ dòng, decoy matched đúng.
   - `sync_processes` (launcher `dev.py sync <owner>` không chứa `stop` + worker
     code-tiny `_analyzer.py`/explicit names + doc-tiny ingestor), `descendants`, `depth`,
     `stop_sync_processes` (TERM → poll 5s → KILL → wait ≤2s; zombie ≠ alive qua `ps -o stat=`).
   - `embedded_falkordb_pids` (redis-server + config `dir`/`dbfilename` shlex comments),
     `stop_embedded_falkordb`.
   - `_mcp_pids` (gộp bản copy cũ trong `cmds/mcp.rs` — giờ delegate về đây),
     `_pid_instance_id` (sidecar → `ps eww`/`-E` fallback), `_mcp_uptime`,
     `_mcp_stop_pattern` (TERM → poll 5s → KILL).
   - Signal qua `libc::kill` (`unsafe` cho phép cục bộ — crate deny unsafe_code).
2. **`cmds/mcp.rs`**: thêm **`mcp_start_one` native** (port `_mcp_start_one`): entry script
   theo catalog `SERVICES` (giữ nguyên thứ tự flag `MCP_SERVICES` của dev.py), env =
   inherit → svc `.env` → harness config overlay, provider isolation (`env::isolate_...`),
   hygiene `_REMOTE_STORAGE_KEYS` (explicit remote thắng, pop path khi có URI), log
   redirect `.cache/dev-mcp-<name>.log`, pid file + sidecar `instance_id`;
   `start()` python-path + `SyncLifecycle::drop` (sync.rs) gọi native thay `call_json("mcp_start")`.
3. **Rewire**: `sync.rs` — `stop_sync_workers` (assemble report native), `stop_embedded`,
   `embedded_falkordb_pids`, `mcp_stop`, Drop-guard `mcp_start`; `mcp.rs` — `mcp_uptime`,
   `mcp_stop`, `mcp_pids` (delegate), `mcp_start`.
4. **D2 — parity harness**: **GATE 7** (3 case): (a) `dev sync code stop` với 2 decoy
   worker thật (spawn detach qua `sh` để launchd reap — decoy Popen-parented tạo zombie
   làm psutil `wait` hiểu là alive tới timeout → `forced=2` vs Rust `forced=0`;đã ghi
   trong report), (b) embedded-falkordb mock (symlink `redis-server-mock` → `/bin/sleep`
   + config trỏ RDB fake) — probe `before/stopped/after` Python vs Rust, (c) `_mcp_pids`
   discovery (khá trừ self-match của probe — pattern lắp runtime + filter own pid).
5. Parity hook mở rộng: `CORTEX_DEV_PARITY_ENV=procinfo` (+`_DB`) và `=mcp_pids`
   (+`_PATTERN`) cho harness.

## Gate kết quả (phase-02.md)

- [x] GATE 7 PASS 3/3 — `sync code stop` output + exit code byte-khớp (normalize pid);
      embedded mock discovery+stop khớp; mcp pid discovery khớp.
- [x] Embedded falkordb: stop → `embedded_falkordb_pids` rỗng (mock probe `after=[]`).
- [x] Live smoke: `dev mcp start` auto-rust — 2 server cortex-mcp LISTEN 8788/8789;
      `dev stop` dọn sạch (0 listener). `CORTEX_MCP_BACKEND=python` — native
      `mcp_start_one` spawn Python server thật, LISTEN cả 2 port, sidecar pid đúng,
      `dev stop` dừng cả hai (rollback không gãy).
- [x] Kill escalation: decoy test — TERM đủ chết → forced=0; cấu trúc KILL-after-5s
      giữ nguyên logic Python.
- [x] `cargo test -p cortex-dev` → 23/23.

## Delta / ghi nhận

- Zombie semantics: psutil `wait()` coi zombie non-child là alive tới timeout (đếm
  forced); Rust classify zombie = dead ngay (đúng hành vi end-state). Trên máy thật
  worker bị shell/launchd reap nên 2 bên cho cùng kết quả; harness phải detach decoy
  để gate strict.
- `_pid_instance_id` Rust đọc thêm dòng `instance_id=` của sidecar (chính xác hơn
  heuristic trailing-segment của Python khi instance id chứa `-`); fallback `ps eww`
  như Python.
- `dev stop`/`doctor` lifecycle vẫn qua script Python (phase-05 port từng action).

## Kết luận

**PASS** — 6 op process hết caller Python-bridge; live smoke 2 backend OK.
