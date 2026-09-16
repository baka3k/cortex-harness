# Phase-02 report — Embedded resolution + polymorphic store + hatch (2026-09-16)

Closes trigger delegation #1 (graphops.rs ladybug + embedded-falkordb). Build at HEAD `848117d`+
(uncommitted phase-02 delta), handshake xanh toàn bộ analyzer children.

## Changes

1. **Ladybug branch native** (`graphops.rs::prepare_graph_args`):
   - Store path: `--ladybug-path` arg (cli.rs merge env `LADYBUG_PATH`) > `cortex_storage::resolve_storage`
     anchored tại **`--root`** — deliberate deviation so cli.py:243-244 (`Path.cwd()`), probe phase-01.4
     đã chứng minh cwd-anchor trỏ nhầm instance. Deviation ghi doc-comment tại `resolve_embedded_path`.
   - **Graph name H1, chain trong suốt**: raw `--ladybug-graph` arg > `project_id` > env `LADYBUG_GRAPH`
     > `"hyper_graph"`. cli.rs KHÔNG còn merge env vào `args.ladybug_graph` (env merge sớm sẽ lấn quyền
     project_id). Registry KHÔNG tham dự ladybug_graph.
   - **Nuance empirical ghi nhận**: live python argparse default `--ladybug-graph` = `env or "hyper_graph"`
     làm `project_id` bị che trong path gọi trực tiếp (đo được: env unset + project_id sp1golden →
     "hyper_graph"); Rust port implement chain ĐÚNG NHƯ TÀI LIỆU (cli.py:249-256 source-of-truth).
     Flow dev thật luôn truyền `--ladybug-graph` tường minh (sync.rs:183-190) nên hành vi user KHÔNG đổi.
   - **ProjectRegistry thật** (thay stub warn): `apply_project_registry_defaults` đọc
     `<root>/.cortext-harness/config/*.json` (walk-up từ root như cli.py `_resolve_config_dir`), match
     project_id case-insensitive, fill `falkordb_graph` khi trống; unregistered → warn + fallback
     (mirror cli.py). Qdrant-collection fill bỏ qua — cortex-sync không có flag đó.
2. **`open_store` polymorphic** → `Box<dyn GraphStore>`: ladybug qua `LadybugStore::open(path, graph)`;
   falkordb remote qua `FalkorDbStore`; neo4j fail-closed actionable. Call sites đổi: setup (:1230),
   impact (:1318), topology probe (:1368), message-scan lane (:3009) — helpers nhận `&mut dyn GraphStore`.
3. **Embedded falkordb — Q2 SPIKE VERDICT: FAIL → fail-closed trung thực** (`EMBEDDED_FALKORDB_UNAVAILABLE`):
   (a) `falkor_boot.rs` là read-only contract (`save ""` + `SHUTDOWN NOSAVE`) — ghi trong phiên MẤT khi
   shutdown; (b) analyzer children Rust KHÔNG có support FalkorDBLite (cli.rs:288-291) — write plane
   không chạy được embedded dù parent wire được. Error nêu đủ 2 lựa chọn: `FALKORDB_URI` (giữ data) /
   `GRAPH_PROVIDER=ladybug` (rebuild bằng full re-sync, KHÔNG có data migration). Đã smoke: exit ≠ path
   delegate, message nguyên văn như thiết kế.
4. **Backend stamp (L2)**: native → `summary["backend"]="rust-native"` (trước run_flow); delegation →
   patch `backend="python"` vào summary python child post-exit (`stamp_delegated_summary`, best-effort
   khi có `--summary-path`).
5. **Hatch**: `CORTEX_SYNC_BACKEND=python` → `delegate_to_python("explicit …")` ngay đầu `run_incremental`.
6. **Message-lane provider gate (M1)**: gate cũ `provider == "falkordb"` → mở cho ladybug
   (`native_message_scan.graph_upsert = "native"` thay "native-falkordb").

## Ladybug store dialect fixes (cortex-graph-writer + analyzer-web-overlays)

Phase-01 golden as-found lộ 2 bug + quá trình native hoá lộ thêm 5 — TẤT CẢ là store-layer dialect,
pre-existing trên cả python-leg (không phải regression của plan):

| # | Triệu chứng | Fix |
|---|---|---|
| 1 | `CREATE ART INDEX` trên `ProjectModule(id)` — "already has a HASH primary-key index" | `create_indexes` swallow biến thể PK-backed (index tồn tại ở dạng mạnh hơn) |
| 2 | Index trên property ngoài base columns (Workflow.workflow_id, site_id, …) → preflight poll 60s deadline | `create_indexes` auto-DDL `ALTER TABLE ADD <prop> STRING` rồi retry index |
| 3 | Preflight inspect trước bootstrap → "Table Project does not exist" | `LadybugStore::inspect_indexes` bootstrap-first (không đặt trong preflight — FalkorDbStore::ensure_schema đệ quy preflight) |
| 4 | Zero-arg `timestamp()` không tồn tại (setup query) | `prepare()` rewrite cả `datetime()` LẪN `timestamp()` → `timestamp('<iso>')` (mirror ladybug_driver.py:269-274) |
| 5 | `SET <var> += row` không parse được (topology endpoints, web overlays) | `expand_set_plus_equals` sau render: per-property gán theo union-key; loại merge-key/PK assignment (SET lại id = PK-set violation); guard `+= row.props` giữ nguyên (fail-closed) |
| 6 | `FOREACH (x IN list \| DETACH DELETE x)` không parse (topology cleanup, web delete, message cleanup) | Query biến thể ladybug delete-trực-tiếp-từ-MATCH; topology cleanup chạy per-label (`TOPOLOGY_OWNED_NODE_LABELS`) vì labelless MATCH làm binder/auto-DDL không resolve được property |
| 7 | `MERGE (x:Label {natural_key})` rewrite sang id để lại `<var>.id = <expr>` trong SET → PK-set violation | `rewrite_merge_natural_keys` strip assignment trùng expr (ON CREATE + ON MATCH, idempotent) |
| 8 | `row.handler_scope + '::'` → `+(BOOL,STRING)` (binder mis-type struct field dùng trong cả so sánh lẫn concat) | `relationship_query` ladybug dùng `concat(row.handler_scope, '::')` (repro + bisect bằng PyPI ladybug) |
| 9 | Message upsert FOREACH-CASE (`tools/common/message_scan.py` shape) không parse | Ladybug variants FOREACH-free: (messages+sender) / (receiver links, lọc rows có receiver) / (File CONTAINS link best-effort — `File→CONTAINS→Message` nằm ngoài manifest rel schema, golden chưa từng chạy được; violation chỉ log, không hỏng lane) |
| 10 | Topology link queries labelless MATCH (PUBLIC_API/EXISTING_ENDPOINT/ANDROID_FACT) — binder mis-type | Ladybug per-label variants (`{label}` template + label lists) |

Scanner `unwind_row_keys`/`first_map_keys`: depth-aware + string-aware cho CẢ nháy đơn lẫn nháy kép
(`quote_cypher` render `"`; route chứa `{code}` trong string đã bẻ depth counting ở bản đầu).

## Gates — kết quả

- ✅ Scratch ladybug sync Rust-only (`/tmp/sp1-golden`, instance `sp1-golden`, data home riêng):
  **0 dòng delegation; exit 0; `status=success outcome=scanned; component_failures=0`;
  `summary.backend == "rust-native"`** (run18 + final confirm sau clippy-fix).
- ✅ M1: `native_message_scan.graph_upsert == "native"`, **`total_graph_upserted = 2 > 0`**.
- ✅ Graph name: store mở đúng file overlay-derived, named graph `sp1golden` (project_id-derived),
  12 ApiEndpoint rows written; H1 chain asserted ở gate phase-04 (non-masked).
- ✅ Embedded falkordb Q2-FAIL: error actionable nguyên văn 2 lựa chọn + rebuild caveat, exit ≠ 0-path,
  không delegate.
- ✅ Hatch regression: `CORTEX_SYNC_BACKEND=python` → delegation fires, python leg healthy
  (`backend == "python"`, success/scanned, 0 failures — children dùng cùng store fixes).
- ✅ `cargo test -p cortex-sync` xanh (74 lib + integration); `cargo clippy --all-targets -D warnings`
  xanh cho cortex-sync / cortex-graph-writer / analyzer-web-overlays.

## Golden fixtures (healthy re-capture)

- `tests/fixtures/sync-plane-golden/ladybug/summary-full-rust-native-healthy.json` — native leg.
- `tests/fixtures/sync-plane-golden/ladybug/summary-full-python-healthy.json` — python leg (hatch),
  là reference phase-04 cho masked-summary parity (cùng writer behaviour, cùng scratch shape).
- `.cache/sp1-golden/golden-store-healthy.lbug` — store copy đóng sạch sau run native.

## Không làm / deliberate

- Không đổi `cortex-dev/src/cmds/{init,sync}.rs` — dev overlay đã set `LADYBUG_PATH`/`--ladybug-graph`
  đúng; phase-05 mới đụng flip/runbook.
- `CORTEX_DATA_HOME` trong scratch config: resolve_storage đọc được từ config key lẫn env — dùng env
  block như thật.
