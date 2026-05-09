# High-Level Query Methods

## Overview

GraphDriver now provides **database-agnostic high-level query methods** that abstract away query language differences between Neo4j, Kuzu, FalkorDB, and other graph databases.

## Why High-Level Methods?

### Problem with Raw Queries

```python
# ❌ BAD: Database-specific query (Neo4j Cypher only)
cypher = "MATCH (n:Function) WHERE n.id = $id RETURN n"
records, _, _ = await driver.execute_query(cypher, {"id": node_id}, database)

# This breaks when switching to Kuzu/FalkorDB!
```

### Solution with High-Level Methods

```python
# ✅ GOOD: Database-agnostic
node = await driver.find_node_by_id(node_id, database)

# Works with Neo4j, Kuzu, FalkorDB, Neptune!
```

## Available Methods

### Database Metadata

#### `list_databases() -> List[str]`

List all available databases.

```python
databases = await driver.list_databases()
# Returns: ["neo4j", "kotlin_project", "java_project"]
```

#### `list_relationship_types(database) -> List[str]`

List all relationship types in a database.

```python
rel_types = await driver.list_relationship_types(database="kotlin_project")
# Returns: ["CALLS", "POSSIBLE_CALLS", "CONTAINS", "DECLARES", ...]
```

### Node Operations

#### `find_node_by_id(node_id, database) -> Optional[Dict]`

Find a single node by its ID.

```python
node = await driver.find_node_by_id(
    node_id="com.example.MyClass.myMethod",
    database="kotlin_project"
)
# Returns: {"id": "...", "name": "myMethod", "labels": ["Function"], ...}
```

#### `find_nodes_by_ids(node_ids, database) -> List[Dict]`

Find multiple nodes by their IDs.

```python
nodes = await driver.find_nodes_by_ids(
    node_ids=["id1", "id2", "id3"],
    database="kotlin_project"
)
# Returns: [{"id": "id1", ...}, {"id": "id2", ...}, ...]
```

### Search Operations

#### `search_functions(query, limit, database) -> List[Dict]`

Search for functions by name or qualified_name.

```python
functions = await driver.search_functions(
    query="onCreate",
    limit=50,
    database="kotlin_project"
)
# Returns: List of functions containing "onCreate" in name
```

#### `search_by_code(query, limit, database) -> List[Dict]`

Search nodes by code content.

```python
nodes = await driver.search_by_code(
    query="startActivity",
    limit=50,
    database="kotlin_project"
)
# Returns: List of nodes with "startActivity" in code/comment
```

### Path Finding

#### `find_function_paths(start_id, end_id, relationship_types, max_depth, database) -> List[Path]`

Find shortest paths between two functions.

```python
paths = await driver.find_function_paths(
    start_id="com.example.App.main",
    end_id="com.example.Utils.helper",
    relationship_types=["CALLS", "POSSIBLE_CALLS"],
    max_depth=8,
    database="kotlin_project"
)
# Returns: List of Path objects
```

#### `query_function_subgraph(function_id, relationship_types, direction, max_depth, database) -> List[Path]`

Query subgraph around a function.

```python
subgraph = await driver.query_function_subgraph(
    function_id="com.example.Service.process",
    relationship_types=["CALLS", "CONTAINS"],
    direction="both",  # "incoming", "outgoing", or "both"
    max_depth=2,
    database="kotlin_project"
)
# Returns: List of paths forming the subgraph
```

#### `find_paths_between_modules(source_modules, target_modules, relationship_types, max_depth, limit, database) -> List[Path]`

Find paths between modules (file paths).

```python
paths = await driver.find_paths_between_modules(
    source_modules=["com/example/ui"],
    target_modules=["com/example/database"],
    relationship_types=["CALLS"],
    max_depth=8,
    limit=10,
    database="kotlin_project"
)
# Returns: List of paths between UI and Database modules
```

### Specialized Queries

#### `list_possible_calls(limit, project_id, database) -> Tuple[List[Dict], List[Dict]]`

List POSSIBLE_CALLS relationships (virtual dispatch).

```python
nodes, edges = await driver.list_possible_calls(
    limit=200,
    project_id="kotlin_project",
    database="kotlin_project"
)
# Returns: (list of nodes, list of edges)
```

#### `list_symbols_by_file_path(file_paths, database) -> List[Dict]`

List symbols (functions) in files matching path tokens.

```python
symbols = await driver.list_symbols_by_file_path(
    file_paths=["MainActivity.kt", "Fragment"],
    database="kotlin_project"
)
# Returns: List of functions in matching files
```

#### `list_functions_by_class(class_names, database) -> List[Dict]`

List functions in classes matching names.

```python
functions = await driver.list_functions_by_class(
    class_names=["MainActivity", "BaseActivity"],
    database="kotlin_project"
)
# Returns: List of functions in matching classes
```

#### `list_functions_by_file(file_path, database) -> List[Dict]`

List functions in a specific file.

```python
functions = await driver.list_functions_by_file(
    file_path="MainActivity.kt",
    database="kotlin_project"
)
# Returns: List of functions in MainActivity.kt
```

#### `list_entrypoints(modules, relationship_types, database) -> List[Dict]`

List entrypoint functions called from outside specified modules.

```python
entrypoints = await driver.list_entrypoints(
    modules=["com/example/internal"],
    relationship_types=["CALLS", "STARTS_COMPONENT"],
    database="kotlin_project"
)
# Returns: List of internal functions called from external code
```

## Migration Guide

### Before (Database-Specific)

```python
# Old code with raw Cypher
async def get_symbol(node_id: str, db: str):
    cypher = "MATCH (n) WHERE n.id = $id RETURN n LIMIT 1"
    records, _, _ = await driver.execute_query(cypher, {"id": node_id}, db)
    if records:
        return records[0].get("n")
    return None
```

### After (Database-Agnostic)

```python
# New code with high-level method
async def get_symbol(node_id: str, db: str):
    return await driver.find_node_by_id(node_id, db)
```

## Implementation for Other Databases

When implementing a new database driver (e.g., KuzuDriver), you only need to implement these high-level methods with the appropriate query syntax:

```python
class KuzuDriver(GraphDriver):
    async def find_node_by_id(self, node_id: str, database: Optional[str] = None):
        # Kuzu-specific query syntax
        kuzu_query = f"MATCH (n) WHERE n.id = '{node_id}' RETURN n LIMIT 1"
        records = await self.execute_query(kuzu_query, database=database)
        # ... parse results
```

## Benefits

1. **Database Agnostic**: Switch between Neo4j, Kuzu, FalkorDB without changing MCP code
2. **Cleaner Code**: High-level intent instead of low-level query syntax
3. **Type Safety**: Methods have clear signatures and return types
4. **Easier Testing**: Mock high-level methods instead of query strings
5. **Better Errors**: Database-specific errors abstracted away
6. **Future Proof**: New databases can be added without changing application code

## Best Practices

1. **Always use high-level methods** in MCP servers and application code
2. **Only use `execute_query`** for truly custom queries that don't fit existing methods
3. **Add new high-level methods** if you find yourself writing the same query pattern repeatedly
4. **Document database-specific quirks** in driver implementation, not in application code

## Example: Updating MCP Server

```python
# Before
@mcp_server.tool(name="get_symbol")
async def tool_get_symbol(node_id: str, db: str):
    cypher = "MATCH (n) WHERE n.id = $id RETURN n LIMIT 1"
    records, _, _ = await driver.execute_query(cypher, {"id": node_id}, db)
    if records:
        return {"node": records[0]["n"]}
    raise RuntimeError(f"Node {node_id} not found")

# After
@mcp_server.tool(name="get_symbol")
async def tool_get_symbol(node_id: str, db: str):
    node = await driver.find_node_by_id(node_id, db)
    if node:
        return {"node": node}
    raise RuntimeError(f"Node {node_id} not found")
```

## Future Extensions

Planned methods:

- `annotate_node(node_id, note, tags, severity, database)`
- `trace_flow(start_id, end_id, rel_types, max_depth, database)`
- `get_ipc_messages(sender, receiver, database)`
- Community detection methods (like Graphiti)
- Graph analytics methods

## See Also

- [MIGRATION_GUIDE.py](MIGRATION_GUIDE.py) - How to migrate analyzers
- [README.md](README.md) - Overall architecture
- [STRUCTURE.md](../STRUCTURE.md) - Directory organization
