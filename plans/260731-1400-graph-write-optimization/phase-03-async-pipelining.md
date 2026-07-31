# Phase 3: Real Async + Redis Pipelining

## Goal

Unblock event loop với `run_in_executor`, loại bỏ network RTT overhead với Redis pipelining.

## Hiện trạng

### Vấn đề 1: Fake async

```python
# falkordb_driver.py:231-237
async def execute_query(self, query, parameters=None, database=None):
    return self.execute_query_sync(query, parameters, database)
    #     ^^^^^^^^^^^^^^^^^^^^ SYNCHRONOUS
```

`graph.query()` gọi `client.execute_command()` — Redis blocking I/O. Trong lúc chờ Redis response (10-50ms), event loop bị block hoàn toàn. Không task nào chạy được.

### Vấn đề 2: Mỗi query = 1 TCP round-trip

```python
# Sequence hiện tại (sequential, blocking):
query 1: TCP send → wait Redis → TCP read → parse    (RTT ~0.1ms + exec ~10ms)
query 2: TCP send → wait Redis → TCP read → parse    (RTT ~0.1ms + exec ~10ms)
...
query 300: TCP send → wait Redis → TCP read → parse  (RTT ~0.1ms + exec ~10ms)

# Total network overhead: 300 × 0.1ms = 30ms (localhost)
# Over network: 300 × 5ms = 1500ms
```

falkordb-py không expose pipelining, nhưng underlying `redis.Redis` connection hỗ trợ.

## Giải pháp

### Step 3.1: run_in_executor cho execute_query

**File:** `code-tiny/tools/graph/driver/falkordb_driver.py:231`

```python
# BEFORE:
async def execute_query(self, query, parameters=None, database=None):
    return self.execute_query_sync(query, parameters, database)

# AFTER:
async def execute_query(self, query, parameters=None, database=None):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,  # default ThreadPoolExecutor
        self.execute_query_sync, query, parameters, database
    )
```

**Impact:** Event loop free trong lúc chờ Redis. asyncio scheduler có thể chạy khác coroutines (file I/O, progress logging, cache writes).

**Risk:** THẤP. redis.Redis is thread-safe (ConnectionPool). Cùng query, cùng order, chỉ unblock event loop.

**Caveat:** `write_batches()` trong language_writer gọi `await driver.execute_query()` tuần tự. run_in_executor không biến chúng thành parallel — chỉ unblock. Benefit chính đến khi kết hợp pipelining.

### Step 3.2: Redis pipelining method

**File:** `code-tiny/tools/graph/driver/falkordb_driver.py` — thêm method mới

```python
import redis as redis_module

class FalkorDBDriver(Neo4jDriver):
    # ... existing code ...

    def execute_queries_pipelined(
        self,
        queries: List[Tuple[str, Optional[Dict[str, Any]]]],
        database: Optional[str] = None,
    ) -> List[Tuple[List[Dict], List[str], Any]]:
        """Queue multiple GRAPH.QUERY commands in 1 Redis pipeline.

        FalkorDB processes writes sequentially server-side (per-graph FIFO lock).
        Pipelining eliminates client-side network RTT between commands —
        all commands are sent in a single TCP write, responses read sequentially.

        Args:
            queries: List of (cypher_query, params_dict) tuples
            database: Optional graph database name

        Returns:
            List of (records, keys, result) tuples, one per query
        """
        graph = self._graph_for(database)
        redis_client = self._client.connection  # underlying redis.Redis

        pipe = redis_client.pipeline(transaction=False)
        prepared_queries = []

        for query, params in queries:
            q, p = _prepare_falkordb_query(query, params)
            # Build CYPHER param header (falkordb format: "CYPHER k1=v1 k2=v2 ")
            param_header = graph._build_params_header(p)
            full_query = param_header + q
            pipe.execute_command("GRAPH.QUERY", graph.name, full_query, "--compact")
            prepared_queries.append((q, p))

        # Single TCP write, sequential reads
        raw_responses = pipe.execute()

        # Parse each response
        from falkordb.query_result import QueryResult
        results = []
        for response in raw_responses:
            try:
                qr = QueryResult(graph, response)
                keys = [_result_key(item) for item in qr.header]
                records = [
                    {key: _normalize_falkordb_value(row[idx]) for idx, key in enumerate(keys)}
                    for row in qr.result_set
                ]
                results.append((records, keys, qr))
            except Exception as e:
                logger.warning(f"Pipelined query failed: {e}")
                results.append(([], [], None))

        return results

    async def execute_queries_pipelined_async(
        self,
        queries: List[Tuple[str, Optional[Dict[str, Any]]]],
        database: Optional[str] = None,
    ) -> List[Tuple[List[Dict], List[str], Any]]:
        """Async wrapper for execute_queries_pipelined."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self.execute_queries_pipelined, queries, database
        )
```

### Step 3.3: Pipeline independent node writes trong write_all

**File:** `code-tiny/tools/graph/writer/language_writer.py:write_all()`

Hiện tại `write_all()` gọi tuần tự:
```python
await self.write_files(...)        # chờ xong
await self.write_namespaces(...)   # chờ xong
await self.write_types(...)        # chờ xong
...
```

Mỗi `write_*` là UNWIND MERGE on `{id: ...}` — **không phụ thuộc nhau** (không MATCH nodes khác). Có thể pipeline:

```python
async def write_all(self, ..., use_pipelined=False):
    # ... existing code ...

    if use_pipelined and hasattr(self.driver, 'execute_queries_pipelined_async'):
        # Phase A: pipeline all independent node writes (NO edges)
        node_queries = []
        if files:
            node_queries.append((self._build_files_query(), {"rows": files}))
        if namespaces:
            node_queries.append((self._build_namespaces_query(), {"rows": namespaces}))
        if types:
            node_queries.append((self._build_types_query(), {"rows": types}))
        if functions:
            node_queries.append((self._build_functions_query(), {"rows": functions}))
        # ... other node types ...

        # Send all node writes in 1 pipeline (FalkorDB processes sequentially)
        await self.driver.execute_queries_pipelined_async(node_queries, self.database)

        # Phase B: edge writes (DEPEND on nodes existing — AFTER pipeline completes)
        if relations:
            await self.write_relations_typed(relations, state, state_writer)
        if calls_with_site:
            await self.write_calls_with_site(calls_with_site, state, state_writer)

    else:
        # Existing sequential path (fallback)
        ...
```

**Important:** Đây KHÔNG phải parallelization. Pipeline gửi tất cả node queries trong 1 TCP write, nhưng FalkorDB xử lý tuần tự. Benefit = loại bỏ network RTT giữa queries.

**Risk:** THẤP. Node writes dùng MERGE (idempotent). Edge writes chạy SAU khi pipeline hoàn tất → nodes đã tồn tại. Dependency order preserved.

### Step 3.4: Extract Cypher queries thành reusable builders

Hiện tại Cypher query inline trong `write_batch` closure. Để pipeline, cần extract thành methods:

```python
class LanguageCodeWriter:
    def _files_merge_query(self) -> str:
        return """
        UNWIND $rows AS row
        MERGE (f:File {id: row.id})
        SET f.path = row.path, ...
        """

    def _functions_merge_query(self) -> str:
        return """
        UNWIND $rows AS row
        MERGE (f:Function {id: row.id})
        SET f.name = row.name, ...
        """

    # etc.
```

## Validation

### Test 1: Event loop non-blocking

```python
import asyncio

async def test_non_blocking():
    driver = FalkorDBDriver(...)
    # Trong lúc execute_query chạy, asyncio.sleep(0) nên yield được
    task1 = asyncio.create_task(driver.execute_query("MATCH (n) RETURN count(n)"))
    task2 = asyncio.create_task(asyncio.sleep(0.01))
    done, pending = await asyncio.wait({task1, task2}, return_when=asyncio.FIRST_COMPLETED)
    # task2 nên complete gần như ngay lập tức (event loop not blocked)
    assert task2 in done
```

### Test 2: Pipeline correctness

```python
# Pipeline 3 node-write queries, verify all nodes created
queries = [
    (write_files_query, {"rows": files_batch}),
    (write_functions_query, {"rows": functions_batch}),
    (write_types_query, {"rows": types_batch}),
]
await driver.execute_queries_pipelined_async(queries)
# Verify: count nodes == sum of batches
```

### Test 3: Timing comparison

```bash
# Sequential (current):
# 13 queries × (0.1ms RTT + 20ms exec) = 261ms

# Pipelined:
# 1 pipeline write × (0.1ms RTT + 13×20ms exec) = 260ms
# → Network overhead: 13×0.1ms → 1×0.1ms = saves ~1.2ms localhost
# Over network (5ms RTT): saves ~60ms
```

## Deliverables

- [ ] `execute_query()` dùng `run_in_executor`
- [ ] `execute_queries_pipelined()` method trên FalkorDBDriver
- [ ] Extract Cypher query builders cho node types
- [ ] `write_all(use_pipelined=True)` path
- [ ] Event loop non-blocking verified
- [ ] Pipeline correctness verified
