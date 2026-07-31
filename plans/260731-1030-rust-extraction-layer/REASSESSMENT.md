# Reassessment: Overall plan — CAN graph write be improved?

## Full picture

```
CURRENT (estimated for 2493 C++ files):

Phase                    Time         Cost driver                       Rust-able?
─────────────────────────────────────────────────────────────────────────────────────────────
Parse + Extract          92.8s        Python AST walk (CPU)             ✅ Rust 8-thread
Semantic enrichment      80.5s        Regex heuristics (CPU)            ✅ Rust regex
Call resolution          ~5s          HashMap matching (CPU)            ✅ Rust
─────────────────────────────────────────────────────────────────────────────────────────────
Graph write (FalkorDB)   ~40-60s      Network I/O + Cypher (I/O)        ❌ Keep Python
Embedding                ~30-60s      Model inference (ML)              ❌ Keep Python
Qdrant upsert            ~10s         Network I/O                       ❌ Keep Python
─────────────────────────────────────────────────────────────────────────────────────────────
TOTAL                    ~260-300s
```

## Layer 2: Graph write — 3 SAFE optimizations (confirmed by FalkorDB research)

### Fix 2a: Redis pipelining — eliminate network round-trips

**Research finding:** falkordb-py `graph.query()` = 1 Redis command = 1 TCP round-trip per query. But the underlying `redis.Redis` connection supports **pipelining** (queue N commands, send in 1 TCP write, read all responses).

```python
# CURRENT: sequential, 1 round-trip per query
for query, params in batched_queries:
    graph.query(query, params)        # TCP send + wait + read, repeat

# PIPELINED: 1 round-trip for ALL queries (sequential, NOT parallel)
pipe = redis_client.pipeline(transaction=False)
for query, params in batched_queries:
    pipe.execute_command("GRAPH.QUERY", graph_name, full_query, "--compact")
results = pipe.execute()              # 1 TCP write, N reads
```

**Important:** FalkorDB serializes writes per-graph (1 writer at a time, FIFO queue). Pipelining does **NOT parallelize writes** — it eliminates **network RTT between sequential commands**. With localhost Redis, RTT ~0.1ms; over network 1-10ms. For 300+ queries × 0.1-10ms = **30-3000ms pure network overhead eliminated.**

**Risk: near 0.** Same queries, same order, same correctness.

### Fix 2b: Index verification — THE hidden bottleneck?

**Research finding:** `MERGE (n:Label {prop: value})` does an implicit MATCH first. **Without an index on `Label(prop)`, the MATCH does a FULL SCAN of all nodes with that label.**

Current code MERGEs on these keys — ALL need range indexes:
- `Function(id)`, `Type(id)`, `File(id)`, `Namespace(id)`, `Class(id)`
- `Field(id)`, `Alias(id)`, `Template(id)`, `FunctionType(id)`
- Plus all `MATCH (source {id: row.source_id})` patterns

**If ANY index missing → FalkorDB full-scans 10K+ nodes per MERGE row.**

**Action:** Run `GRAPH.EXPLAIN` on each MERGE query. If plan shows `Node By Label Scan` instead of `Index Scan` → add missing index. **This could be the single biggest graph-write fix with zero risk.**

### Fix 2c: Skip unresolved calls + increase batch size

- Filter out `callee_id = None` calls before sending (eliminates wasted round-trips)
- Increase calls batch size 50 → 500 (reduces 624 → 62 queries)

## Conclusion: NOT just Rust — 3 layers

```
Layer 1: Rust extraction (largest single win)
  Parse+Semantic:  173s → ~14s     (92% reduction)

Layer 2: Graph write tuning (3 fixes, all SAFE)
  Graph write:     ~50s → ~15-25s   (50-70% reduction)
  2b: Index verification      ← possibly biggest, 0 code change
  2a: Redis pipelining        ← eliminate network RTT
  2c: Skip unresolved + batch  ← quick win

Layer 3: Embedding tuning
  Embedding:       ~45s → ~25s     (batch_size 4→32)

COMBINED: ~260-300s → ~55-85s (70% reduction)
```

## Priority order

1. **Fix 2b FIRST (index verification)** — 0 code change risk, just run EXPLAIN + add indexes
2. **Layer 1 (Rust extraction)** — biggest win
3. **Fix 2a (pipelining)** — after Rust
4. **Fix 2c + Layer 3** — quick wins
