# Phase 1: Core Provider Registration

## Mục tiêu
Đăng ký LadybugDB vào hệ thống provider enum + normalization + factory.

## Files cần sửa

### 1. `code-tiny/tools/graph/core/base.py`
```python
class GraphProvider(Enum):
    NEO4J = "neo4j"
    KUZU = "kuzu"
    FALKORDB = "falkordb"
    NEPTUNE = "neptune"
    LADYBUG = "ladybug"  # NEW
```

### 2. `code-tiny/tools/graph/core/provider_contract.py`
```python
_PROVIDER_ALIASES = {
    # existing...
    "ladybug": GraphProvider.LADYBUG,
    "ladybugdb": GraphProvider.LADYBUG,
    "ladybug-db": GraphProvider.LADYBUG,
}
```

Cập nhật `normalize_graph_provider_name()`:
```python
if provider not in {GraphProvider.FALKORDB, GraphProvider.NEO4J, GraphProvider.LADYBUG}:
    raise ValueError(f"Unsupported graph provider: {provider.value}")
```

Cập nhật `isolate_graph_provider_environment()`:
```python
# Thêm branch cho ladybug: strip cả FALKORDB_ và NEO4J_ keys
elif provider == "ladybug":
    for key in tuple(environment):
        if key.startswith("FALKORDB_") or key.startswith("NEO4J_") or key == "DOC_FALKORDB_GRAPH":
            environment.pop(key, None)
```

### 3. `code-tiny/tools/graph/core/factory.py`
```python
elif provider == GraphProvider.LADYBUG:
    from tools.graph.driver.ladybug_driver import LadybugDBDriver
    return LadybugDBDriver(
        path=config.get("path"),
        database=config.get("database") or config.get("graph"),
        graph=config.get("graph"),
        instance_id=config.get("instance_id"),
        owner_id=config.get("owner_id"),
    )
```

`create_from_env()`:
```python
elif provider == GraphProvider.LADYBUG:
    prefix = env_prefix if env_prefix != "NEO4J" else "LADYBUG"
    path = os.getenv("LADYBUG_PATH")
    if not path:
        from cortex_harness.storage import resolve_storage
        path = str(resolve_storage(Path.cwd()).ladybug_path_for_role(
            StorageRole.CODE if "CODE" in env_prefix else StorageRole.DOCUMENT
        ))
    config = {
        "database": os.getenv(f"{prefix}_GRAPH") or os.getenv(f"{prefix}_DATABASE", "hyper_graph"),
        "graph": os.getenv(f"{prefix}_GRAPH"),
        "path": path,
        "instance_id": os.getenv("CORTEX_STORAGE_INSTANCE", "default"),
        "owner_id": os.getenv("CORTEX_STORAGE_OWNER", "code"),
    }
    return await GraphDriverFactory.create_driver(provider, config)
```

## Tests
- `test_normalize_graph_provider_ladybug`: "ladybug", "ladybugdb", "LADYBUG" → LADYBUG
- `test_factory_create_ladybug`: factory dispatches correctly
- `test_isolate_env_ladybug`: strips FALKORDB_/NEO4J_ keys

## Acceptance
- `GraphProvider.LADYBUG` tồn tại
- `normalize_graph_provider_name("ladybug")` → `"ladybug"`
- `GraphDriverFactory.create_driver(GraphProvider.LADYBUG, config)` → `LadybugDBDriver` instance
