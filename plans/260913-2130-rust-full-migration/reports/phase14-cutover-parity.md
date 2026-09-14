# Phase 14 — Scope B: Cutover parity report (migration tool + flip defaults + runbook)

Ngày: 2026-09-14 · Scope: `cortex migrate` (FalkorDBLite `.rdb` → Ladybug),
flip defaults `CORTEX_RUST_ANALYZER` / `CORTEX_MCP_BACKEND`, cutover runbook.

## 1. Files created / changed

| File | Loại | Nội dung |
|---|---|---|
| `rust/crates/cortex-migrate/` (crate mới, bin `cortex-migrate`) | tạo | `src/main.rs` (CLI + orchestration + report), `src/falkor_boot.rs` (redislite + `falkordb.so` boot, cop cơ chế đã chạy của `cortex-mcp/src/graph/runtime.rs` — read-only, `SHUTDOWN NOSAVE`), `src/falkor_values.rs` (records → `NodeRow`/`RelRow` qua `normalize_value`), `src/ladybug_writer.rs` (DDL + batched insert qua crate `lbug`, type inference) |
| `rust/crates/cortex-dev/src/cmds/migrate.rs` | tạo | `dev migrate` — dispatcher mỏng: định vị `cortex-migrate` (env `CORTEX_MIGRATE_BIN` → release → debug), forward argv, propagate exit code |
| `rust/crates/cortex-dev/src/{tree,main,cmds/mod}.rs` | sửa (additive) | surface `dev migrate` (`--dry-run`, `--overwrite`, `--data-root`, `--instance`, `--owner`, `--graph`) + dispatch; `dev stop` dọn thêm process `cortex-mcp` |
| `rust/crates/cortex-dev/src/cmds/mcp.rs` | sửa (additive) | `CORTEX_MCP_BACKEND` trong `dev mcp start`: auto/rust → launch `cortex-mcp --server unified` (code, :8788) / `--server mind` (doc, :8789) kèm env `.env` + overlay config, log/pid file như `_mcp_start_one`; `python` → đường Python cũ nguyên vẹn |
| `code-tiny/tools/sync/incremental_sync.py` | sửa (ONE surgical change) | `_rust_analyzer_binary`: UNSET → auto-flip Rust khi binary tồn tại; `=python` → rollback flag ép Python; `=rust` giữ nghĩa cũ |
| `docs/cutover-runbook.md` | tạo | runbook cutover 1-week dogfood → migrate → flip → rollback → archive Python |
| `tests/test_phase14_rust_analyzer_flip.py` | tạo | 6 regression tests cho flip semantics analyzer |

**Quyết định kiến trúc** (theo đề cho phép chọn): logic migration nằm ở crate
riêng `cortex-migrate` (cần `lbug`, `redis`, `cortex-falkordb`,
`cortex-graph-writer` — trái với dependency-minimal của `cortex-dev`);
`dev migrate` chỉ là dispatcher. Cả 2 mặt CLI cùng tồn tại.

## 2. Flip semantics đã implement

### Analyzer (`_rust_analyzer_binary`)

| `CORTEX_RUST_ANALYZER` | binary tồn tại | kết quả |
|---|---|---|
| UNSET | có | **binary Rust** (`rust/target/release/analyzer-<lang>`) — auto-flip mặc định |
| UNSET | không | Python (fallback tức thì) |
| `python` | có/không | **Python script** — rollback flag tuyệt đối |
| `rust` | có | binary Rust (nghĩa cũ) |
| `rust` | không | Python (fallback như cũ) |
| giá trị lạ | — | bỏ qua, hành vi cũ |

Verify bằng `tests/test_phase14_rust_analyzer_flip.py` (**6/6 passed**) + check
trực tiếp `python -c`: head của `_build_analyzer_cmd` là binary Rust khi
unset+binary, và `[python, script]` khi `=python`; mọi flag còn lại giữ nguyên.

### MCP server (`dev mcp start` / `dev stop`)

| `CORTEX_MCP_BACKEND` | `cortex-mcp` binary | kết quả |
|---|---|---|
| UNSET | có | **Rust**: `--server unified` :8788 (code), `--server mind` :8789 (doc) |
| UNSET | không | Python (hành vi cũ) |
| `python` | — | Python (rollback flag) |
| `rust` | không | fallback Python + cảnh báo ở dòng `[backend]` |

E2E đã chạy: rust server lên :8788, `/health` + `/ready` xanh, `dev stop
--name code-tiny` kill được process Rust; `CORTEX_MCP_BACKEND=python` khởi
động đúng `unified_mcp.py`. Doc server Python đang chạy của user (:8789) không
bị đụng.

## 3. Gate results

1. **`cortex-migrate --dry-run` trên instance thật** — PASS:
   * `--instance default`: 15/15 graph (code+doc) liệt kê kèm counts
     (vd `dogfood_p05` 98 nodes/277 rels; `jp1_probe` 11/15; `procsample_legacy` 45/24).
   * `--instance cortex`: 3/3 graph.
   * `--instance bakatrans` (rdb thật 144 MB): `bakatrans` **97,559 nodes /
     268,548 rels**, labels + rel types liệt kê đủ.
2. **Full migrate instance synthetic** — PASS: tạo
   `~/.cortext-harness/v1/instances/phase14-synth/falkordb/code/data.rdb`
   (boot redislite + `falkordb.so`, `CREATE` graph `synth_graph` 5 nodes /
   4 rels, `SAVE`, `SHUTDOWN`). Migrate → report
   `5/5 nodes, 4/4 rels` **OK**, chi tiết từng label (Person 2/2, Team 2/2,
   Doc 1/1) và rel (KNOWS 1/1, MEMBER_OF 2/2, OWNS 1/1) khớp; đọc lại QUA
   `cortex_graph_writer::store::LadybugStore`. Round-trip nội dung (string /
   int / array / rel property) được khóa bằng unit test
   `roundtrip_content_via_ladybug_store`.
3. **An toàn**: migrate lại khi store đích tồn tại → `ERROR: target store
   already exists … use --overwrite` (exit 1); `--overwrite` → migrate lại OK.
   Nguồn `.rdb` không bao giờ bị ghi (boot `save ""` + `SHUTDOWN NOSAVE`).
4. **Bonus — full migrate instance thật `bakatrans`** (gate riêng của plan
   phase-14): **PASS**, exit 0 — `97559/97559` nodes, `268548/268548` rels,
   cả 19 label + 18 rel-type đều `src == dst` (store đích 754 MB tại
   `instances/bakatrans/ladybug/code/code.lbug/bakatrans`).
5. **`dev migrate --help`** — PASS (surface + examples như trên).
6. **Clippy** — `cargo clippy -p cortex-dev -p cortex-migrate --all-targets
   -- -D warnings` clean.
7. **Cargo tests** — `cortex-dev` 23/23, `cortex-migrate` 8/8 passed.

## 4. Loại trừ (exclusions) + ghi chú phạm vi

* **`dev.py` / `code-tiny/mcp.sh` không được sửa** (ngoài phạm vi file cho
  phép) nên `CORTEX_MCP_BACKEND` được đặt ở `dev mcp start` (cortex-dev) thay
  vì `_mcp_start_one`. Điều này có nghĩa: script `mcp.sh` và đường launch
  Python thuần vẫn luôn khởi động server Python — dùng `dev mcp start` hoặc
  launch `cortex-mcp` trực tiếp để có backend Rust.
* FalkorDB remote `127.0.0.1:6379` không bị đụng tới — migration chỉ đọc file
  local `<instance>/falkordb/<owner>/data.rdb`.
* Multi-label node: chỉ label đầu được first-class trong Ladybug (schema tĩnh
  1 table/label); metadata label phụ không persist riêng.
* Property type động được infer (union value): INT+DOUBLE → DOUBLE, array →
  `STRING[]` (element coerce string), mix kiểu khác → STRING; map property
  (không hỗ trợ first-class) được serialize JSON vào STRING.
* `SKIP`-paging khi stream node/rel từ FalkorDB: O(n²/size) với graph lớn —
  chấp nhận cho tool chạy 1 lần (bakatrans ~11 phút cho 366K entities).

## 5. Suspected issues

1. **`dev stop --name <tên>` (đường Python lifecycle) báo "stop complete"
   nhưng process Python fastmcp có thể còn sống** — quan sát trong E2E:
   server Python :8788 phải `kill` thủ công. Là hành vi pre-existing của
   lifecycle Python (ngoài phạm vi sửa); hook rust-side
   (`cmds/mcp.rs::stop_rust_mcp`) hoạt động đúng với process Rust.
2. **Node không label** được ghi vào table `_unlabeled` (Cypher bắt buộc có
   label khi CREATE) — count tổng vẫn khớp, nhưng tên label là đặt thêm.
3. **Store file name của graph** dùng `cortex_storage::layout::
   ladybug_graph_path` → graph name có ký tự ngoài `[A-Za-z0-9_.-]` sẽ bị từ
   chối (fail-closed, báo lỗi per-graph, các graph khác vẫn migrate).
4. Bakatrans store đích 754 MB so với `.rdb` 144 MB — Ladybug giữ schema/
   WAL lớn hơn RDB snapshot của redis; chấp nhận được trên disk local, nên
   ghi vào INSTALLER_GUIDE sau.

## 6. Kết luận

Gate phase 14 scope B: **hoàn thành**. `cortex migrate` (+ `dev migrate`)
migrate được instance thật lớn nhất hiện có (bakatrans) với count parity
100%, flip defaults hoạt động theo đúng semantic "Rust mặc định, Python là
rollback flag", runbook vận hành có sẵn tại `docs/cutover-runbook.md`.
