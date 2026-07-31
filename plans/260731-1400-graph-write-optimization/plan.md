---
title: "Graph Write Optimization — FalkorDB"
status: draft
created: 2026-07-31
mode: hi-plan
source: profiling-driven FalkorDB research + code audit
target: code-tiny/tools/graph/writer/language_writer.py, code-tiny/tools/graph/driver/falkordb_driver.py, code-tiny/scripts/setup_constraints.py
scope: Fix unlabeled MATCH full-scans, add missing indexes, batch tuning, pipelining
blockedBy: []
relatedPlans: [neo4j-to-falkordb-migration, 260731-1030-rust-extraction-layer]
---

# Graph Write Optimization — FalkorDB

## Overview

Graph write phase mất ~40-60s trên 2493 file C++ với dependency chồng chéo. Nghiên cứu phát hiện **5 vấn đề cụ thể**, ordered theo impact:

| # | Vấn đề | Impact | Risk | Files cần sửa |
|---|--------|--------|------|---------------|
| 1 | **Unlabeled MATCH** — full scan toàn graph mỗi relation row | 🔴 CỰC LỚN | Thấp | language_writer.py |
| 2 | **Missing indexes** — Namespace(id), Class(id) | 🔴 LỚN | Gần 0 | setup_constraints.py |
| 3 | **Calls batch size 50** — 624 round-trips | 🟡 Vừa | Gần 0 | cplus_analyzer.py |
| 4 | **Fake async** — execute_query block event loop | 🟡 Vừa | Thấp | falkordb_driver.py |
| 5 | **No pipelining** — mỗi query = 1 TCP round-trip | 🟡 Vừa | Thấp | falkordb_driver.py |

**Tất cả 5 fix giữ nguyên tính sequential, không parallelize, không thay đổi thứ tự write.** An toàn cho dependency correctness.

## Verified Findings

### Finding 1: Unlabeled MATCH = full scan toàn graph

**Vị trí:** `language_writer.py` 2 chỗ

```python
# Line 341 — write_relations() fallback
UNWIND $rows AS row
MATCH (source {id: row.source_id})        # ← KHÔNG CÓ LABEL!
MATCH (target {id: row.target_id})        # ← KHÔNG CÓ LABEL!

# Line 1268 — write_relations_typed()
"UNWIND $rows AS row "
"MATCH (a {id: row.source_id}), (b {id: row.target_id}) "   # ← KHÔNG CÓ LABEL!
f"MERGE (a)-[r:{_rel_type}]->(b) "
```

**Tại sao chậm:** FalkorDB cần label để chọn index. Không label = quét **tất cả nodes trong graph** (File, Function, Type, Namespace, Class, Field, ... 50K+ nodes) cho mỗi `MATCH`. Mỗi relation row = 2 full scans. Với hàng nghìn relations = hàng chục nghìn full scans.

**Data đã có sẵn:** Mỗi relation row chứa `source_label` và `target_label` (verified — cplus_analyzer.py gán cho mọi RelationEdge). Chỉ cần đưa vào Cypher.

### Finding 2: Missing indexes

**setup_constraints.py** tạo index cho Type, Package, Field, Alias, Template, FunctionType, Message, ... **Nhưng thiếu:**

| Label | MERGE/MATCH trong writer? | Index có? |
|-------|--------------------------|-----------|
| `Namespace` | `MERGE (n:Namespace {id: row.id})` (language_writer:667) | ❌ THIẾU |
| `Class` | `MERGE (c:Class {id: row.id})` (language_writer:827) | ❌ THIẾU |

Mỗi MERGE Namespace/Class không có index = full scan tất cả nodes cùng label.

**cplus_analyzer.py:4397** tạo 6 index riêng nhưng **không bao gồm:**
- Namespace(id) — MERGE ở language_writer
- Class(id) — MERGE ở language_writer
- Function(id) ← có ✅
- File(id) ← có ✅

### Finding 3: Calls batch size quá nhỏ

```python
# cplus_analyzer.py:4669
neo4j_calls_batch_size: int = 50    # default

# 31,205 calls / 50 = 624 queries
# Mỗi query = Redis round-trip + FalkorDB MATCH+MATCH+MERGE
```

### Finding 4: Fake async

```python
# falkordb_driver.py:231-237
async def execute_query(self, query, parameters=None, database=None):
    return self.execute_query_sync(query, parameters, database)
    #     ^^^^^^^^^^^^^^^^^^^^ SYNCHRONOUS — blocks event loop
```

`graph.query()` gọi `client.execute_command()` — Redis blocking I/O. Async wrapper chỉ delegate, không release event loop.

### Finding 5: No pipelining

falkordb-py `graph.query()` = 1 Redis command = 1 TCP round-trip. `redis.Redis` connection hỗ trợ `pipeline(transaction=False)` — queue N commands, send 1 TCP write, read all responses.

**Important:** FalkorDB serialize writes per-graph (1 writer at a time, FIFO). Pipelining **không parallelize writes** — chỉ loại bỏ network RTT giữa sequential commands. RTT localhost ~0.1ms; 300+ queries × 0.1ms = 30ms+ overhead thuần network.

## Phase Breakdown

### Phase 1 — Labeled MATCH + missing indexes (HIGHEST IMPACT, LOWEST RISK)

**Goal:** Biển mọi unlabeled MATCH thành labeled MATCH, thêm 2 index thiếu.

**Thay đổi:**

1. `language_writer.py:write_relations_typed()` (line 1268):
```python
# BEFORE:
"MATCH (a {id: row.source_id}), (b {id: row.target_id}) "

# AFTER:
f"MATCH (a:{row['source_label']} {{id: row.source_id}}), " \
f"(b:{row['target_label']} {{id: row.target_id}}) "
```

**Caveat:** FalkorDB không cho dynamic label trong Cypher `MATCH (a:{label})`. Cần group relations theo `(source_label, target_label)` pair, mỗi group 1 query với fixed labels. Code hiện tại đã group theo `rel_type` — mở rộng thêm grouping theo label pair.

2. `language_writer.py:write_relations()` (line 341): tương tự — group theo label pair.

3. `setup_constraints.py`: thêm 2 index:
```
CREATE INDEX namespace_id_lookup IF NOT EXISTS FOR (n:Namespace) ON (n.id)
CREATE INDEX class_id_lookup IF NOT EXISTS FOR (c:Class) ON (c.id)
```

**Risk:** THẤP. `source_label`/`target_label` đã có trong data. Labeled MATCH dùng index → faster. Không thay đổi correctness — cùng nodes, cùng edges.

**Validation:**
- Graph structure trước/sau fix phải identical (node count, edge count, relation types)
- Run `GRAPH.EXPLAIN` trên labeled query → confirm `Index Scan` thay vì `Node By Label Scan`

### Phase 2 — Batch size + skip unresolved calls

**Goal:** Giảm số lượng queries cho CALLS edges.

**Thay đổi:**

1. `cplus_analyzer.py:4669`: default `neo4j_calls_batch_size` 50 → 500
2. Filter `callee_id = None` trước khi gửi:
```python
buf_calls = [c for c in buf_calls if c.get("callee_id")]
```

**Risk:** Gần 0. Batch size là config parameter. Skip None callee đúng behavior — chúng bị silent-drop anyway.

### Phase 3 — Real async + pipelining

**Goal:** unblock event loop, loại bỏ network RTT overhead.

**Thay đổi:**

1. `falkordb_driver.py:execute_query()`:
```python
async def execute_query(self, query, parameters=None, database=None):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, self.execute_query_sync, query, parameters, database
    )
```

2. Thêm `execute_queries_pipelined()` method cho batch write:
```python
def execute_queries_pipelined(self, queries: List[Tuple[str, dict]], database=None):
    """Queue multiple GRAPH.QUERY commands in 1 Redis pipeline."""
    redis_client = self._client.connection
    pipe = redis_client.pipeline(transaction=False)
    for query, params in queries:
        graph = self._graph_for(database)
        query, params = _prepare_falkordb_query(query, params)
        param_header = graph._build_params_header(params)
        full_query = param_header + query
        pipe.execute_command("GRAPH.QUERY", graph.name, full_query, "--compact")
    return pipe.execute()
```

3. `language_writer.py:write_all()`: pipeline các independent node writes trong cùng flush.

**Risk:** THẤP cho run_in_executor (cùng query, cùng order). THẤP cho pipelining (FalkorDB xử lý sequential anyway, chỉ client-side batching).

**Caveat:** `_build_params_header` là private method của falkordb Graph. Cần verify nó stable, hoặc replicate logic (format: `CYPHER key1=value1 key2=value2 `).

## Performance Projection

| Phase | Fix | Graph write time | Cumulative |
|-------|-----|-----------------|------------|
| Current | — | ~50s | ~50s |
| Phase 1 | Labeled MATCH + indexes | ~15-20s | ~15-20s |
| Phase 2 | Batch 500 + skip None | ~12-17s | ~12-17s |
| Phase 3 | Async + pipelining | ~10-15s | ~10-15s |

Phase 1 cho **impact lớn nhất** (50-70% reduction). Phases 2-3 cho thêm 15-30%.

## Risk Register

| Risk | Severity | Mitigation |
|------|----------|------------|
| Dynamic label trong Cypher — FalkorDB syntax | Medium | Group relations theo label pair; mỗi group fixed-label query |
| `_build_params_header` private API thay đổi | Low | Pin falkordb version; replicate logic nếu break |
| run_in_executor + redis-py thread safety | Low | redis.Redis is thread-safe; ConnectionPool handles concurrency |
| Large batch timeout (500 rows × large payload) | Low | Socket timeout đã set 120s; 500 rows << Redis capacity |
| Relation rows thiếu source_label (edge case) | Low | Fallback: nếu label missing, dùng unlabeled MATCH cho row đó |

## Success Criteria

- [ ] Phase 1: `GRAPH.EXPLAIN` trên labeled MATCH cho thấy `Index Scan`
- [ ] Phase 1: Graph structure (node/edge count) identical trước/sau
- [ ] Phase 2: Calls query count giảm 10x (624 → 62)
- [ ] Phase 3: Event loop không block trong graph write
- [ ] Total: Graph write time giảm 50%+ trên 2493 C++ files
