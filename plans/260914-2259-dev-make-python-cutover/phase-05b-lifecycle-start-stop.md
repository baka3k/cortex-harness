# Phase 05b — Port `start` + `stop` → native (xoá shim `mcp-lifecycle.py` cuối cùng trên đường dev start)

## Vì sao làm ngay (không phải việc vặt)

`dev start` **vẫn là shim Python**: `cmds/lifecycle.rs:727` → `run_lifecycle("start")` →
`python3 scripts/mcp-lifecycle.py start`. Hệ quả trực tiếp trên branch `feat/change-db`
(config active `GRAPH_PROVIDER=ladybug`):

```
ValueError: Unsupported graph provider for CODE_GRAPH_PROVIDER: 'ladybug'; expected 'falkordb' …
  at scripts/mcp_runtime_config.py:55 (normalize_graph_provider)
```

`scripts/mcp_runtime_config.py:45-58` là **cho phép 2 provider duy nhất còn sót** trong repo:
plan `260913-1538-ladybug-graph-provider` không hề nhắc file này. Mọi đường Rust đã
ladybug-complete: `cortex-dev/src/config.rs:203` (`LADYBUG_ALIASES` = ladybug|lbug|lady-bug|kuzu),
`env.rs:268` (isolate 3 nhánh), `cortex-mcp/src/project_registry.rs:156`,
`cortex-sync/src/cli.rs:30`, `cortex-storage/src/config.rs:68-79` (`LADYBUG_PATH|CODE_PATH|DOC_PATH`
trong `LOCAL_CFG_KEYS`). → **Sửa Python là công vứt** (phase-07 archive `mcp-lifecycle.py`);
port `start`/`stop` là cách duy nhất đóng lỗi mà không thêm nợ.

Bug thứ hai cùng cụm (phải sửa trong bản port, không sửa ở Python): key graph chọn theo
`provider == "falkordb" ? FALKORDB_GRAPH : NEO4J_DB` — `mcp-lifecycle.py:1874`
(`runtime_overrides --database`) và `:1931` (`invoke_start` graph của pids record). Với
ladybug nó ghi/đọc `NEO4J_DB`, tức `--database` im lặng sai và `dev start` không bao giờ
reuse đúng record (graph luôn rỗng).

## Decision

| # | Quyết định | Lý do |
|---|---|---|
| D1 | **Giữ nguyên launch model của Python**: wrapper `start-<instance>-<server>.command` + `osascript` mở Terminal.app (POSIX: gnome-terminal/x-terminal-emulator/xterm) + chờ `pid_path` 5s. `dev start` tiếp tục chạy server **Python** qua `<svc>/mcp.sh`; `dev mcp start` vẫn là đường chọn rust backend | User chốt 2026-09-15: giữ UX mỗi server một cửa sổ log. Hệ quả tốt: wrapper đã nhận `--host/--port/--path` qua argv nên **không cần tham số hoá `cmds/mcp.rs`** (`McpService.port` const giữ nguyên) — diff gọn hơn kế hoạch ban đầu. `.active.env` vẫn là nguồn env thật (mcp.sh source `CORTEX_HARNESS_ENV_FILE` cuối cùng, sau `.env`) |
| D2 | **Giữ nguyên state contract** `.cache/mcp/`: `<instance>-<server>.active.env` (0600), `<instance>-<server>.pid`, `start-<instance>-<server>.command` (0755), `pids.json` | `dev doctor` (còn shim) và `record_runtime_metadata` (`mcp-lifecycle.py:1604`) đọc các file này; format không đổi ⇒ không phá record sẵn có (`cortext-*`, `bakatrans-*`, `aws-sa-associate-*`). `json.dumps(indent=2)` = `to_string_pretty`, **không** ký tự xuống dòng cuối |
| D3 | Graph key theo provider **3 nhánh**: falkordb→`FALKORDB_GRAPH`, ladybug→`LADYBUG_GRAPH`, neo4j→`NEO4J_DB` | FIX bug ở mục trên; khớp `cortex-storage/src/targets.rs:870-889` (đọc `LADYBUG_GRAPH` fallback `hyper_graph`) |
| D4 | `format_bash_exports` port kèm **unset matrix 3 provider** (falkordb unset `NEO4J_*`+`LADYBUG_*`; neo4j unset `FALKORDB_*`+`LADYBUG_*`+`DOC_FALKORDB_GRAPH`; ladybug unset `FALKORDB_*`+`NEO4J_*`+`DOC_FALKORDB_GRAPH`) | Mirror `env.rs:278-283` (`drop_prefixes`) — bản Python `mcp_runtime_config.py:255-266` mới có 2 nhánh. **Delta có chủ đích**: falkordb nay unset thêm `LADYBUG_*` |
| D5 | `dev stop` = pre-kill `cortex-mcp` (đã có: `mcp::stop_rust_mcp`) **rồi** native stop-records; **không** shim python | `main.rs:200-204` đang làm nửa trước; nửa sau là `mcp_state::stop` |
| D6 | Lỗi runtime: `[error] {msg}` trên **stdout**, exit 1 (như `main()` Python `:2141-2143`); lỗi cross-option kiểu argparse: stderr + exit 2 | `print()` của Python mặc định ra stdout — shim hiện tại cũng thế, nên giữ để script gọi `make start` không đổi |

## Phạm vi đã làm

### Mới: `rust/crates/cortex-dev/src/mcp_state.rs`
Cổng các primitive mà Rust **chưa có** (đã kiểm: `grep` 0 hit cho `pids.json`,
`active.env`, `osascript`, `next_available_port`, `tcp_port_open`, `config_instance`,
`validate_instance_name`, `stop_process_tree`):

- `state_dir()` / `pid_file()`; `read_records`/`write_records` (+ biến thể `*_from`/`*_to` nhận
  path tường minh — seam cho unit test, không dùng env-var hack vì `set_var` là `unsafe` ở edition 2024)
- `validate_instance_name` (`^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$`), `config_instance`
  (`PROJECT_ID | CORTEX_STORAGE_INSTANCE | <graph key>` → sanitize → strip `-.` → cắt 64 → default `cortext`)
- `tcp_port_open` (`connect_timeout` 1s, thử hết addrinfo như `socket.create_connection`),
  `next_available_port`
- `shlex_quote` (POSIX), `format_bash_exports` (D4), `graph_key` (D3)
- `stop_process_tree` (con trước, SIGTERM không leo thang) + `stop(instance)` (port `invoke_stop`:
  record khớp `script in command`, skip stale, sweep markers khi `instance=None`, prune + xoá file khi rỗng)
- `record_is_live`, `state_paths`, `write_active_env` (0600), `write_executable` (0755)

### Sửa tối thiểu
- `procinfo.rs`: `send_signal` + `SIGTERM` → `pub(crate)` (tái dùng, không thêm `unsafe` mới).
- `tree.rs`: `--provider` Choice thêm `ladybug` (Python argparse đã có 3 choice, bản Rust thiếu).
- `main.rs`: `mod mcp_state;`. Routing `dev stop` giữ nguyên (pre-kill rust MCP rồi `lifecycle::stop`, nay native).
- `cmds/mcp.rs`: **không đổi** (nhờ D1).

### `cmds/lifecycle.rs`
`start`/`stop` hết `run_lifecycle`: `resolve_start_config(cwd)` → `selected_servers` (giữ nguyên
5 thông báo lỗi của Python) → `env::mcp_env_from_config` per server → nhánh custom / không custom
(reuse + `next_available_port` vs `stop(instance)` + `Port already in use`) →
`runtime_overrides` (D3) → `.active.env` + wrapper `.command` byte-khớp → `terminal_command`
(osascript + `json.dumps` escaping qua `serde_json::to_string`) → chờ pid 5s → record `pids.json`.

### Không đụng
`scripts/mcp-lifecycle.py`, `scripts/mcp_runtime_config.py` (chờ phase-07 archive),
`tests/test_mcp_runtime_config.py`, actions `doctor|infra-up|infra-down` (vẫn shim —
`mcp-lifecycle.py:191-214,1491-1514` cho thấy doctor đã tự xử ladybug qua `GRAPH_PROVIDER`
env, không qua `normalize_graph_provider`).

## Gate — kết quả 2026-09-15

- [x] `dev start` với config active `GRAPH_PROVIDER=ladybug`: **không raise**, exit 0,
      `cortext/code-tiny` 8788 + `cortext/doc-tiny` 8789 LISTEN;
      `cortext-code-tiny.active.env` = `GRAPH_PROVIDER=ladybug`, `CODE_GRAPH_PROVIDER=ladybug`,
      `LADYBUG_PATH|CODE_PATH|DOC_PATH`, `LADYBUG_GRAPH=cortext`, **0 dòng** `export FALKORDB_*`/`NEO4J_*`.
- [x] `cargo test -p cortex-dev` **38/38 pass** (thêm 5 test lifecycle + 6 test mcp_state).
- [x] `dev start` lần 2 → `[start] Reusing cortext/code-tiny on 127.0.0.1:8788 (graph=cortext)`
      — chứng minh D3: graph đọc từ `LADYBUG_GRAPH` (bản Python cho ra chuỗi rỗng nên không bao giờ reuse).
- [x] `dev stop` full → `[stop] MCP stop complete.`, cổng 8788/8789 thoát LISTEN, `pids.json` **bị xoá**
      (record rỗng → unlink, như `:1723`); `dev stop --name <absent>` → scoped, không đụng record khác.
- [x] Không còn đường nào từ `dev start`/`dev stop` tới `run_lifecycle` (chỉ `doctor`, `infra-up`,
      `infra-down` còn shim).
- [x] `tests/test_make_lifecycle.py` + `tests/test_dev_lifecycle_commands.py`: 57 pass,
      1 fail **có sẵn từ trước** — `Makefile:55 update:` không có `dev update`
      (`tree.rs` không chứa "update"; Makefile không bị sửa trong thay đổi này).

## Delta parity cần ghi nhận

1. **D4**: `falkordb` nay unset thêm `LADYBUG_*` trong `.active.env` (mirror `env.rs:278`).
2. **D3**: `--database` trên provider ladybug ghi `LADYBUG_GRAPH` (thay vì `NEO4J_DB` như shim).
3. Thông báo lỗi khi mở terminal failed: bản Rust
   `Terminal launch failed (osascript exited N).` thay cho `Command '[...]' returned non-zero exit status N.`
   của `subprocess.CalledProcessError`.
4. **Có sẵn, không phải regression**: sweep của `dev stop` (`str(ROOT) in command` + marker)
   khớp cả dòng argv của shell gọi nó — nếu argv chứa `code-tiny/mcp.sh`/`mcp_graph_rag.py`
   thì `dev stop` tự SIGTERM chính process cha của nó và chết trước khi ghi `pids.json`.
   Python y hệt (`:1750-1755` chỉ loại `os.getpid()`). Đã tái hiện thật trong lượt verify này.
   Nếu muốn siết, làm thành việc riêng (so sánh `script`/`argv[1]`, không so argv thô).

## Việc còn lại của phase-05

`doctor`, `infra-up`, `infra-down` (3/14) vẫn shim Python → **11/14 native** sau phase-05b.
