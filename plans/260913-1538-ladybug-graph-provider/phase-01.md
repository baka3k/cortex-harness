# Phase 01: LadybugDriver core — query path, lane, lease

## Context

Tạo driver Ladybug theo đúng contract mà `FalkorDBDriver` (909 dòng) đang phục vụ, kế thừa `CypherGraphDriver` để nhận các high-level Cypher methods dùng chung. Driver mới chưa được wire vào factory (Phase 02) — phase này chỉ dựng lớp + unit test với fake objects, và một spike verifikasi API release hiện hành.

## Requirements

- Kế thừa `CypherGraphDriver` (`code-tiny/tools/graph/core/cypher_driver.py`), return signature `(records, keys, summary)` như falkordb driver.
- Local-only: bắt buộc `path` (directory). Không nhận uri/host/port — raise `ValueError` fail-closed nếu có.
- `StorageLease` với `backend="ladybug"` (cùng cơ chế portalocker hiện có — `lease.py` không cần sửa).
- `BoundedLane(concurrency=1)` + daemon-thread executor pattern: copy mô hình `_run_in_executor`/`_inflight_native_futures`/deferred close của `falkordb_driver.py:497-547` (embedded call có thể wedge thread — không được dùng ThreadPoolExecutor joinable).
- Query timeout: `conn.set_query_timeout(...)` nếu API có; env `LADYBUG_QUERY_TIMEOUT_MS` default 120000 (mirror `FALKORDB_QUERY_TIMEOUT_MS`).
- Env `LADYBUG_BUFFER_POOL_SIZE` pass vào `Database(..., buffer_pool_size=...)` khi có giá trị.

## Spike (bắt buộc trước khi code, ghi kết quả vào file này)

Đã chạy trên release **ladybug 0.20.4** (PyPI), Python 3.12, macOS arm64. Kết quả:

1. **Import name**: `import ladybug` (module `ladybug`; exposes `Database`, `Connection`, `AsyncConnection`, `QueryResult`, `PreparedStatement`, `Type`). Wheels: `macosx_15_0_arm64` + `macosx_15_0_x86_64` (**floor macOS 15.0**), `manylinux_2_26/2_27` + musllinux, `win_amd64` + `win_arm64`. Python 3.10–3.14.
2. **`Database(database_path, *, buffer_pool_size=0, max_num_threads=0, read_only=False, auto_checkpoint=True, enable_multi_writes=False, ...)`**: auto-create DB file khi chưa tồn tại (write mode) nhưng **KHÔNG tạo parent directory** → driver phải `mkdir(parents=True)`. DB là **1 file** (vd `graph.lbug`), không phải directory. `db.close()` có; không có `destroy()`. Single-writer được enforce bằng OS file lock ("Could not set lock on file"); **read-only mở đồng thời với writer OK** (đọc thấy committed data, WAL-aware).
3. **`Connection(db)`; `conn.execute(query, parameters)`**: parameters phải là dict hoặc None; `$name` style ✓. `QueryResult`: `get_column_names()`, `has_next()/get_next()`, `get_all()`, `get_num_tuples()`, `get_execution_time()`, `get_as_df()`. `conn.set_query_timeout(timeout_in_ms)` ✓ tồn tại; `conn.interrupt()` cũng có.
4. **Value shape**: node → plain dict `{'_ID': {'offset','table'}, '_LABEL': 'Person', <props...>}`; rel → `{'_SRC': {...}, '_DST': {...}, '_LABEL': 'Knows', '_ID': {...}, <props...>}`.
5. **MERGE: OK** (MERGE + SET upsert hoạt động). **Wildcard rel table `FROM * TO *`: KHÔNG hỗ trợ** (parser exception) → phase-04 phải register rel table per-pair từ manifest + auto-register on error.
6. **FTS**: cần `INSTALL FTS; LOAD EXTENSION FTS;` rồi `CALL CREATE_FTS_INDEX('<Table>', '<idx>', ['<prop>'])` (không cần RETURN) và `CALL QUERY_FTS_INDEX('<Table>', '<idx>', '<query>') RETURN node, score`. Re-create → "Index <idx> already exists in table <T>". Vector index: chưa dùng trong plan này.
7. **`datetime()` KHÔNG tồn tại**; `timestamp('<ISO>')` parse ISO → datetime ✓ → normalize layer phải rewrite `datetime()` → `timestamp($param)`. `STARTS WITH`, `CONTAINS`, `IN $list`, `UNWIND $rows`: OK hết.
8. **`CALL show_tables() RETURN *`** (RETURN * bắt buộc) → cols `[id, name, type(NODE|REL), database name, comment]`. **`CALL show_indexes() RETURN *`** → cols `[table_name, index_name, index_type(ART|HASH), property_names, extension_loaded, index_definition]`; mỗi table có sẵn `_PK` HASH. **Range index KHÔNG hỗ trợ trên 0.20.4** ("HASH indexes are currently supported only on node primary keys"); **ART index OK**: `CREATE ART INDEX <name> FOR (p:<Table>) ON (p.<prop>)` (composite multi-prop OK, name bắt buộc); re-create → "<name> already exists in catalog". `set_query_timeout` ✓ (mục 3).
9. **COPY FROM**: `COPY <Tbl> FROM $df` với pandas DataFrame param ✓; append vào bảng có dữ liệu OK; duplicate PK → raise "Copy exception: Found duplicated primary key value ..." hoặc với `(ignore_errors=true)` **SKIP** (không upsert, property stale giữ nguyên) — skipped rows đếm được qua `CALL show_warnings() RETURN *` (cols `[query_id, message, file_path, line_number, skipped_line_or_record]`).
10. **`EXPORT DATABASE '<dir>'`** ✓ → output: `schema.cypher`, `copy.cypher`, `index.cypher`, `<Table>.parquet` — xác nhận tiềm năng bundle v2 cho plan full-cutover.

Spike bổ sung: read-only trên file chưa tồn tại → "Cannot create an empty database under READ ONLY mode."; write trên read-only → "Cannot execute write operations in a read-only database!"; `ALTER TABLE <T> ADD <prop> <type>` OK; `DROP INDEX` syntax không rõ → không dùng.

**Phát hiện thêm khi implement (quan trọng):** Ladybug connection có **implicit prepared-statement cache** keyed by `(query string, param signature)` (`_pybind_implicit_prepared_cache`). Sau khi auto-DDL ALTER thêm property, retry cùng query string vẫn tái sử dụng stale plan → lặp lại binder error cũ dù catalog đã cập nhật. Driver phải clear cache (`connection._pybind_implicit_prepared_cache.clear()`, guarded getattr) sau mỗi DDL statement. Query không có parameter (execute qua `py_connection.query`) không bị ảnh hưởng. Single-writer cross-process: Ladybug tự enforce OS file lock ("Could not set lock on file"); read-only open đồng thời với writer hoạt động (đọc thấy committed data).

## Implementation steps

1. Tạo `code-tiny/tools/graph/driver/ladybug_driver.py`:
   - `_open_local_ladybug(path, *, read_only)` — lazy import, friendly `ImportError` (mirror `_open_local_falkordb`, `falkordb_driver.py:182-210`), mkdir parents, chmod 0700 trên POSIX khi tạo mới.
   - `LadybugDriver.__init__(path, *, graph, instance_id, owner_id, additional_paths, query_timeout_ms, read_only_siblings)` — acquire lease → open DB → cache `_conn`.
   - `execute_query_sync(query, parameters, database)` — normalize query (reuse shared `_normalize_query`: cần tách `_CALL_IMPORTING_SUBQUERY_RE` + datetime rewrite ra module dùng chung, ví dụ `tools/graph/core/query_normalize.py`; import lại trong falkordb driver để tránh duplicate), retry policy copy `falkordb_driver.py:562-615` nhưng exception classes là Ladybug/runtime errors thay vì `redis.exceptions`.
   - `_normalize_ladybug_value` — node → dict props + `_label`/`_id`; rel → props + `_type`/`_start_id`/`_end_id`; mirror `falkordb_driver.py:145-179`.
   - `close()` + `_close_resources()` với deferred-close semantics.
   - Stubs ném `NotImplementedError` cho `create_indexes`/`inspect_indexes`/`list_databases`/`list_relationship_types`/FTS search — implement ở Phase 04.
   - `provider` property → `GraphProvider.LADYBUG`.
2. Tách `query_normalize.py` và rewire `FalkorDBDriver._prepare_falkordb_query` dùng chung.
3. Unit tests `code-tiny/tests/test_ladybug_driver_local.py` (fake Database/Connection, pattern `tests/test_falkordb_driver_local.py`): lease acquire/release, query round-trip, normalization, retry matrix, timeout mutation → `AmbiguousWriteTimeoutError` (import từ falkordb module hoặc move sang shared — quyết định: move `AmbiguousWriteTimeoutError` sang `core/errors`-style module, re-export ở falkordb driver).
4. Integration test opt-in `@pytest.mark.ladybug`: round-trip CREATE/MATCH trên directory tmp.

## Acceptance

- `pytest code-tiny/tests/test_ladybug_driver_local.py` xanh không cần cài ladybug (fake-based).
- Integration test xanh trên máy có package (macOS arm64).
- Spike answers điền xong vào mục Spike của file này.
