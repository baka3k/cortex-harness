# Phase 04: Schema bootstrap + auto-DDL + index/FTS + introspection gom về driver

> **Status: implemented (2026-09-13).** Bootstrap tạo node tables từ manifest (với base columns id/name/file_path/path/qualified_name/project_id/project_id_normalized), rel tables từ registry, auto-DDL gated `CORTEX_GRAPH_AUTO_DDL` xử lý đủ 3 loại binder error (missing property / missing table / "Cannot bind X as a relationship pattern label"), prepared-statement cache invalidation sau mỗi DDL, `list_labels`/`list_relationship_types`/`fulltext_query_nodes` trên cả falkordb + ladybug driver, rewiring xong java/cplus/android/dead_code_report + setup_constraints (apply_ladybug_schema) + preflight dùng inspect_indexes chung.
>
> **Deviations:** (1) MCP fulltext sites (`db.index.fulltext.queryNodes` trong java/fastmcp/cplus/android MCP) giữ nguyên raw Cypher — đã có sẵn CONTAINS fallback nên ladybug degrade đúng, không refaktor 8 WHERE bespoke; driver API `fulltext_query_nodes` đã có cho adoption sau. Acceptance "grep CALL db\. = 0" do đó KHÔNG đạt tuyệt đối — còn 8 site MCP fulltext + neo4j SHOW (đã có sẵn từ trước plan này). (2) Range index không tồn tại trên ladybug 0.20 → preflight map ART→"range" và `_PK`→"range"(id) để reuse preflight states. (3) Bootstrapped label `Table` là reserved keyword trên ladybug — cần backtick ở mọi query (đã quote trong introspection).

## Context

Rủi ro High số 1 của hi-predict: FalkorDB schemaless vs Ladybug static schema (`CREATE NODE TABLE ... PRIMARY KEY`, property phải khai báo trước, thêm property = `ALTER TABLE`). Phase này làm write-path parity: analyzer ghi được node/rel/index mà không cần biết provider, và mọi dialect introspection nằm trong driver.

## Requirements

### Schema bootstrap (`ladybug_driver.py`, hook tại write-open của Phase 03)
- Khi mở DB write-mode lần đầu: ensure node tables từ `CODE_GRAPH_SCHEMA` (`tools/graph/schema/manifest.py:129`) + registry label/prop của các analyzer: `CREATE NODE TABLE IF NOT EXISTS <Label>(<props...>, PRIMARY KEY(id))`. Type mapping: string→STRING, list→STRING[]/FLOAT[] tùy manifest, số→FLOAT/INT64, bool→BOOL, datetime→TIMESTAMP.
- **Auto-DDL** gated `CORTEX_GRAPH_AUTO_DDL` (default on): bắt lỗi "property X does not exist"/"table does not exist" khi execute write → `ALTER TABLE <label> ADD PROPERTY <name> <type>` (infer type từ param Python: str/bool/int/float/list) → retry 1 lần; luôn `logger.warning` (chống mất dữ liệu âm thầm — worst case hi-predict). Cờ off → raise fail-closed.
- **Rel tables:** spike Phase 01 quyết định: nếu Ladybug hỗ trợ `CREATE REL TABLE <T> (FROM * TO *)` → dùng wildcard 1 lần per rel-type; không thì register per-pair từ manifest + auto-register on first error (cùng cơ chế gated auto-DDL).
- Idempotent: bootstrap chạy `IF NOT EXISTS`/kiểm catalog mỗi lần mở, cache trong process.

### Index/FTS (`create_indexes`, `inspect_indexes`)
- `create_indexes` (`falkordb_driver.py:638-682` parity): range → native `CREATE INDEX` (hoặc statement tương đương theo spike); fulltext → FTS API đã xác minh ở spike. Idempotent "already exists" classification như falkordb.
- `inspect_indexes`: `CALL show_indexes()` → parse ra normalized records (label/properties/index_type/entity_type/status) khớp contract mà `schema/preflight.py` `_READY_STATES`/`_FAILED_STATES` (`preflight.py:15-16`) đang expect; map status Ladybug → "ONLINE"/"FAILED".

### Bulk-load fast path (COPY FROM) — verified trên docs chính thức
- Ladybug cung cấp `COPY FROM` từ CSV/Parquet/JSON/NumPy/**DataFrame** (pandas/polars/pyarrow)/subquery — docs gọi đây là "the fastest way to bulk insert", parallel multi-core, hỗ trợ glob + gzip + `IGNORE_ERRORS`.
- **Semantics khác MERGE (bắt buộc hiểu đúng):** `ignore_errors=true` chỉ **SKIP** row trùng PK, không upsert — property stale sẽ không được update. Vì vậy dual-path:
  - **Initial full ingest / generation mới (bảng rỗng):** analyzer stage nodes/rels vào DataFrame/Parquet → dedupe theo PK (`drop_duplicates` trên key) → `COPY node_tbl FROM df` → xong nodes mới `COPY rel_tbl FROM df` (nodes phải tồn tại trước — requirement của docs). Tránh hoàn toàn MERGE row-by-row qua BoundedLane.
  - **Incremental sync / re-run:** giữ path MERGE hiện có (upsert-by-key), vì COPY không cập nhật property của PK trùng.
- Driver expose `bulk_load(table, frame)` (write-mode only) + pre-condition check "bảng rỗng hoặc caller xác nhận append"; `ignore_errors=true` + `CALL show_warnings()` expose cho caller đếm row bị skip.
- Không thay đổi BoundedLane contract: COPY là 1 statement qua cùng lane.

### Introspection
- `list_relationship_types()`: `CALL show_tables()` filter rel tables → upper() (parity `falkordb_driver.py:753-765`).
- `list_databases()`: enumerate graph directories dưới root + siblings.
- **Gom dialect về driver** — đổi call sites gọi thẳng Falkor dialect:
  - `mcp/java/java_mcp.py:751`, `mcp/cplus/cplus_mcp.py:1091,1124`, `mcp/android/android_mcp.py:902`, `tools/sync/dead_code_report.py:231` → dùng `driver.list_relationship_types()` / thêm `driver.list_labels()` (node tables) vào CypherGraphDriver.
  - Kiểm `db.idx.fulltext.queryNodes` chỉ còn tồn tại trong `falkordb_driver.py` (driver-internal fallback).

### Preflight + setup scripts
- `tools/graph/schema/preflight.py`: branch theo provider (ladybug → inspect qua driver mới); `SchemaEnsureResult` giữ nguyên shape.
- `scripts/setup_constraints.py` (dòng 678/712/733 gọi client APIs): route qua `driver.create_indexes()` thay vì client native.

## Implementation steps

1. Spike-followup: chốt rel-table strategy + FTS statement chính xác (cập nhật Phase 01 spike notes).
2. Bootstrap + auto-DDL trong driver; unit tests: new-label, new-property (auto-DDL on/off), idempotent re-open.
3. Index/FTS + inspect; test khớp preflight states.
4. Introspection + rewiring 5 call sites trên; `grep -rn "CALL db\." --include="*.py" code-tiny` chỉ còn trong `falkordb_driver.py` + `neo4j_driver.py` (nếu neo4j có dialect riêng hợp lệ).
5. Parity integration (marker `@pytest.mark.ladybug`): chạy 1 analyzer thật (vd spring hoặc servlet_jsp nhỏ) end-to-end ghi ladybug → đếm node/rel khớp expectations; chạy `SchemaPreflight`.

## Acceptance

- Toàn bộ `grep "CALL db\."` ngoài driver = 0 (trừ neo4j driver nội bộ).
- Analyzer end-to-end trên ladybug: 0 property bị drop, auto-DDL log đúng mỗi lần ALTER.
- `pytest -m ladybug` xanh; bộ test preflight hiện hữu giữ xanh với falkordb.
