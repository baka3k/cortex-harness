# Phase 1: Labeled MATCH + Missing Indexes

## Goal

Biến mọi unlabeled `MATCH (a {id: ...})` thành labeled `MATCH (a:Function {id: ...})` để FalkorDB dùng index thay vì full-scan. Thêm 2 index thiếu (Namespace, Class). Đây là fix impact lớn nhất với risk thấp nhất.

## Hiện trạng

### Vấn đề 1: write_relations_typed (language_writer.py:1255)

```python
# Hiện tại — group theo rel_type, nhưng MATCH không label
for rel_type, group in groups.items():
    async def write_batch(batch, _rel_type=rel_type):
        query = (
            "UNWIND $rows AS row "
            "MATCH (a {id: row.source_id}), (b {id: row.target_id}) "   # ← FULL SCAN
            f"MERGE (a)-[r:{_rel_type}]->(b) "
            "SET r += row.properties "
            "RETURN count(r) as count"
        )
```

### Vấn đề 2: write_relations (language_writer.py:330)

```python
# Fallback path — cũng không label
query = """
UNWIND $rows AS row
MATCH (source {id: row.source_id})     # ← FULL SCAN
MATCH (target {id: row.target_id})     # ← FULL SCAN
...
"""
```

### Data đã có

Mỗi relation row chứa `source_label` và `target_label`:
```python
# cplus_analyzer.py:4039
buf_relations.append({
    "source_label": "File",
    "target_label": "Function",
    "source_id": "file::src/foo.cpp",
    "target_id": "fn::foo",
    "rel_type": "DECLARES",
    ...
})
```

Labels thực tế từ cplus analyzer: `File`, `Function`, `Type`, `Namespace`, `Field`, `Alias`, `Template`, `Project`, `Resource`, `Event`, `FunctionType`.

## Giải pháp

### Step 1.1: Thêm missing indexes

**File:** `code-tiny/scripts/setup_constraints.py`

Thêm vào `INDEXES` list:
```python
(
    "namespace_id_lookup",
    "CREATE INDEX namespace_id_lookup IF NOT EXISTS FOR (n:Namespace) ON (n.id)",
),
(
    "class_id_lookup",
    "CREATE INDEX class_id_lookup IF NOT EXISTS FOR (c:Class) ON (c.id)",
),
```

**File:** `code-tiny/tools/cplus/cplus_analyzer.py:4397`

Thêm vào `index_queries` list:
```python
"CREATE INDEX namespace_id_lookup IF NOT EXISTS FOR (n:Namespace) ON (n.id)",
"CREATE INDEX class_id_lookup IF NOT EXISTS FOR (c:Class) ON (c.id)",
```

### Step 1.2: Labeled MATCH trong write_relations_typed

**File:** `code-tiny/tools/graph/writer/language_writer.py:1255`

Thay vì group chỉ theo `rel_type`, group theo `(source_label, target_label, rel_type)`:

```python
# BEFORE:
groups: dict = defaultdict(list)
for rel in relations:
    key = rel.get("rel_type", "RELATION")
    groups[key].append(rel)

for rel_type, group in groups.items():
    query = (
        "UNWIND $rows AS row "
        "MATCH (a {id: row.source_id}), (b {id: row.target_id}) "
        f"MERGE (a)-[r:{rel_type}]->(b) ..."
    )

# AFTER:
groups: dict = defaultdict(list)
for rel in relations:
    key = (
        rel.get("source_label") or "",      # ← group thêm theo label
        rel.get("target_label") or "",
        rel.get("rel_type", "RELATION"),
    )
    groups[key].append(rel)

for (src_label, tgt_label, rel_type), group in groups.items():
    # Build labeled query — FalkorDB needs fixed labels for index usage
    src_match = f"(a:{src_label} {{id: row.source_id}})" if src_label else "(a {id: row.source_id})"
    tgt_match = f"(b:{tgt_label} {{id: row.target_id}})" if tgt_label else "(b {id: row.target_id})"
    query = (
        "UNWIND $rows AS row "
        f"MATCH {src_match}, {tgt_match} "
        f"MERGE (a)-[r:{rel_type}]->(b) "
        "SET r += row.properties "
        "RETURN count(r) as count"
    )
```

**Key insight:** FalkorDB không hỗ trợ dynamic label trong Cypher (`MATCH (a:$label)` không work). Phải group rows cùng `(source_label, target_label)` vào chung query để dùng fixed label string.

### Step 1.3: Labeled MATCH trong write_relations

**File:** `code-tiny/tools/graph/writer/language_writer.py:330`

Tương tự — group theo `(source_label, target_label)` trước khi write:

```python
# BEFORE:
async def write_batch(batch):
    query = """
    UNWIND $rows AS row
    MATCH (source {id: row.source_id})
    MATCH (target {id: row.target_id})
    CALL apoc.merge.relationship(...) YIELD rel
    RETURN count(rel) as count
    """

# AFTER:
# Group by (source_label, target_label) pairs
label_groups = defaultdict(list)
for rel in relations:
    src_lbl = rel.get("source_label") or ""
    tgt_lbl = rel.get("target_label") or ""
    label_groups[(src_lbl, tgt_lbl)].append(rel)

for (src_label, tgt_label), label_group in label_groups.items():
    src_match = f"(source:{src_label} {{id: row.source_id}})" if src_label else "(source {id: row.source_id})"
    tgt_match = f"(target:{tgt_label} {{id: row.target_id}})" if tgt_label else "(target {id: row.target_id})"

    async def write_batch(batch, _src_m=src_match, _tgt_m=tgt_match):
        query = f"""
        UNWIND $rows AS row
        MATCH {_src_m}
        MATCH {_tgt_m}
        CALL apoc.merge.relationship(
            source, row.rel_type, {}, row.properties, target, {{}}
        ) YIELD rel
        RETURN count(rel) as count
        """
        # ... existing try/fallback logic ...
```

### Step 1.4: Fallback cho rows thiếu label

Không phải analyzer nào cũng set `source_label`/`target_label`. Handle edge case:

```python
# Rows without labels — fallback to unlabeled (same as current behavior)
unlabeled = [r for r in relations if not r.get("source_label") or not r.get("target_label")]
labeled = [r for r in relations if r.get("source_label") and r.get("target_label")]

# Write labeled group with index-backed MATCH
# Write unlabeled group with current unlabeled MATCH (fallback)
```

## Validation

### Test 1: GRAPH.EXPLAIN

```bash
# Trước fix — unlabeled MATCH
GRAPH.EXPLAIN cplus_graph "MATCH (a {id: 'fn::foo'}) RETURN a"
# Expected: Node By Label Scan (ALL labels) ← SLOW

# Sau fix — labeled MATCH
GRAPH.EXPLAIN cplus_graph "MATCH (a:Function {id: 'fn::foo'}) RETURN a"
# Expected: Index Scan (Function.id index) ← FAST
```

### Test 2: Graph structural equality

```bash
# Count nodes + edges trước fix:
GRAPH.QUERY cplus_graph "MATCH (n) RETURN count(n)"
GRAPH.QUERY cplus_graph "MATCH ()-[r]->() RETURN count(r), type(r)"

# Run fix. Count again. Phải identical.
```

### Test 3: Timing

Profile graph write phase trước/sau trên 2493 C++ files:
```bash
python3 profile_analyzer.py --target /path/to/cpp --language cplus
# Phase: graph write — so sánh wall time
```

## Deliverables

- [ ] 2 index mới: `Namespace(id)`, `Class(id)`
- [ ] `write_relations_typed()`: group theo (source_label, target_label, rel_type)
- [ ] `write_relations()`: group theo (source_label, target_label)
- [ ] Fallback path cho rows thiếu label
- [ ] GRAPH.EXPLAIN confirms Index Scan
- [ ] Graph structure identical trước/sau
- [ ] Graph write time giảm 50%+
