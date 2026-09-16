# Phase 7: Ingest Pipeline

## Mục tiêu
Đảm bảo code-tiny graph writer và doc-tiny graphrag ingest hoạt động với LadybugDB.

## Key Challenges

### 1. Schema-first requirement
LadybugDB cần `CREATE NODE TABLE` / `CREATE REL TABLE` trước khi CREATE/MERGE data.

**Solution:** `ensure_schema()` chạy trước mọi ingest batch. Schema manifest (`CODE_GRAPH_SCHEMA`) → DDL translation.

### 2. `SET n = $map` not supported
FalkorDB/Neo4j hỗ trợ `SET n = $properties` (map assignment). LadybugDB KHÔNG.

**Solution:** Rewrite batch_write_nodes/edges:
```python
# Thay vì:
UNWIND $nodes AS node CREATE (n:Label) SET n = node

# Dùng:
UNWIND $nodes AS node CREATE (n:Label) SET n.prop1 = node.prop1, n.prop2 = node.prop2, ...
```

Hoặc dùng LadybugDB `COPY FROM` cho bulk load (nếu performance critical).

### 3. CALL subquery not supported
Một số queries dùng `CALL { WITH var ... }` → LadybugDB reject.

**Solution:** 
- Identify affected queries trong writer/operations
- Rewrite thành WITH-based hoặc flatten
- Nếu không thể rewrite → raise clear error + fallback

### 4. `REMOVE` clause
LadybugDB: `SET n.prop = NULL` thay vì `REMOVE n.prop`.

**Solution:** `_normalize_ladybug_query()` handles this automatically.

## Code-tiny Ingest Flow

### `tools/graph/writer/language_writer.py`
- Gọi `driver.ensure_schema()` trước khi write
- `batch_write_nodes()` → LadybugDB driver override (explicit property SET)
- `batch_write_edges()` → LadybugDB driver override

### `tools/graph/operations/*.py`
- Hầu hết dùng `execute_query()` → auto-normalize qua driver
- Kiểm tra từng operation file cho CALL subquery usage

### `tools/sync/incremental_sync.py`
- Uses MATCH count + SET `project_id_normalized`
- LadybugDB: MATCH + SET individual properties → OK

## Doc-tiny Ingest Flow

### `doc-tiny/graphrag_ingest_langextract.py`
- Uses `FalkorDBGraphStore` → cần `LadybugDBGraphStore`
- `create_graph_store_from_args()` dispatches by provider → Phase 5 đã handle
- Entity/relationship creation via `session.run()` → LadybugDB session wraps driver

### `doc-tiny/graphrag_query_langextract.py`
- Query side → same driver, same project_id rules

## Tests
- `test_ladybug_code_ingest`: ingest small codebase → verify nodes/edges in .lbdb
- `test_ladybug_doc_ingest`: ingest documents → verify entities/relations
- `test_ladybug_schema_auto_create`: ensure_schema creates all tables
- `test_ladybug_batch_write`: batch_write_nodes with 100+ nodes
- `test_ladybug_incremental_sync`: sync cycle works

## Acceptance
- `dev sync code` với `GRAPH_PROVIDER=ladybug` → code graph ingested
- `dev sync doc` với `GRAPH_PROVIDER=ladybug` → doc graph ingested
- Data queryable sau ingest (verify via MATCH queries)
