# Phase 3: Configuration & CLI Wiring

## Mục tiêu
Wire LadybugDB qua tất cả configuration layers để user có thể chọn qua env var hoặc CLI arg.

## Files cần sửa

### 1. `cortex_harness/dev.py`

#### `_graph_provider()` (line ~466)
```python
def _graph_provider(env: dict, scoped_key: str) -> str:
    # ... existing code ...
    if provider in {"falkor", "falkordb"}:
        return "falkordb"
    if provider == "neo4j":
        return "neo4j"
    if provider in {"ladybug", "ladybugdb", "ladybug-db"}:  # NEW
        return "ladybug"
    raise ValueError(
        f"Unsupported graph provider for {source}: {provider!r}; expected "
        "'falkordb' (alias 'falkor'), 'neo4j', or 'ladybug'"
    )
```

#### `_isolate_graph_provider_environment()` (line ~481)
```python
def _isolate_graph_provider_environment(env: dict, scoped_key: str) -> str:
    provider = _graph_provider(env, scoped_key)
    env["GRAPH_PROVIDER"] = provider
    env[scoped_key] = provider
    for key in tuple(env):
        if provider == "falkordb" and key.startswith("NEO4J_"):
            env.pop(key, None)
        elif provider == "neo4j" and (
            key.startswith("FALKORDB_") or key == "DOC_FALKORDB_GRAPH"
        ):
            env.pop(key, None)
        elif provider == "ladybug":  # NEW
            if (key.startswith("FALKORDB_") or key.startswith("NEO4J_") 
                or key == "DOC_FALKORDB_GRAPH"):
                env.pop(key, None)
    return provider
```

#### `_env_to_neo4j_args()` / `_neo4j_args_code()`
Thêm ladybug branch:
```python
if provider == "ladybug":
    if env.get("LADYBUG_PATH"):
        args += ["--ladybug-path", str(env["LADYBUG_PATH"])]
    args += ["--ladybug-graph", env.get("LADYBUG_GRAPH", "hyper_graph")]
```

### 2. `scripts/mcp_runtime_config.py`

#### `normalize_graph_provider()` (line ~52)
```python
_FALKORDB_PROVIDER_VALUES = frozenset({"falkor", "falkordb"})
_LADYBUG_PROVIDER_VALUES = frozenset({"ladybug", "ladybugdb", "ladybug-db"})  # NEW

def normalize_graph_provider(env, scoped_provider):
    # ... existing code ...
    if value in _FALKORDB_PROVIDER_VALUES:
        return "falkordb"
    if value == "neo4j":
        return "neo4j"
    if value in _LADYBUG_PROVIDER_VALUES:  # NEW
        return "ladybug"
    raise ValueError(...)
```

#### `LOCAL_STORAGE_KEYS` (line ~32)
```python
LOCAL_STORAGE_KEYS = frozenset({
    # existing...
    "LADYBUG_PATH", "LADYBUG_CODE_PATH", "LADYBUG_DOC_PATH",  # NEW
})
```

#### `isolate_graph_provider_environment()`
Thêm ladybug branch (tương tự dev.py).

### 3. `code-tiny/tools/graph/cli.py`

#### `add_graph_provider_args()`
```python
parser.add_argument(
    "--graph-provider",
    choices=["neo4j", "falkordb", "ladybug"],  # thêm "ladybug"
    ...
)
# Thêm --ladybug-path
if not _has_option(parser, "--ladybug-path"):
    parser.add_argument(
        "--ladybug-path",
        default=os.getenv("LADYBUG_PATH"),
        help="LadybugDB .lbdb file path (embedded mode).",
    )
if not _has_option(parser, "--ladybug-graph"):
    parser.add_argument(
        "--ladybug-graph",
        default=os.getenv("LADYBUG_GRAPH", "hyper_graph"),
        help="LadybugDB logical graph name.",
    )
```

### 4. `doc-tiny/graph_store.py`

#### `normalize_provider()`
```python
def normalize_provider(value):
    provider = (value or "neo4j").strip().lower()
    if provider in {"falkor", "falkordb"}:
        return "falkordb"
    if provider == "neo4j":
        return provider
    if provider in {"ladybug", "ladybugdb", "ladybug-db"}:  # NEW
        return "ladybug"
    raise ValueError(f"Unsupported graph provider: {value}")
```

#### `add_graph_store_args()`
```python
parser.add_argument(
    "--graph-provider",
    choices=["neo4j", "falkordb", "ladybug"],
    ...
)
parser.add_argument("--ladybug-path", default=os.getenv("LADYBUG_PATH"))
parser.add_argument("--ladybug-graph", default=os.getenv("LADYBUG_GRAPH", "neo4j"))
```

#### `create_graph_store_from_args()` / `create_graph_store_from_env()` / `create_graph_store_for_project()`
Thêm ladybug branches → tạo `LadybugDBGraphStore` instances.

## Tests
- `GRAPH_PROVIDER=ladybug` → dev.py resolves correctly
- `--graph-provider ladybug --ladybug-path /tmp/test.lbdb` → CLI parses
- `normalize_provider("ladybug")` → `"ladybug"`
- `isolate_graph_provider_environment` strips FalkorDB + Neo4J keys

## Acceptance
- User set `GRAPH_PROVIDER=ladybug` → all MCP servers use LadybugDB
- CLI `--graph-provider ladybug` works cho code-tiny và doc-tiny scripts
- Env isolation: không leak FALKORDB_*/NEO4J_* vars vào LadybugDB process
