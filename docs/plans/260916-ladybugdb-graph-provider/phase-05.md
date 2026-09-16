# Phase 5: Doc-tiny Graph Store Integration

## Mục tiêu
Thêm `LadybugDBGraphStore` cho doc-tiny, tương tự `FalkorDBGraphStore`.

## Files cần sửa

### 1. `doc-tiny/graph_store.py`

#### `LadybugDBGraphStore` class
```python
class LadybugDBGraphStore:
    provider = "ladybug"
    
    def __init__(self, driver, database=None, *, owns_driver=True):
        self._driver = driver
        self._database = database or getattr(driver, "_database", "neo4j")
        self._owns_driver = owns_driver
    
    def session(self):
        return LadybugDBSession(self._driver, self._database)
    
    def for_graph(self, database):
        return LadybugDBGraphStore(self._driver, database, owns_driver=False)
    
    def close(self):
        if self._owns_driver:
            self._driver.close()
    
    def setup_indexes(self):
        # LadybugDB auto-indexes PKs — no manual index creation needed
        pass
```

#### `LadybugDBSession` class
```python
class LadybugDBSession:
    def __init__(self, driver, database=None):
        self._driver = driver
        self._database = database
    
    def __enter__(self):
        return self
    
    def __exit__(self, *args):
        pass
    
    def run(self, query, parameters=None, **kwargs):
        params = dict(parameters or {})
        params.update(kwargs)
        records, _, _ = self._driver.execute_query_sync(query, params, self._database)
        return LadybugDBResult(records)
```

#### `LadybugDBResult` class
```python
class LadybugDBResult(list):
    def single(self):
        return self[0] if self else None
```

#### Factory functions update
```python
def create_graph_store_from_args(args):
    provider = normalize_provider(getattr(args, "graph_provider", None))
    if provider == "falkordb":
        # existing...
    elif provider == "ladybug":
        from tools.graph.driver.ladybug_driver import LadybugDBDriver
        driver = LadybugDBDriver(
            path=args.ladybug_path,
            database=getattr(args, "ladybug_graph", None),
        )
        store = LadybugDBGraphStore(driver, database=getattr(args, "ladybug_graph", None))
        store.setup_indexes()
        return store
    # neo4j branch...

def create_graph_store_from_env():
    provider = env_graph_provider()
    if provider == "ladybug":
        from tools.graph.driver.ladybug_driver import LadybugDBDriver
        path = os.getenv("LADYBUG_PATH")
        if not path:
            from cortex_harness.storage import resolve_storage
            path = str(resolve_storage(Path.cwd()).ladybug_path_for_role("doc"))
        driver = LadybugDBDriver(path=path, database=os.getenv("LADYBUG_GRAPH"))
        store = LadybugDBGraphStore(driver)
        store.setup_indexes()
        return store
    # existing falkordb/neo4j branches...

def create_graph_store_for_project(project_id):
    provider = env_graph_provider()
    if provider == "ladybug":
        # Same pattern as falkordb but with ladybug paths
        ...
```

### 2. `doc-tiny/mcp_graph_rag.py`
- Import `LadybugDBGraphStore` khi provider = ladybug
- `create_graph_store_from_env()` đã handle provider dispatch

## Tests
- `LadybugDBGraphStore.session()` → returns working session
- `LadybugDBGraphStore.setup_indexes()` → no-op
- `create_graph_store_from_args()` with ladybug provider
- Doc ingest + query round-trip

## Acceptance
- Doc-tiny scripts accept `--graph-provider ladybug --ladybug-path ...`
- `LadybugDBGraphStore` wraps `LadybugDBDriver` correctly
- Doc graph ingest + query works end-to-end
