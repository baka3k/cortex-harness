# Phase 04 — MCP Integration

## Objective

Update MCP servers (`graph_mcp`, `mind_mcp`) and ingest/query scripts to use
`StorageFactory` instead of constructing backend clients directly. MCP tool
signatures and behavior remain unchanged — only the internal backend resolution
is affected.

## Dependencies

- Phase 01 (BackendMode config).
- Phase 02 (RemoteQdrantStore).
- Phase 03 (StorageFactory).

## Changes

### 4.1 graph_mcp (code-tiny)

**File:** `code-tiny/mcp/unified_mcp.py`

Current pattern (simplified):
```python
# Direct FalkorDBDriver construction
driver = FalkorDBDriver(path=falkordb_path, graph=graph_name)

# Or direct LocalQdrantStore
store = LocalQdrantStore(resolved, QdrantStorageRole.CODE)
```

New pattern:
```python
from cortex_harness.storage import create_storage

# Resolve per-project backend
targets = ProjectRegistry.resolve_project_targets(project_id)
factory = create_storage(targets)

# Same API, different backend
driver = factory.get_falkordb_driver(targets.code_graph)
store = factory.get_qdrant_store(QdrantStorageRole.CODE)
```

**Key integration points in unified_mcp.py:**

1. **Graph queries** (`query_subgraph`, `find_paths`, `semantic_search`, etc.):
   - Currently create `FalkorDBDriver` from env/path.
   - Change: resolve via factory using `project_id` from tool params.

2. **Vector queries** (`explore_graph`, `semantic_search` vector path):
   - Currently use `LocalQdrantStore` directly.
   - Change: resolve via factory.

3. **Project listing** (`list_databases`, `get_project_modules`):
   - Currently assume local backend.
   - Change: each project's backend resolved independently.

### 4.2 mind_mcp (doc-tiny)

**File:** `doc-tiny/mcp_graph_rag.py`

Current pattern:
```python
from cortex_harness.storage import get_client
client = get_client(resolved, QdrantStorageRole.DOCUMENT)
```

New pattern:
```python
from cortex_harness.storage import create_storage
targets = ProjectRegistry.resolve_project_targets(project_id)
factory = create_storage(targets)
store = factory.get_qdrant_store(QdrantStorageRole.DOCUMENT)
```

**Key integration points:**

1. **Qdrant queries** in `query_graph_rag_langextract`:
   - Use factory for Qdrant store.
   - Graph queries use factory for FalkorDB driver.

2. **Qdrant ingestion** in `graphrag_ingest_langextract`:
   - Use factory for Qdrant store.
   - Graph writes use factory for FalkorDB driver.

### 4.3 Wrapper Functions (CRITICAL — Red Team C2)

**File:** `code-tiny/tools/common/local_qdrant.py`

`get_code_qdrant_store()` hiện raise `RemoteQdrantUnsupportedError` khi locator
là HTTP URL. Refactor để dùng factory:

```python
def get_code_qdrant_store(project_id: str = "") -> QdrantStore:
    """Return the code Qdrant store for the given project."""
    if project_id:
        targets = ProjectRegistry.resolve_project_targets(project_id)
        factory = create_storage(targets)
        return factory.get_qdrant_store(QdrantStorageRole.CODE)
    # Fallback: local-only for scripts without project context
    resolved = resolve_storage(Path.cwd())
    return LocalQdrantStore(resolved, QdrantStorageRole.CODE)
```

**File:** `doc-tiny/doc_local_qdrant.py`

`get_document_qdrant_store()` — same refactoring pattern.

### 4.4 Ingest Scripts

**Files affected (definitive list from grep):**
- `doc-tiny/0_reset_all.py`
- `doc-tiny/graphrag_ingest_langextract.py`
- `doc-tiny/graphrag_query_langextract.py`
- `doc-tiny/graph_store.py` (**added by Red Team H3**)
- `doc-tiny/doc_local_qdrant.py`
- `code-tiny/tools/common/primary_vector_sync.py`
- `code-tiny/tools/common/local_qdrant.py`
- Analyzer scripts under `code-tiny/tools/*/`

**Pattern change:**

```python
# Before: always local
from cortex_harness.storage import LocalQdrantStore, resolve_storage
resolved = resolve_storage()
store = LocalQdrantStore(resolved, role)

# After: project-aware backend
from cortex_harness.storage import create_storage
targets = ProjectRegistry.resolve_project_targets(project_id)
factory = create_storage(targets)
store = factory.get_qdrant_store(role)
```

### 4.5 MCP Backend Modules (CRITICAL — Red Team C3)

**Files affected (actual MCP integration points):**
- `code-tiny/mcp/fastmcp_server.py` — imports `get_code_qdrant_store`
- `code-tiny/mcp/cplus/cplus_mcp.py` — imports `get_code_qdrant_store`
- `code-tiny/mcp/java/java_mcp.py` — imports `get_code_qdrant_store`
- `code-tiny/mcp/android/android_mcp.py` — imports `get_code_qdrant_store`
- `code-tiny/mcp/services/impact_service.py` — constructs driver
- `code-tiny/mcp/services/explore_service.py` — constructs driver
- `code-tiny/tools/graph/core/factory.py` — `GraphDriverFactory.create_driver_from_env()` (**Red Team M3**)

Strategy: Refactor `get_code_qdrant_store()` (§4.3) to use factory → all MCP
backend modules automatically get remote support without individual changes.

For `GraphDriverFactory.create_driver_from_env()`: add `project_id` parameter
and consult factory when available, fall back to env-based local for backward
compatibility.

### 4.6 doc-tiny Process-Global Singletons (Red Team M2)

**File:** `doc-tiny/mcp_graph_rag.py`

Replace module-level `_qdrant_client = None` / `_neo4j_driver = None` singletons
with per-project cache:

```python
_qdrant_stores: dict[str, QdrantStore] = {}
_graph_drivers: dict[str, FalkorDBDriver] = {}

def get_qdrant_for_project(project_id: str) -> QdrantStore:
    if project_id not in _qdrant_stores:
        targets = ProjectRegistry.resolve_project_targets(project_id)
        factory = create_storage(targets)
        _qdrant_stores[project_id] = factory.get_qdrant_store(
            QdrantStorageRole.DOCUMENT
        )
    return _qdrant_stores[project_id]
```

### 4.7 Backward Compatibility

Scripts that don't have a `project_id` context (e.g., `make storage-init`,
`make doctor`) continue to use local backend directly — no factory needed.

Factory chỉ được introduce ở những nơi đã có `project_id` flow.

### 4.5 Error Handling in MCP Tools

MCP tools cần handle `BackendConnectionError` gracefully:

```python
@mcp.tool()
async def semantic_search(query: str, project_id: str = "", ...):
    try:
        factory = create_storage(targets)
        store = factory.get_qdrant_store(role)
        results = store.search(collection, vector, limit=top_k)
    except BackendConnectionError as e:
        return {"error": str(e), "error_code": "BACKEND_UNAVAILABLE"}
```

## Migration Strategy

**Không cần migration tool.** Khi user muốn chuyển project từ local → remote:

1. Update `.cortext-harness/config/project.json`:
   ```json
   {"storage_backend": "remote", "remote": {"qdrant_url": "...", "falkordb_uri": "..."}}
   ```
2. Ensure remote server is running.
3. Re-run ingest: `dev ingest --project project_id`.
4. Data is rebuilt from source code/documents.

Reverse (remote → local):
1. Remove `storage_backend` and `remote` from config (defaults to local).
2. Re-run ingest.

## Acceptance Criteria

- [ ] `unified_mcp.py` graph query tools use factory for backend resolution.
- [ ] `unified_mcp.py` vector query tools use factory for backend resolution.
- [ ] `mcp_graph_rag.py` uses factory for both Qdrant and FalkorDB.
- [ ] Ingest scripts (`graphrag_ingest_langextract.py`, `0_reset_all.py`) use factory.
- [ ] MCP tools return structured error when remote backend is unreachable.
- [ ] Scripts without `project_id` context still work with local backend.
- [ ] No changes to MCP tool signatures or return shapes.
- [ ] `make doctor` validates connectivity for projects configured as remote.

### 4.8 Emergency Rollback (Red Team Q6)

Env var `CORTEX_STORAGE_BACKEND_FORCE_LOCAL=1` force-disables remote mode
for all projects. Factory checks this at construction time:

```python
class StorageFactory:
    def __init__(self, ...):
        if os.getenv("CORTEX_STORAGE_BACKEND_FORCE_LOCAL"):
            self._mode = BackendMode.LOCAL
            self._remote = None
```

## Files Modified

| File | Change |
|------|--------|
| `code-tiny/tools/common/local_qdrant.py` | **Refactor `get_code_qdrant_store()` to use factory** |
| `doc-tiny/doc_local_qdrant.py` | **Refactor `get_document_qdrant_store()` to use factory** |
| `code-tiny/mcp/unified_mcp.py` | Use factory for backend resolution |
| `code-tiny/mcp/fastmcp_server.py` | Verify uses wrapper function |
| `code-tiny/mcp/cplus/cplus_mcp.py` | Verify uses wrapper function |
| `code-tiny/mcp/java/java_mcp.py` | Verify uses wrapper function |
| `code-tiny/mcp/android/android_mcp.py` | Verify uses wrapper function |
| `code-tiny/mcp/services/impact_service.py` | Use factory |
| `code-tiny/mcp/services/explore_service.py` | Use factory |
| `code-tiny/tools/graph/core/factory.py` | Add `project_id` to `create_driver_from_env()` |
| `doc-tiny/mcp_graph_rag.py` | Per-project cache, use factory |
| `doc-tiny/graph_store.py` | Use factory |
| `doc-tiny/graphrag_ingest_langextract.py` | Use factory |
| `doc-tiny/graphrag_query_langextract.py` | Use factory |
| `doc-tiny/0_reset_all.py` | Use factory |
| `cortex_harness/storage/__init__.py` | Export `BackendConnectionError` |
