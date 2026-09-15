# Phase-01 report — Probes + golden capture (2026-09-16, branch `feat/change-db`)

Mục tiêu: khoá evidence trước khi đổi code. Không sửa production code (2 exception có chủ đích ghi ở §8 — không có).
Build: `cargo build --release --workspace` PASS tại HEAD `4b854bd` (build-commit handshake xanh trong mọi run).

---

## 1. Golden ladybug sync (scratch `sp1-golden`, python-leg)

Cơ chế thật đã dùng (ghi đúng từng lệnh):

- Scratch project: `cp -R tests/fixtures/web-overlays/fastapi_django/. /tmp/sp1-golden/` → git init + commit `694378a`
  (baseline để phase-04 chạy incremental leg touch-1-file).
- Scratch config `/tmp/sp1-golden/.cortext-harness/config/dev.json` mirror đúng shape config thật của user
  (`GRAPH_PROVIDER=ladybug`, `CODE_GRAPH_PROVIDER=ladybug`, `LADYBUG_GRAPH=<project_id>`), thay:
  `CORTEX_STORAGE_INSTANCE=sp1-golden`, `CORTEX_DATA_HOME=/tmp/sp1-golden-data`, không QDRANT (embedding pass skip).
- Bootstrap store: **tự động đúng cơ chế thật** — `resolve_storage` tạo instance + manifest lần chạy đầu
  (`/tmp/sp1-golden-data/v1/instances/sp1-golden/manifest.json`), Python ladybug driver tạo store file lần full-sync đầu
  (`ladybug.Database(...)` tạo file + chmod 0700; log auto-DDL bootstrap 166 index/schema trong run).
- Lệnh: `rust/target/release/cortex-dev sync code --project-dir /tmp/sp1-golden --full-scan all`
  (options PHẢI đứng trước `all` — parser group-option; `--full-scan all` sai thứ tự → usage error).
  Delegation fires như kỳ vọng: `[cortex-sync] python-plane delegation: graph target resolution: ladybug provider
  requires embedded storage resolution (Python-plane)` → Python orchestrator chạy full run với Rust analyzer children.

Kết quả: exit 0 ở tầng dev nhưng **run outcome=failed** — 1 component failure có tồn tại từ trước (pre-existing):

- `overlay:fastapi_django` (analyzer-fastapi-django, Rust child) fail 2 lỗi ladybug dialect:
  1. `SET node += row` — **`SET +=` không parse được trên ladybug 0.20.4** (đã ghi trong module doc
     `ladybug_store.rs:14-17`: "query chứa += (typed relations, evidence sites, topology) fail y như Python driver
     trên ladybug hiện tại; parity gate chạy trên falkordb"). Query đổ vỡ: project_topology endpoint upsert
     (`topology.rs:46,81,92,110,120,131,145` — `SET endpoint += row, …`).
  2. `failed to create range index on ProjectModule(id): Cannot create ART index because the table already has a
     HASH primary-key index` — store bootstrap tạo mọi node table `id STRING PRIMARY KEY` (`ladybug_store.rs:326,360`),
     rồi `create_indexes` (`:871-901`) chỉ swallow "already exists", không swallow biến thể PK-collision. Schema manifest
     yêu cầu index range trên `ProjectModule(id)` → hard fail.
- Python parser (primary) PASS trên ladybug; state bị mark dirty đúng cơ chế recovery.

→ **Hệ quả luồng kế hoạch**: golden as-found ghi nhận hành vi python-leg hiện tại (fixture run1 dưới đây). Hai lỗi
dialect trên là bug store-layer có sẵn (không do plan này); phase-02 sửa (thuộc "wire ladybug"), sau đó golden HEALTHY
được re-capture qua cùng cơ chế delegation (python-plane còn trong tree tới hết phase-05) — phase-04 so Rust-native vs
golden healthy. Golden as-found giữ làm evidence "python-leg as-found".

Fixtures đã chốt:

- `tests/fixtures/sync-plane-golden/ladybug/summary-full-run1.json` — summary run1 (outcome=failed, component_failures
  có 2 lỗi trên; graph name `sp1golden` đúng project_id-derived).
- `tests/fixtures/sync-plane-golden/ladybug/state-after-full-run1.json` — state v2 (dirty=true, dirty_inventory_paths).
- `tests/fixtures/sync-plane-golden/ladybug/manifests-run1/` — changed/deleted manifests per parser (python,
  fastapi_django, project_topology, python_embedding_*).
- `.cache/sp1-golden/golden-store-asfound-run1.lbug` — copy store 1.5MB (đóng sạch: copy sau khi run thoát; ladybug
  embedded không để .wal trong dir `code.lbug/` — chỉ `hyper_graph`).
- README tái-create: `tests/fixtures/sync-plane-golden/README.md`.

## 2. Journal probe (M2 precondition + fixture byte-compat)

- **Required mode + non-cplus parser**: `CORTEX_GRAPH_JOURNAL_MODE=required` → parent delegate (orchestrator.rs:1222)
  → Python orchestrator từ chối ngay: `parser 'python' is blocked in graph-journal required mode; use shared-shadow
  until its node-first writer migration is complete` (contract có sẵn — required lane là cplus-only).
- **Required mode + cplus (scratch `/tmp/sp3-cplus`, 3 file .cpp, instance `sp3-cplus`)**: child analyzer-cplus (Rust,
  post phase-08) chạy graph pass ok nhưng **KHÔNG tạo journal** — không có dir `graph-write-journal/` nào được tạo
  (`cortex-graph-writer` không có consumer nào của `CORTEX_GRAPH_JOURNAL*` — grep toàn workspace: chỉ journalx.rs,
  journalenv.rs, orchestrator.rs). Run fail tại child exit 1 (lỗi ladybugdialect riêng: `ladybug provider does not
  support typed relation row properties (relations:Function:POINTER_TO:Type)` — pre-existing, ngoài biên plan này).
  → **M2 precondition CHÍNH THỨC: Rust children KHÔNG enqueue batch. Freeze replay contract = legacy-drain-only.**
- **Journal fixture byte-compat**: legacy journal thật trên máy:
  `.cache/graph-write-journal/0708b42ae396610a0165a5b0/cplus.sqlite3` — 2 runs (status `blocked`), **39 batches**
  (33 done / 4 pending / 2 blocked; reconciliation: node_identity×18, file_cleanup×16, evidence_edge×2,
  repository_file×2, orphan_unknown_cleanup×1; phases nodes×35, relationships×4), barriers `phase:nodes` +
  `audit:endpoints` + per-node. `schema_fingerprint = 8613fc08894a26c2…` **== canonical fingerprint hiện tại**
  → byte-compat với `cortex-graph-core` DDL/API. Copy: `tests/fixtures/sync-plane-golden/journal/cplus-legacy.sqlite3`
  (488K) + `.cache/sp1-golden/legacy-journal-cplus.sqlite3` (original giữ nguyên).
- **consumer.py `_main` ladybug branch**: xác nhận KHÔNG có (consumer.py:355-358 — `neo/neo4j` → NEO4J, else
  FALKORDB). Trên ladybug required lane, consumer sẽ tạo driver FALKORDB (embedded redislite từ falkordb_code_path
  của instance) — hành vi thật đã ghi; deliberate fix ở phase-03: store target từ GraphContext (ladybug hỗ trợ).
- **Edge case dev-level pre-run recovery (phát hiện mới)**: export `CORTEX_GRAPH_JOURNAL_MODE=required` ở tầng dev →
  `journalx::recover_required_lane` spawn consumer KHÔNG có PATH/METADATA (per-run env chỉ tồn tại trong cortex-sync)
  → `[journal] recovery failed: journal mode requires an absolute path and stable run metadata` exit 70 → sync bị skip.
  Không ảnh hưởng flow thật (mode env không bao giờ ở tầng dev trong production); ghi nhận để phase-03 xoá python spawn
  (journalx.rs:306-343) và thay bằng lib replay — edge case chết theo.

## 3. Caller-check deletion manifest (C1 mở rộng)

- `tools/sync/{owner_manifest,build_owner_manifests,dead_code_report}.py`: importers ngoài namespace chính họ:
  `cortex_harness/sync_processes.py`, `cortex_harness/dev.py`, `rust/crates/cortex-dev/src/procinfo.rs` (script list),
  tests (cobol, cplus_windows_resource, common_analyzer_registry, perl_integration). Chưa thấy importer sống ngoài
  sync/parity — giữ kết luận XOÁ phase-06 (procinfo.rs:109 trong manifest Rust).
- **7 file C1 — xác nhận LIVE importers ngoài sync (không xoá, chuyển python-legacy-cleanup)**:
  - `driver/falkordb_driver.py` ← doc-tiny (qua storage remote_probe/factory + `tools.graph.core.factory`), MCP
    rollback (`tools.graph.core.shared_runtime`), tests.
  - `driver/ladybug_driver.py` ← `doc-tiny/graph_store.py:212` (lazy), storage factory, scripts/benchmark_ladybug,
    parity gen_writer_rows_fixture, tests.
  - `cli.py` ← `tools/sync/*` (sẽ chết cùng closure), `tools/ts/ts_analyzer.py`, `mcp/services/impact_service.py`
    (env_graph_provider), scripts/setup_constraints.py + cleanup_repo_graph.py, tests.
  - `core/base.py` ← `tools/graph/__init__.py:8` (top-level), operations/*, writers/*, consumer.py, MCP.
  - `core/provider_contract.py` + `core/shared_runtime.py` ← MCP python rollback servers (fastmcp/unified/java/cplus/
    android + services/{explore,impact,workflow}), tests.
  - `tools/graph/__init__.py` ← explore_service, scripts/backfill_project_scope_keys, sync scripts, tests.
- **`cortex_harness/dev.py` sync bodies :3400-3560**: dead xác nhận — entrypoints binary-only (`dev.sh` exec
  cortex-dev binary, phase-06 cutover note; Makefile không invoke dev.py sync). XOÁ được ở phase-06.
- **`tests/test_incremental_sync_*.py` glob**: **15 file** thực tế (gồm `_lock`, `_worktree`; KHÔNG có `_workflow`) +
  `tests/test_sync_processes.py` = 16. Glob dùng ở phase-06, không hardcode count (L1 ✓).
- Kết luận manifest: giữ nguyên rev2 (true sync closure); cập nhật plan.md đã xong từ rev2 — caller-check xác nhận,
  không phát hiện thêm target cần chuyển.

## 4. cwd-vs-root probe

- Python thật: `prepare_graph_args` với `--root /tmp/sp1-golden`, cwd = repo, KHÔNG `LADYBUG_PATH`:
  resolved path = `/Users/…/.cortext-harness/v1/instances/default/ladybug/code/code.lbug/hyper_graph` —
  **anchor theo `Path.cwd()` (cli.py:243-244) → trỏ nhầm vào instance của REPO, không phải instance của project**
  (hazard sai store có thật, đúng như research gap #2). Graph name vẫn đúng precedence: `sp1golden` (project_id,
  LADYBUG_GRAPH unset).
- **Chốt phase-02: Rust resolve_storage anchor theo `--root`** (deliberate deviation so cli.py:243-244, ghi
  doc-comment). Trong flow dev thật, `LADYBUG_PATH` env từ storage overlay luôn có sẵn nên hai anchor cho cùng kết
  quả; lệch chỉ xảy ra khi gọi cortex-sync trực tiếp (parity/CI) — anchor `--root` là đúng ngữ nghĩa.

## 5. Embedded-falkordb spike input (H2)

- `cortex-migrate/src/falkor_boot.rs:100-117` đọc kỹ: spawn redis-server (redislite) + `--loadmodule falkordb.so`,
  config `dbdir/dbfilename` trỏ `data.rdb`, port TCP tự chọn, readiness PING/GRAPH.LIST (60s deadline), Drop =
  `SHUTDOWN NOSAVE`. **Read-only contract** (migration): `save ""` + `appendonly no` + NOSAVE ⇒ **mọi ghi trong
  phiên bị mất khi shutdown**. Reuse cho sync (write path) CẦN sửa lifecycle: persist-on-shutdown (`SAVE` trước
  `SHUTDOWN` hoặc bật rdb-save) — không còn là "reuse nguyên văn", là fork có chủ đích.
- Dependency direction: `cortex-sync → cortex-migrate` hợp lệ kỹ thuật (cả hai đều ở workspace, cortex-migrate nhẹ —
  chỉ phụ thuộc cortex-falkordb + regex); nhưng semantics khác (read-only vs durable-write) → tách hàm boot writable
  riêng, không đổi contract migration.
- Blast radius `FALKORDB_PATH`: config/env plumbing (storage config, dev.py graph-args passthrough, targets.py
  fingerprint) + ReadMe rollback note. KHÔNG có data migration falkordb→ladybug (grep: chỉ falkor_boot chạm falkordb;
  cortex-migrate không có target ladybug) — message phase-02 fallback phải nêu rõ rebuild-by-full-resync.
- **Kết luận spike đầu vào**: wire-able với effort trung bình (fork boot + save-on-drop + readiness), rủi ro lifecycle
  (process leak, save semantics, redislite version drift) không thấp. Quyết định Q2 giữ spike-first NGẮN: thử wiring
  trong phase-02 với budget một nhánh; FAIL lifecycle → fallback fail-closed trung thực (2 lựa chọn + rebuild caveat)
  như plan.md §Q2. (Xem phase-02 report cho kết quả cuối.)

## 6. Coordination (M3)

- (a) Working tree đang có uncommitted edits của vector-lane trên `docs/cutover-runbook.md` (+ `doc-tiny/mcp.sh`,
  `plans/260915-2027…/phase05-cutover-bugfix.md`, file cortex-mcp ladybug provider mới). **Phase-02 sẽ KHÔNG đụng
  runbook cho tới khi các edit đó được commit riêng** (M3a). Commit tách lane: giữ working tree hiện tại nguyên vẹn,
  các commit của plan này chỉ chạm file của sync-plane.
- (b) `plans/260821-2115-dev-sync-code-windows` vẫn `status: active`, targets đè deletion manifest
  (incremental_sync.py, tools/graph/cli.py, core/factory.py). Kết luận ghi cho gate phase-06 (M3b): plan đó thuộc
  Windows remote-FalkorDB plumbing (2026-08, pre-analyzer-cutover); target của nó đã bị phase-08 analyzer cutover +
  plan này thay thế/xoá. Cần đóng/re-scope TRƯỚC DELETE commit — phần việc của phase-06 (cross-plan update), không
  block phase-02..05.

## 7. Golden mask list (phase-04 dùng)

Mirror `sync_orchestrator_parity.py:62-74` `MASKED_SUMMARY_KEYS`:

```
run_id, correlation_id, started_at, finished_at, duration_seconds, wait_seconds, updated_at, detector_evidence
```

+ per-run tokens đệ quy: `cache_dir`/`scope.cache_dir`, `lock.path`, `lock.owner`, đường dẫn `*_manifest`,
  `state_before/state_after.inventory_path`, `parse_quality.artifact(s)`, `native_message_scan.output_dir`,
  pid-token trong tên file manifest (pattern `<snapshot12>_<pid>_<hex8>`), `summary_path`.
+ **KHÔNG mask**: graph name (assert = project_id-derived, H1), `summary.backend` (assert đúng leg type, L2),
  `status`/`outcome`/`error` (assert khớp — golden healthy thì native phải healthy).

## 8. Open questions → đã đóng

| Research open question | Kết luận |
|---|---|
| (a) Rust children có enqueue journal? | KHÔNG (§2) — replay = legacy-drain-only, phase-03 freeze |
| (b) consumer.py trên ladybug required? | Không chạy được đúng nghĩa (không có branch; driver FALKORDB sai store) — deliberate fix phase-03: store từ GraphContext |
| (c) tools/sync/{owner_manifest,…} callers | Không có live importer ngoài sync/parity/tests — xoá phase-06 (§3) |
| (d) Path.cwd() vs --root | `--root` (§4) |
| (e) embedded-falkordb strategy | Spike-first ngắn ở phase-02; fallback fail-closed (§5) |

Gates: fixtures ✓ (commit kèm report), report 7 mục ✓, manifest plan.md khoá cuối ✓ (không đổi so rev2).
