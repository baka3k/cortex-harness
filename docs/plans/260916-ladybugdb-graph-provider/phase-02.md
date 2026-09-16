# Phase 2: LadybugDB Driver Implementation

## Mục tiêu
Implement `LadybugDBDriver` — embedded graph driver tuân thủ `CypherGraphDriver` contract.

## File mới: `code-tiny/tools/graph/driver/ladybug_driver.py`

### Class structure
```python
class LadybugDBDriver(CypherGraphDriver):
    """LadybugDB embedded graph driver.
    
    LadybugDB is an embedded columnar graph database (successor to KuzuDB).
    It uses file-based storage (.lbdb) and supports Cypher with some differences
    from FalkorDB/Neo4j.
    """
    
    def __init__(
        self,
        *,
        path: str | Path,
        database: Optional[str] = None,
        graph: Optional[str] = None,
        instance_id: str = "default",
        owner_id: str = "code",
    ):
        import ladybug as lb
        
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._database = database or graph or "hyper_graph"
        self._instance_id = instance_id
        self._owner_id = owner_id
        
        # LadybugDB: one Database = one file, one Connection per thread
        self._db = lb.Database(str(self._path))
        self._conn = lb.Connection(self._db)
        self._lock = threading.Lock()
        
        # Schema tracking: ensure DDL runs once per graph
        self._schema_ensured = False
```

### Key methods

#### execute_query / execute_query_sync
```python
async def execute_query(self, query, parameters=None, database=None):
    query = _normalize_ladybug_query(query)
    params = prepare_project_scope_parameters(query, parameters)
    with self._lock:
        result = self._conn.execute(query, params or {})
        header = [col.get_name() for col in result.get_column_names()]  # API TBD
        records = [dict(zip(header, row)) for row in result.rows_as_dict()]
    return records, header, None

def execute_query_sync(self, query, parameters=None, database=None):
    # Same but synchronous
```

#### Cypher normalization
```python
def _normalize_ladybug_query(query: str) -> str:
    """Rewrite Cypher constructs LadybugDB doesn't support."""
    # 1. CALL { WITH var ... } → not supported, raise clear error
    if re.search(r'CALL\s*\{', query, re.IGNORECASE):
        raise UnsupportedCypherError(
            "LadybugDB does not support CALL subqueries. "
            "Rewrite the query to avoid CALL { }."
        )
    # 2. REMOVE n.prop → SET n.prop = NULL
    query = re.sub(
        r'\bREMOVE\s+(\w+\.\w+)',
        r'SET \1 = NULL',
        query, flags=re.IGNORECASE
    )
    # 3. labels(n) → label(n)
    query = re.sub(r'\blabels\s*\(', 'label(', query, flags=re.IGNORECASE)
    return query
```

#### Schema management
```python
async def ensure_schema(self, manifest=None, database=None, *, timeout_seconds=60.0):
    """Create NODE TABLE and REL TABLE from schema manifest."""
    from tools.graph.schema import CODE_GRAPH_SCHEMA
    schema = manifest or CODE_GRAPH_SCHEMA
    
    with self._lock:
        for node_label, properties in schema.node_tables.items():
            cols = ", ".join(
                f"{col.name} {col.ladybug_type} PRIMARY KEY" 
                if col.is_pk else f"{col.name} {col.ladybug_type}"
                for col in properties
            )
            self._conn.execute(
                f"CREATE NODE TABLE IF NOT EXISTS {node_label}({cols})"
            )
        for rel_type, (from_label, to_label, props) in schema.rel_tables.items():
            prop_cols = ", ".join(f"{p.name} {p.ladybug_type}" for p in props)
            extra = f", {prop_cols}" if prop_cols else ""
            self._conn.execute(
                f"CREATE REL TABLE IF NOT EXISTS {rel_type}(FROM {from_label} TO {to_label}{extra})"
            )
    self._schema_ensured = True
```

#### batch_write_nodes (override — LadybugDB doesn't support `SET n = map`)
```python
async def batch_write_nodes(self, nodes, label, database=None):
    if not nodes:
        return 0
    # LadybugDB doesn't support SET n = $map
    # Use UNWIND + CREATE with explicit properties
    # Alternative: COPY FROM with temp CSV
    keys = list(nodes[0].keys())
    set_clause = ", ".join(f"n.{k} = node.{k}" for k in keys)
    query = f"""
    UNWIND $nodes AS node
    CREATE (n:{label})
    SET {set_clause}
    RETURN count(n) as count
    """
    records, _, _ = await self.execute_query(query, {"nodes": nodes}, database)
    return records[0]["count"] if records else 0
```

### Thread safety
- LadybugDB Connection is NOT thread-safe
- Wrap all `conn.execute()` calls with `threading.Lock`
- Same pattern as FalkorDBDriver's lease/admission

### Lease/Admission
- Kế thừa pattern từ FalkorDBDriver
- `StorageLease` cho write serialization
- `BoundedLane` cho admission control

## Tests
- Test connection lifecycle (open, close, verify)
- Test Cypher normalization (REMOVE → SET NULL, labels → label)
- Test CALL subquery rejection
- Test schema creation (CREATE NODE TABLE, CREATE REL TABLE)
- Test batch_write_nodes with explicit property SET
- Test parameterized queries ($param syntax)
- Test thread safety (concurrent reads/writes)

## Acceptance
- `LadybugDBDriver` implements all `GraphDriver` abstract methods
- Embedded mode: open `.lbdb` file, execute Cypher, close
- Schema auto-creation from manifest
- Project ID parameters work correctly
