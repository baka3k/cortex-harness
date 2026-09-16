# Phase 6: Project ID Query Rules

## Mục tiêu
Đảm bảo LadybugDB driver tuân thủ đầy đủ R1-R6 của PROJECT_ID_QUERY_RULES.

## Analysis

### R1: Không truyền project_id → query TẤT CẢ
- Cypher predicate: `WHERE ($project_id IS NULL OR n.project_id_normalized STARTS WITH $project_id_normalized)`
- LadybugDB hỗ trợ `STARTS WITH` ✅
- LadybugDB hỗ trợ `$param` syntax ✅
- `prepare_project_scope_parameters()` là provider-neutral → reuse ✅

### R2: Có project_id → scope theo project
- Same predicate, `$project_id` non-null → filter active
- LadybugDB driver pass params qua `conn.execute(query, params)` ✅

### R3: Case-insensitive (casefold)
- `project_id_normalized` field đã được casefold ở ingest time
- Query side: `project_id_lookup_key()` → casefold → pass as `$project_id_normalized`
- LadybugDB: `STARTS WITH` trên string field → case-sensitive by default
- **KHÔNG ảnh hưởng** vì data đã stored as casefold ✅

### R4: LIKE/prefix match
- `STARTS WITH` operator → LadybugDB supports ✅
- `project_id_scope_keys()` → expand prefix → pass as list → `any()` match
- Cần verify: LadybugDB supports `IN` operator hoặc `$param IN list`

### R5: Local = Remote parity
- LadybugDB chỉ có embedded mode → local only
- Không có remote mode → R5 trivially satisfied ✅

### R6: WRITE PATH exact match
- Write paths dùng `=` hoặc exact key lookup
- LadybugDB: `MATCH (n {id: $id})` → exact match ✅
- `batch_write_nodes/edges` → CREATE/MERGE with exact IDs ✅

## Implementation

### LadybugDB driver: parameterized query handling
```python
def _prepare_ladybug_query(query, parameters):
    """Prepare query for LadybugDB execution."""
    query = _normalize_ladybug_query(query)
    params = prepare_project_scope_parameters(query, parameters)
    return query, params
```

### Verify STARTS WITH support
LadybugDB docs confirm `STARTS WITH` is supported (standard openCypher).
Test: `MATCH (n) WHERE n.project_id_normalized STARTS WITH 'bank' RETURN n`

### Verify $param IN list support
If LadybugDB doesn't support `IN $list_param`, cần rewrite thành OR chain:
```python
# Alternative if IN not supported:
WHERE n.project_id_normalized STARTS WITH $key0
   OR n.project_id_normalized STARTS WITH $key1
   OR ...
```

## Tests
- `test_ladybug_project_id_unscoped`: project_id=None → all results
- `test_ladybug_project_id_exact`: project_id="bank_android" → only that project
- `test_ladybug_project_id_prefix`: project_id="bank" → bank_android + bank_cplus
- `test_ladybug_project_id_casefold`: project_id="Bank" → same as "bank"
- `test_ladybug_write_exact_match`: write to project A doesn't affect project B

## Acceptance
- Tất cả 4 rules (R1-R4) pass trên LadybugDB
- R5 trivially satisfied (embedded only)
- R6 write isolation verified
