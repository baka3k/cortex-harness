# Phase 2: Batch Size Tuning + Skip Unresolved Calls

## Goal

Giảm số lượng Redis round-trips cho CALLS edges từ ~624 xuống ~62. Loại bỏ wasted queries cho unresolved calls.

## Hiện trạng

```python
# cplus_analyzer.py:4669
parser.add_argument("--neo4j-calls-batch-size", type=int, default=50)

# 31,205 calls / 50 = 624 queries
# Mỗi query: Redis round-trip (~0.1ms localhost) + FalkorDB MATCH+MATCH+MERGE (~10-50ms)
# = 624 × (0.1 + 30) ≈ 18.7s chỉ cho CALLS edges
```

### Vấn đề 1: Batch quá nhỏ

50 edges/batch cho FalkorDB là保守 quá. Redis/FalkorDB xử lý 500-1000 rows/UNWIND dễ dàng. Socket timeout đã set 120s (falkordb_driver.py:175).

### Vấn đề 2: Unresolved calls gửi vô ích

```python
# language_writer.py:1309 — write_calls_with_site()
UNWIND $rows AS row
CALL {
    WITH row
    MATCH (caller:Function {id: row.caller_id})    # ← tìm thấy
    RETURN caller
}
CALL {
    WITH row
    MATCH (callee:Function {id: row.callee_id})    # ← callee_id = None → NOT FOUND
    RETURN callee
}
MERGE (caller)-[r:CALLS {site_id: row.site_id}]->(callee)  # ← silent fail, edge bị drop
```

Calls với `callee_id = None` (unresolved cross-file calls) **luôn bị silent drop** — MATCH trả về rỗng, MERGE không thực hiện. Nhưng vẫn **tiêu tốn 1 round-trip** cho mỗi batch chứa chúng.

## Giải pháp

### Step 2.1: Tăng default batch size

**File:** `code-tiny/tools/cplus/cplus_analyzer.py:4669`

```python
# BEFORE:
parser.add_argument("--neo4j-calls-batch-size", type=int, default=50)

# AFTER:
parser.add_argument("--neo4j-calls-batch-size", type=int, default=500)
```

**Impact:** 624 queries → 62 queries (10x ít round-trips).

**Risk:** Batch 500 rows × ~200 bytes/row = ~100KB payload. Redis handle MB-scale payloads routinely. Socket timeout 120s dư sức.

### Step 2.2: Filter unresolved calls trước khi gửi

**File:** `code-tiny/tools/cplus/cplus_analyzer.py` — trước khi flush buf_calls

```python
# Trong _flush_write_buffers(), trước khi gọi write_calls_with_site:
if buf_calls:
    # Filter out unresolved calls — they'd be silently dropped by MATCH anyway
    resolved_calls = [c for c in buf_calls if c.get("callee_id")]
    unresolved_count = len(buf_calls) - len(resolved_calls)
    if unresolved_count > 0 and verbose:
        print(f"[calls] Skipping {unresolved_count} unresolved calls (callee_id=None)")

    if resolved_calls:
        _orig_bs = code_writer.batch_size
        code_writer.batch_size = max(1, neo4j_calls_batch_size)
        try:
            await code_writer.write_calls_with_site(resolved_calls)
        finally:
            code_writer.batch_size = _orig_bs
        _total_calls_written += len(resolved_calls)
    # buf_calls reset bên dưới
```

### Step 2.3: Tăng LanguageCodeWriter default batch_size

**File:** `code-tiny/tools/graph/writer/language_writer.py:83`

```python
# BEFORE:
def __init__(self, driver, database=None, batch_size=1000, verbose=False):

# AFTER:
def __init__(self, driver, database=None, batch_size=2000, verbose=False):
```

**Impact:** Node writes (File, Function, Type, ...) cũng giảm round-trips. 2000 rows/batch × ~500 bytes/row = ~1MB — trong capacity Redis.

## Validation

```bash
# Count calls queries trước/sau:
# Trước: 31,205 / 50 = 624 queries
# Sau:   31,205 / 500 = 62 queries (cũng giảm thêm nếu skip None)

# Verify graph:
GRAPH.QUERY cplus_graph "MATCH ()-[r:CALLS]->() RETURN count(r)"
# Phải >= số resolved calls (unresolved alreads bị drop trước và sau)
```

## Deliverables

- [ ] Calls batch default 50 → 500
- [ ] Filter callee_id=None trước khi gửi
- [ ] LanguageCodeWriter batch 1000 → 2000
- [ ] Graph CALLS edge count unchanged (unresolved đã bị drop trước và sau)
