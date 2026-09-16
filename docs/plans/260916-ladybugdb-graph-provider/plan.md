# Plan: Thêm LadybugDB làm Graph Provider

> **Status:** implemented (2026-09-16) — Phase 0 spike GO (see `phase-00-spike-report.md`), Phases 1-10 landed, review findings fixed, full-suite regression clean
> **Created:** 2026-09-16
> **Mode:** --full (research + scope challenge + red team + validate)

## Objective

Thêm **LadybugDB** (`ladybug`) làm graph provider thứ ba bên cạnh FalkorDB và Neo4j,
đảm bảo:

1. **Ingest** dữ liệu vào LadybugDB hoạt động bình thường (code-tiny graph writer + doc-tiny graphrag ingest).
2. **Query** dữ liệu ra đúng contract, đặc biệt **giữ nguyên rule project_id**:
   - Không truyền `project_id` → query TẤT CẢ (R1).
   - Truyền `project_id` → scope theo project, case-insensitive, LIKE/prefix (R2-R4).
   - Local = Remote parity (R5).
   - WRITE PATH giữ exact match (R6).
3. LadybugDB hoạt động ở **embedded mode** (tương tự FalkorDBLite) — không cần server.

## Context

### LadybugDB là gì
- **Embedded columnar graph database**, successor của KuzuDB (archived).
- "DuckDB for graphs" — nhúng trực tiếp vào process, không cần server.
- Python API: `import ladybug as lb` → `lb.Database("path.lbdb")` → `lb.Connection(db)` → `conn.execute(cypher)`.
- Hỗ trợ Cypher nhưng có **khác biệt quan trọng** so với FalkorDB/Neo4j.

### Cypher differences ảnh hưởng
| FalkorDB/Neo4j | LadybugDB | Impact |
|---|---|---|
| Schema-less | **Cần CREATE NODE TABLE / CREATE REL TABLE trước** | Cần schema DDL phase |
| `CALL { WITH var ... }` | **CALL subquery KHÔNG hỗ trợ** (chỉ EXISTS/COUNT) | Cần rewrite hoặc tránh |
| `REMOVE n.prop` | **Không hỗ trợ** → `SET n.prop = NULL` | Cần normalize |
| `SET n += map` | **Không hỗ trợ** → phải SET từng property | Cần rewrite batch write |
| `FOREACH` | **Không hỗ trợ** → dùng UNWIND | Cần rewrite |
| `labels(n)` | `label(n)` | Cần function mapping |
| Manual index | **Chỉ auto PK index** | Bỏ setup_indexes() |
| `STARTS WITH` | **Hỗ trợ** ✅ | Project ID rule giữ nguyên |
| `$param` syntax | **Hỗ trợ** ✅ | Parameterized queries OK |

### Architecture hiện tại (provider system)

```
GraphProvider enum (base.py)
  ↓
GraphDriverFactory.create_driver() (factory.py)
  ↓
provider_contract.py: normalize_graph_provider_name(), isolate_graph_provider_environment()
  ↓
CypherGraphDriver (cypher_driver.py): portable Cypher ops
  ↓
Concrete drivers: falkordb_driver.py, neo4j_driver.py
```

**Configuration layers cần touch:**
- `cortex_harness/dev.py`: `_graph_provider()`, `_isolate_graph_provider_environment()`
- `scripts/mcp_runtime_config.py`: `normalize_graph_provider()`, `LOCAL_STORAGE_KEYS`
- `cortex_harness/storage/config.py`: path resolution (thêm `ladybug_*` paths)
- `code-tiny/tools/graph/cli.py`: `add_graph_provider_args()`
- `doc-tiny/graph_store.py`: `normalize_provider()`, `add_graph_store_args()`, factory functions

## Scope

### In scope
1. `GraphProvider.LADYBUG` enum value
2. `LadybugDBDriver` implementation (embedded mode)
3. Provider registration ở tất cả configuration layers
4. Storage path resolution cho LadybugDB (`.lbdb` files)
5. Project ID query rules giữ nguyên (R1-R6)
6. Doc-tiny `LadybugDBGraphStore` adapter
7. CLI args (`--graph-provider ladybug`, `--ladybug-path`, etc.)
8. Tests

### Out of scope
- Remote/server mode cho LadybugDB (chỉ embedded)
- Migration tool từ FalkorDB → LadybugDB
- Rust cortex-dev integration (Python-only, Rust side chưa có graph provider code)
- MCP server config auto-switch (user chọn qua env var)

### Additional Touchpoints (from explorer)

9. **`code-tiny/tools/graph/core/shared_runtime.py`**: Update `_driver_key()` for LadybugDB cache keys
10. **`scripts/mcp-lifecycle.py`** + **`.ps1`**: Handle LadybugDB environment isolation in MCP server launches
11. **MCP backends** (4 files): `cplus_mcp.py`, `android_mcp.py`, `java_mcp.py`, `fastmcp_server.py` — add LadybugDB driver creation alongside FalkorDB/Neo4j
12. **`code-tiny/mcp/falkordb_discovery.py`**: Add `ladybug_discovery.py` for cross-instance `.lbdb` fan-out
13. **Service layer**: `impact_service.py`, `explore_service.py` — handle LadybugDB-specific code paths
14. **Documentation**: Update `DATABASE_INTEGRATION.md`, `PROJECT_REGISTRY.md`, `UNIFIED_INGEST_QUERY_CONTRACT.md`, `PROJECT_ID_QUERY_RULES.md`

## Red Team Findings (2026-09-16)

### Verdict: STOP — 3 Critical Blockers

| # | Blocker | Severity | Effort |
|---|---------|----------|--------|
| B1 | **Package identity** — `GraphProvider.KUZU` đã tồn tại trong `base.py:15`. LadybugDB là Kuzu fork. Cần quyết định: reuse `KUZU` enum hay thêm `LADYBUG` mới? Verify pip package + API. | Critical | 1-2 days |
| B2 | **Schema type system** — Writer layer dùng `SET n += row` (24 occurrences) với dynamic properties. LadybugDB cần typed columns TRƯỚC khi insert. Cần property inventory + type mapping + Cypher rewrite infra. | Critical | 2-3 weeks |
| B3 | **CALL subquery rewrite** — `write_calls_with_site()` và `write_possible_calls_with_site()` (highest-volume ingest ops) dùng `CALL { WITH row ... }`. LadybugDB KHÔNG hỗ trợ. Cần verified rewrite strategy. | Critical | 1-2 weeks |

### Additional High-Severity Findings

| # | Finding | Severity | Notes |
|---|---------|----------|-------|
| F1 | `SET n += map` (24 occurrences) — không thể rewrite thành individual SET khi properties dynamic/heterogeneous | High | Coupled to B2 |
| F2 | `FOREACH` conditional MERGE pattern (`message_scan.py:536`) — không có LadybugDB equivalent trực tiếp | High | Cần driver-level abstraction |
| F3 | Graph name mapping: FalkorDB multi-graph vs LadybugDB single-file — `for_graph()` pattern breaks | High | Cần per-project .lbdb directory + cache |
| F4 | `labels(n)` → `label(n)`: multi-label vs single-label semantics khác biệt cơ bản | Medium | Cần audit toàn bộ usage |
| F5 | Thread safety: `threading.Lock` không đủ — cần `StorageLease` (cross-process) + `BoundedLane` (backpressure) | Medium | Infra exists, cần adapt |
| F6 | Performance mismatch: columnar DB cho OLTP workload (point queries) — cần benchmark | Medium | 2-3 days |

### Resolution: Add Phase 0 Spike

Trước khi implement bất kỳ phase nào, cần **1-week spike** (Phase 0) để resolve 3 blockers:

1. Install `ladybug` package, verify Python API, quyết định enum naming
2. Scrape property inventory từ ~15 writer files → complete `Dict[label, Dict[prop, type]]`
3. Rewrite 5 complex Cypher queries (CALL subquery, FOREACH, SET +=) → verify trên real LadybugDB
4. Benchmark: point-query + ingest performance vs FalkorDB

## Phases

### Phase 0: Spike — Verify & Resolve Blockers (NEW)
**Duration:** 1 week
**Goal:** Resolve 3 critical blockers trước khi commit to implementation

#### 0a. Package Identity (B1)
```bash
pip install ladybug==0.20.4
python3 -c "import ladybug as lb; db = lb.Database('/tmp/test.lbdb'); conn = lb.Connection(db); print(conn.execute('RETURN 1'))"
```
- Quyết định: `GraphProvider.KUZU` (reuse) vs `GraphProvider.LADYBUG` (new)
- Recommendation: **New `LADYBUG` enum** — LadybugDB là fork với breaking changes, reuse KUZU gây nhầm lẫn
- Update `factory.py:99-101` từ `NotImplementedError` → LadybugDB driver

#### 0b. Property Inventory (B2)
Scan toàn bộ writer files để build complete property map:
- `tools/graph/writer/language_writer.py` (primary)
- `tools/graph/writer/project_topology_writer.py`
- `tools/graph/writer/aspnet_writer.py`, `spring_writer.py`, `mybatis_writer.py`, etc.
- `tools/graph/writer/query_contract.py` (relationship properties)
- `tools/graph/operations/*.py` (direct mutation paths)

Output: `ladybug_schema.py` — `Dict[label, Dict[prop, ladybug_type]]`

#### 0c. Cypher Rewrite Verification (B3)
Rewrite và verify trên real LadybugDB:
1. `write_calls_with_site()` — CALL { WITH row } → flat MATCH + MERGE
2. `write_possible_calls_with_site()` — same pattern
3. `find_node_by_id()` — UNION ALL across 30+ labels → LadybugDB equivalent
4. `FOREACH (_ IN CASE WHEN ... )` conditional MERGE → split query
5. `SET n += row` → explicit SET per known column + JSON spill

#### 0d. Benchmark (F6)
Compare FalkorDB vs LadybugDB on:
- `find_node_by_id()` latency (point query)
- `write_calls_with_site()` throughput (batch write)
- `query_function_subgraph()` latency (traversal)
- Full ingest time (~10K nodes, ~50K edges)

#### 0e. Spike Report
Write `phase-00-spike-report.md` với:
- Verified API surface
- Complete property inventory
- Rewritten queries (pass/fail)
- Benchmark results
- Go/No-Go recommendation

### Phase 1: Core Provider Registration
**Files:** `base.py`, `provider_contract.py`, `factory.py`

1. Thêm `GraphProvider.LADYBUG = "ladybug"` vào `GraphProvider` enum
2. Thêm aliases: `"ladybug"`, `"ladybugdb"`, `"ladybug-db"` → `GraphProvider.LADYBUG`
3. Cập nhật `normalize_graph_provider_name()` accept `"ladybug"`
4. Cập nhật `isolate_graph_provider_environment()` strip FALKORDB_/NEO4J_ keys khi provider = ladybug
5. Cập nhật `GraphDriverFactory.create_driver()` dispatch to `LadybugDBDriver`
6. Cập nhật `GraphDriverFactory.create_from_env()` cho LadybugDB

### Phase 2: LadybugDB Driver Implementation
**Files:** `code-tiny/tools/graph/driver/ladybug_driver.py` (NEW)

1. Implement `LadybugDBDriver(CypherGraphDriver)`:
   - Constructor: `path` (required), `database` (graph name), `instance_id`, `owner_id`
   - `lb.Database(path)` + `lb.Connection(db)` — embedded mode
   - `execute_query()` / `execute_query_sync()` → `conn.execute(cypher, params)`
   - Result mapping: LadybugDB response → `Tuple[List[Dict], List[str], Any]` (same contract as FalkorDB)
2. **Cypher normalization** cho LadybugDB:
   - Rewrite `CALL { WITH var ... }` → Ladybug-compatible (hoặc reject với clear error)
   - Rewrite `REMOVE n.prop` → `SET n.prop = NULL`
   - Rewrite `SET n += $map` → individual `SET n.key = $map.key`
   - Rewrite `labels(n)` → `label(n)`
3. **Schema management**:
   - `ensure_schema()`: translate `CODE_GRAPH_SCHEMA` manifest → `CREATE NODE TABLE` / `CREATE REL TABLE` DDL
   - `create_indexes()`: no-op (LadybugDB auto-indexes PKs)
   - `inspect_indexes()`: return PK metadata
4. **Graph selection**: LadybugDB dùng `CREATE NODE TABLE ... IF NOT EXISTS` — graph name mapping qua database path hoặc label prefix
5. Thread safety: LadybugDB Connection không thread-safe → wrap với lock (tương tự FalkorDB driver)
6. Lease/admission: kế thừa từ FalkorDBDriver pattern

### Phase 3: Configuration & CLI Wiring
**Files:** `dev.py`, `mcp_runtime_config.py`, `cli.py`, `graph_store.py`

1. **`cortex_harness/dev.py`**:
   - `_graph_provider()`: accept `"ladybug"` → return `"ladybug"`
   - `_isolate_graph_provider_environment()`: strip FALKORDB_/NEO4J_ keys khi ladybug
   - `_env_to_neo4j_args()` / `_neo4j_args_code()`: thêm ladybug branch → `--graph-provider ladybug --ladybug-path ...`
2. **`scripts/mcp_runtime_config.py`**:
   - `normalize_graph_provider()`: accept `"ladybug"`
   - `LOCAL_STORAGE_KEYS`: thêm `LADYBUG_PATH`, `LADYBUG_CODE_PATH`, `LADYBUG_DOC_PATH`
   - `isolate_graph_provider_environment()`: ladybug branch
3. **`code-tiny/tools/graph/cli.py`**:
   - `add_graph_provider_args()`: `choices=["neo4j", "falkordb", "ladybug"]`
   - Thêm `--ladybug-path` arg
4. **`doc-tiny/graph_store.py`**:
   - `normalize_provider()`: accept `"ladybug"`
   - `add_graph_store_args()`: thêm ladybug choices + `--ladybug-path`
   - `create_graph_store_from_args()`: ladybug branch
   - `create_graph_store_from_env()`: ladybug branch
   - `create_graph_store_for_project()`: ladybug branch

### Phase 4: Storage Path Resolution
**Files:** `cortex_harness/storage/config.py`, `layout.py`, `generation.py`

1. Thêm `LADYBUG_PATH`, `LADYBUG_CODE_PATH`, `LADYBUG_DOC_PATH` env vars
2. Thêm `DEFAULT_LADYBUG_PATH` = `v1/instances/default/ladybug/code/data.lbdb`
3. Thêm `ladybug_path`, `ladybug_code_path`, `ladybug_doc_path` fields vào `ResolvedStorage`
4. Thêm `ladybug_path_for_role()` method
5. Cập nhật `layout.py` để include ladybug paths trong storage layout output
6. Cập nhật `generation.py` cho ladybug generation manifest

### Phase 5: Doc-tiny Graph Store Integration
**Files:** `doc-tiny/graph_store.py`, `doc-tiny/mcp_graph_rag.py`

1. `LadybugDBGraphStore` class (tương tự `FalkorDBGraphStore`):
   - Wrap `LadybugDBDriver`
   - `session()` → `LadybugDBSession`
   - `setup_indexes()` → no-op hoặc PK-only
   - `for_graph()` → lightweight view
2. Cập nhật `mcp_graph_rag.py` để dùng `env_graph_provider()` và tạo đúng store

### Phase 6: Project ID Query Rules
**Files:** `project_scope.py`, driver, query templates

1. **Giữ nguyên R1-R6** — LadybugDB hỗ trợ `STARTS WITH` và `$param` syntax
2. `prepare_project_scope_parameters()` đã provider-neutral → reuse
3. Cypher predicate: `WHERE ($project_id IS NULL OR n.project_id_normalized STARTS WITH $project_id_normalized)` → **hoạt động trên LadybugDB**
4. LadybugDB driver phải implement `prepare_project_scope_parameters()` hook (kế thừa từ `CypherGraphDriver`)
5. Test: verify project_id=None → all results, project_id="bank" → prefix match

### Phase 7: Ingest Pipeline
**Files:** `code-tiny/tools/graph/writer/*`, `doc-tiny/graphrag_ingest_langextract.py`

1. Code-tiny graph writer dùng `CypherGraphDriver.batch_write_nodes/edges` → LadybugDB driver phải support:
   - `UNWIND $nodes AS node CREATE (n:Label) SET n = node` → **LadybugDB KHÔNG hỗ trợ `SET n = node` (map assignment)**
   - → Cần rewrite thành individual SET hoặc dùng LadybugDB-native COPY
   - Alternative: dùng `UNWIND` + `CREATE` với explicit property mapping
2. Doc-tiny ingest: `graphrag_ingest_langextract.py` → dùng `FalkorDBGraphStore` → cần `LadybugDBGraphStore` equivalent
3. Schema creation trước ingest: LadybugDB cần node/rel tables tồn tại trước khi CREATE/MERGE

### Phase 8: MCP Backend & Service Layer Integration
**Files:** `shared_runtime.py`, `mcp-lifecycle.py`, `cplus_mcp.py`, `android_mcp.py`, `java_mcp.py`, `fastmcp_server.py`, `impact_service.py`, `explore_service.py`

1. **`shared_runtime.py`**: Update `_driver_key()` to include LadybugDB in cache key computation
2. **`mcp-lifecycle.py`** + **`.ps1`**: Handle `GRAPH_PROVIDER=ladybug` in server launch, env isolation
3. **MCP backends** (4 files): Thêm LadybugDB branch trong driver creation:
   ```python
   if provider == "ladybug":
       driver = get_shared_graph_driver(GraphProvider.LADYBUG, ladybug_config)
   ```
4. **`impact_service.py`**: FalkorDB-specific code paths → add LadybugDB equivalent
5. **`falkordb_discovery.py`**: Add `ladybug_discovery.py` for cross-instance `.lbdb` fan-out queries

### Phase 9: Documentation
**Files:** `DATABASE_INTEGRATION.md`, `PROJECT_REGISTRY.md`, `UNIFIED_INGEST_QUERY_CONTRACT.md`, `PROJECT_ID_QUERY_RULES.md`

1. Update `PROJECT_ID_QUERY_RULES.md`: Mention LadybugDB alongside FalkorDB/Neo4j
2. Update `UNIFIED_INGEST_QUERY_CONTRACT.md`: Add LadybugDB provider option
3. Update `PROJECT_REGISTRY.md`: Document `GRAPH_PROVIDER=ladybug` option
4. Update `DATABASE_INTEGRATION.md`: LadybugDB integration guide

### Phase 10: Tests
**Files:** `code-tiny/tests/`, `doc-tiny/tests/`, `tests/`

1. Unit tests cho `LadybugDBDriver`:
   - Connection lifecycle (open, close, verify)
   - Query execution (read, write, parameterized)
   - Cypher normalization (CALL rewrite, REMOVE→SET NULL, SET += → individual)
   - Schema creation (CREATE NODE TABLE, CREATE REL TABLE)
2. Integration tests:
   - Ingest code graph → LadybugDB → verify nodes/edges
   - Ingest doc graph → LadybugDB → verify entities/relations
   - Query with project_id → verify scope rules (R1-R4)
   - Query without project_id → verify unscoped (R1)
3. Provider registration tests:
   - `normalize_graph_provider("ladybug")` → `GraphProvider.LADYBUG`
   - `env_graph_provider()` with `GRAPH_PROVIDER=ladybug`
   - CLI args parsing
4. Storage path tests:
   - Default path resolution
   - Env var override
   - Role-based path (code vs doc)

## Dependencies

- **Python package:** `ladybug` (pip install ladybug) — cần thêm vào `requirements.txt`
- **No Docker/server** — embedded mode only
- **Platform:** LadybugDB hỗ trợ macOS, Linux, Windows (tương tự FalkorDBLite)

## Risks (updated from Red Team 2026-09-16)

| Risk | Severity | Mitigation |
|------|----------|------------|
| **B2: Schema-first — 100+ labels, dynamic properties, 24× `SET +=`** | **Critical** | Phase 0 spike: property inventory + hybrid PK + JSON spill. 2-3 weeks. |
| **B3: CALL subquery in core ingest paths** | **Critical** | Phase 0 spike: rewrite + verify 5 queries. 1-2 weeks. |
| **B1: Package identity (KUZU vs LADYBUG enum)** | **Critical** | Phase 0 spike: install + verify API. 1-2 days. |
| F1: `SET n += map` (24 occurrences) with heterogeneous props | High | Coupled to B2 — schema compiler produces property_whitelist |
| F2: `FOREACH` conditional MERGE — no direct equivalent | High | Driver-level `conditional_merge()` abstraction |
| F3: Graph name mapping — `for_graph()` pattern breaks | High | Per-project .lbdb directory + lazy open + cache |
| F4: `labels(n)` multi-label vs `label(n)` single-label | Medium | Audit all usage; likely safe (writers use single-label) |
| F5: Thread safety — need StorageLease + BoundedLane | Medium | Reuse existing infra, adapt for LadybugDB |
| F6: Performance mismatch — columnar for OLTP | Medium | Phase 0 benchmark; add cache if >2x slower |
| LadybugDB maturity (v0.20.4, Nov 2025) | Medium | Feature flag, FalkorDB remains default |
| Variable-length path semantics (walk vs trail) | Low | `is_trail()` filter cho critical queries |
| Error classification khác FalkorDB | Low | Thêm LadybugDB markers vào `_DATABASE_NOT_FOUND_MARKERS` |
| `datetime()` function not supported | Low | Same rewrite strategy as FalkorDB driver |

## Critical Design Decision: Schema-first Strategy

**Problem:** LadybugDB yêu cầu `CREATE NODE TABLE` với typed columns TRƯỚC khi insert.
CODE_GRAPH_SCHEMA có 100+ node labels, mỗi node có dynamic properties (thêm mới thường xuyên).
FalkorDB/Neo4j schema-less → insert thoải mái.

**Option A: Full schema scan** — Scan toàn bộ writer code để discover tất cả properties per label.
- Pro: Clean, query được từng property
- Con: Fragile — mỗi khi thêm property mới phải update DDL. 100+ labels × ~10-20 props = rất nhiều columns.

**Option B: PK + JSON spill** — `CREATE NODE TABLE Label(id STRING PRIMARY KEY, _properties STRING)`
- Pro: Simple, không cần biết trước properties, flexible
- Con: Không query trực tiếp được inside JSON (phải extract)

**Option C: Hybrid** — Known indexed columns typed + `_properties JSON STRING` cho rest.
- Pro: Indexed columns queryable (project_id, symbol_id, v.v.), rest flexible
- Con: Dual-write (column + JSON), slightly complex
- **SELECTED** ✅

**Implementation:**
```cypher
-- LadybugDB DDL for a code graph node:
CREATE NODE TABLE IF NOT EXISTS Function(
    id STRING PRIMARY KEY,
    name STRING,
    project_id STRING,
    project_id_normalized STRING,
    _properties STRING  -- JSON spill cho dynamic props
)
```

**Query pattern:**
```cypher
-- Indexed lookup (fast):
MATCH (n:Function) WHERE n.project_id_normalized STARTS WITH $pid RETURN n

-- Dynamic property access (via JSON extract):
MATCH (n:Function) WHERE n.id = $id RETURN n._properties  -- parse JSON client-side
```

## Decisions

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | Embedded-only, no remote mode | LadybugDB chưa có server mode; FalkorDB đã cover remote use case |
| D2 | FalkorDB remains default | Stability; LadybugDB là option, không phải replacement |
| D3 | Separate driver file, không modify FalkorDB driver | Clean separation; LadybugDB có đủ khác biệt |
| D4 | Reuse `CypherGraphDriver` base | Portable ops (batch_write, verify_connection) giảm duplicate |
| D5 | Schema DDL từ manifest auto-generate | LadybugDB cần schema trước khi write; manual DDL không scale |
| D6 | `.lbdb` extension cho data files | LadybugDB native format |

## Success Criteria

1. `GRAPH_PROVIDER=ladybug` → MCP servers start với LadybugDB backend
2. `dev sync code` ingests code graph vào LadybugDB `.lbdb` file
3. `dev sync doc` ingests doc graph vào LadybugDB `.lbdb` file
4. `semantic_search(project_id="bank")` → đúng prefix match trên LadybugDB (R1-R4)
5. `semantic_search()` (no project_id) → trả tất cả results (R1)
6. `pytest` passes cho tất cả new + existing tests (no FalkorDB/Neo4j regression)
7. `dev doctor` reports LadybugDB storage status
8. Cross-instance fan-out queries work với `.lbdb` files
9. Documentation updated (PROJECT_ID_QUERY_RULES, UNIFIED_INGEST_QUERY_CONTRACT, etc.)
