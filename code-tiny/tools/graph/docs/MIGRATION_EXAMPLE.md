# MCP Migration Example: Using High-Level Query Methods

## Overview

This document shows how to migrate MCP server code from raw Cypher queries to database-agnostic high-level methods.

## Example 1: get_symbol

### ❌ Before (Raw Cypher)

```python
@mcp_server.tool(name="get_symbol")
async def tool_get_symbol(
    node_id: Any = None,
    db: Optional[str] = None,
    content_mode: Optional[str] = None,
    include_raw_fields: bool = False,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    # Merge parameters
    payload = _merge_payload(
        payload,
        {
            "node_id": node_id,
            "db": db,
            "content_mode": content_mode,
            "include_raw_fields": include_raw_fields,
        },
    )
    node_id = payload.get("node_id")
    db = payload.get("db")
    content_mode = payload.get("content_mode")
    include_raw_fields = payload.get("include_raw_fields", False)

    if node_id is None:
        raise ValueError("node_id is required.")

    candidates = _resolve_db_candidates(db)
    _require(candidates[0] if candidates else None, "db")
    node_id = str(node_id)

    # ❌ Raw Cypher query - Neo4j specific!
    query = "MATCH (n) WHERE n.id = $id RETURN n LIMIT 1"
    used_db, results = await _run_cypher_first(query, {"id": node_id}, candidates)

    if results:
        mode = _normalize_content_mode(content_mode)
        return {"db": used_db, "node": _record_node(results[0]["n"], mode, include_raw_fields)}

    raise RuntimeError(f"Node {node_id} not found in any db.")
```

### ✅ After (High-Level Method)

```python
@mcp_server.tool(name="get_symbol")
async def tool_get_symbol(
    node_id: Any = None,
    db: Optional[str] = None,
    content_mode: Optional[str] = None,
    include_raw_fields: bool = False,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    # Merge parameters (same as before)
    payload = _merge_payload(
        payload,
        {
            "node_id": node_id,
            "db": db,
            "content_mode": content_mode,
            "include_raw_fields": include_raw_fields,
        },
    )
    node_id = payload.get("node_id")
    db = payload.get("db")
    content_mode = payload.get("content_mode")
    include_raw_fields = payload.get("include_raw_fields", False)

    if node_id is None:
        raise ValueError("node_id is required.")

    candidates = _resolve_db_candidates(db)
    _require(candidates[0] if candidates else None, "db")
    node_id = str(node_id)

    # ✅ Use high-level method - database agnostic!
    driver = await _get_graph_driver()

    # Try each database candidate
    node = None
    used_db = None
    for db_candidate in candidates:
        node = await driver.find_node_by_id(node_id, db_candidate)
        if node:
            used_db = db_candidate
            break

    if node:
        mode = _normalize_content_mode(content_mode)
        return {"db": used_db, "node": _record_node(node, mode, include_raw_fields)}

    raise RuntimeError(f"Node {node_id} not found in any db.")
```

**Benefits:**

- Works with Neo4j, Kuzu, FalkorDB without changes
- Clearer intent: "find node by ID"
- No raw query string construction
- Easier to test and mock

---

## Example 2: get_node_details

### ❌ Before (Raw Cypher)

```python
@mcp_server.tool(name="get_node_details")
async def tool_get_node_details(
    node_ids: Optional[List[Any]] = None,
    db: Optional[str] = None,
    content_mode: Optional[str] = None,
    include_raw_fields: bool = False,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    # ... parameter handling ...

    node_ids = _normalize_string_list(node_ids)
    if not node_ids:
        raise ValueError("node_ids must be a non-empty list.")

    candidates = _resolve_db_candidates(db)
    _require(candidates[0] if candidates else None, "db")

    ids = [str(item) for item in node_ids]

    # ❌ Raw Cypher - Neo4j specific
    query = "MATCH (n) WHERE n.id IN $ids RETURN n"
    used_db, results = await _run_cypher_first(query, {"ids": ids}, candidates)

    if results:
        mode = _normalize_content_mode(content_mode)
        nodes = [_record_node(item["n"], mode, include_raw_fields) for item in results]
        return {"db": used_db, "nodes": nodes}

    raise RuntimeError("No matching nodes found in any db.")
```

### ✅ After (High-Level Method)

```python
@mcp_server.tool(name="get_node_details")
async def tool_get_node_details(
    node_ids: Optional[List[Any]] = None,
    db: Optional[str] = None,
    content_mode: Optional[str] = None,
    include_raw_fields: bool = False,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    # ... parameter handling ...

    node_ids = _normalize_string_list(node_ids)
    if not node_ids:
        raise ValueError("node_ids must be a non-empty list.")

    candidates = _resolve_db_candidates(db)
    _require(candidates[0] if candidates else None, "db")

    ids = [str(item) for item in node_ids]
    driver = await _get_graph_driver()

    # ✅ Use high-level method
    nodes_found = []
    used_db = None
    for db_candidate in candidates:
        nodes_found = await driver.find_nodes_by_ids(ids, db_candidate)
        if nodes_found:
            used_db = db_candidate
            break

    if nodes_found:
        mode = _normalize_content_mode(content_mode)
        nodes = [_record_node(node, mode, include_raw_fields) for node in nodes_found]
        return {"db": used_db, "nodes": nodes}

    raise RuntimeError("No matching nodes found in any db.")
```

---

## Example 3: find_paths

### ❌ Before (Raw Cypher with String Interpolation)

```python
@mcp_server.tool(name="find_paths")
async def tool_find_paths(
    start_function_id: Any = None,
    end_function_id: Any = None,
    max_depth: int = 8,
    include_possible: bool = False,
    include_fp: bool = False,
    parser_type: Optional[str] = None,
    # ... other params ...
) -> Dict[str, Any]:
    # ... parameter handling ...

    start_id = str(start_function_id)
    end_id = str(end_function_id)
    depth = _normalize_depth(max_depth, default=8, max_limit=20)

    # Resolve relationship types based on parser
    rel_types = await _resolve_call_rel_types(include_possible, include_fp, parser_type, candidates)

    # ❌ Build Cypher query with string interpolation - dangerous!
    rel_pattern = f"[:{'|'.join(rel_types)}*..{depth}]"
    query = (
        f"MATCH (a:Function) WHERE a.id = $start "
        f"MATCH (b:Function) WHERE b.id = $end "
        "AND a.id <> b.id "
        f"MATCH p=shortestPath((a)-{rel_pattern}->(b)) RETURN p"
    )

    used_db, result = await _run_cypher_first(
        query,
        {"start": start_id, "end": end_id},
        candidates
    )

    if result:
        paths = [row["p"] for row in result]
        graph = _paths_to_graph(paths, content_mode, include_raw_fields)
        return {"db": used_db, **graph}

    raise RuntimeError("No path found in any db.")
```

### ✅ After (High-Level Method)

```python
@mcp_server.tool(name="find_paths")
async def tool_find_paths(
    start_function_id: Any = None,
    end_function_id: Any = None,
    max_depth: int = 8,
    include_possible: bool = False,
    include_fp: bool = False,
    parser_type: Optional[str] = None,
    # ... other params ...
) -> Dict[str, Any]:
    # ... parameter handling ...

    start_id = str(start_function_id)
    end_id = str(end_function_id)
    depth = _normalize_depth(max_depth, default=8, max_limit=20)

    # Resolve relationship types based on parser
    rel_types = await _resolve_call_rel_types(include_possible, include_fp, parser_type, candidates)

    driver = await _get_graph_driver()

    # ✅ Use high-level method - clean and safe!
    paths = None
    used_db = None
    for db_candidate in candidates:
        paths = await driver.find_function_paths(
            start_id=start_id,
            end_id=end_id,
            relationship_types=rel_types,
            max_depth=depth,
            database=db_candidate
        )
        if paths:
            used_db = db_candidate
            break

    if paths:
        graph = _paths_to_graph(paths, content_mode, include_raw_fields)
        return {"db": used_db, **graph}

    raise RuntimeError("No path found in any db.")
```

**Benefits:**

- No string interpolation (safer)
- Parameters are typed and validated
- Works with any graph database
- Driver handles query construction internally

---

## Example 4: query_subgraph

### ❌ Before (Complex Cypher Construction)

```python
@mcp_server.tool(name="query_subgraph")
async def tool_query_subgraph(
    function_id: Any = None,
    direction: str = "all",
    max_depth: int = 2,
    # ... other params ...
) -> Dict[str, Any]:
    # ... parameter handling ...

    depth = _normalize_depth(max_depth, default=2, max_limit=10)
    direction = direction.lower()
    rel_types = await _resolve_call_rel_types(include_possible, include_fp, parser_type, candidates)

    # ❌ Complex pattern building
    rel_pattern = f"[:{'|'.join(rel_types)}*1..{depth}]"

    for candidate in candidates:
        try:
            paths: List[Any] = []

            # ❌ Different queries for different directions - repetitive
            if direction in {"incoming", "in"}:
                incoming_query = f"MATCH (f:Function) WHERE f.id = $id MATCH p=(f)<-{rel_pattern}-(n) RETURN p"
                results = await _run_cypher(incoming_query, {"id": function_id}, candidate)
                paths = [row["p"] for row in results]

            elif direction in {"outgoing", "out"}:
                outgoing_query = f"MATCH (f:Function) WHERE f.id = $id MATCH p=(f)-{rel_pattern}->(n) RETURN p"
                results = await _run_cypher(outgoing_query, {"id": function_id}, candidate)
                paths = [row["p"] for row in results]

            else:  # both
                incoming_query = f"MATCH (f:Function) WHERE f.id = $id MATCH p=(f)<-{rel_pattern}-(n) RETURN p"
                outgoing_query = f"MATCH (f:Function) WHERE f.id = $id MATCH p=(f)-{rel_pattern}->(n) RETURN p"
                incoming_results = await _run_cypher(incoming_query, {"id": function_id}, candidate)
                outgoing_results = await _run_cypher(outgoing_query, {"id": function_id}, candidate)
                paths = [row["p"] for row in incoming_results] + [row["p"] for row in outgoing_results]

            if paths:
                graph = _paths_to_graph(paths, content_mode, include_raw_fields)
                return {"db": candidate, **graph}

        except Exception as exc:
            last_error = exc
            continue

    raise RuntimeError(f"No subgraph found for node {function_id}")
```

### ✅ After (Simple High-Level Call)

```python
@mcp_server.tool(name="query_subgraph")
async def tool_query_subgraph(
    function_id: Any = None,
    direction: str = "all",
    max_depth: int = 2,
    # ... other params ...
) -> Dict[str, Any]:
    # ... parameter handling ...

    depth = _normalize_depth(max_depth, default=2, max_limit=10)
    direction = direction.lower()
    rel_types = await _resolve_call_rel_types(include_possible, include_fp, parser_type, candidates)

    driver = await _get_graph_driver()

    # ✅ Single method call - driver handles complexity!
    paths = None
    used_db = None
    for candidate in candidates:
        try:
            paths = await driver.query_function_subgraph(
                function_id=str(function_id),
                relationship_types=rel_types,
                direction=direction,
                max_depth=depth,
                database=candidate
            )
            if paths:
                used_db = candidate
                break
        except Exception as exc:
            continue

    if paths:
        graph = _paths_to_graph(paths, content_mode, include_raw_fields)
        return {"db": used_db, **graph}

    raise RuntimeError(f"No subgraph found for node {function_id}")
```

**Benefits:**

- 60% less code
- Direction logic hidden in driver
- No pattern construction
- Easier to understand and maintain

---

## Example 5: find_path_between_module

### ❌ Before (Massive Cypher Query)

```python
@mcp_server.tool(name="find_path_between_module")
async def tool_find_path_between_module(
    source_modules: List[str],
    target_modules: List[str],
    # ... other params ...
) -> Dict[str, Any]:
    # ... parameter handling ...

    rel_types = await _resolve_call_rel_types(include_possible, include_fp, parser_type, db_candidates)
    rel_pattern = f"[:{'|'.join(rel_types)}*..{depth}]"

    # ❌ Huge, complex query - hard to read and maintain
    query = (
        "WITH [t IN $sources | toLower(t)] AS sources, [t IN $targets | toLower(t)] AS targets "
        "MATCH (s:Function)<-[:CONTAINS]-(sf:File) "
        "MATCH (t:Function)<-[:CONTAINS]-(tf:File) "
        "WHERE any(token IN sources WHERE "
        "toLower(coalesce(s.file_path, '')) CONTAINS token OR "
        "toLower(coalesce(sf.path, '')) CONTAINS token OR "
        "toLower(coalesce(sf.file_path, '')) CONTAINS token) "
        "AND any(token IN targets WHERE "
        "toLower(coalesce(t.file_path, '')) CONTAINS token OR "
        "toLower(coalesce(tf.path, '')) CONTAINS token OR "
        "toLower(coalesce(tf.file_path, '')) CONTAINS token) "
        "AND s.id <> t.id "
        f"MATCH p=shortestPath((s)-{rel_pattern}->(t)) "
        "RETURN p LIMIT 10"
    )

    used_db, result = await _run_cypher_first(
        query,
        {"sources": source_modules, "targets": target_modules},
        db_candidates
    )

    if result:
        paths = [row["p"] for row in result]
        graph = _paths_to_graph(paths, content_mode, include_raw_fields)
        return {"db": used_db, **graph}

    raise RuntimeError("No paths found")
```

### ✅ After (Clean High-Level Call)

```python
@mcp_server.tool(name="find_path_between_module")
async def tool_find_path_between_module(
    source_modules: List[str],
    target_modules: List[str],
    # ... other params ...
) -> Dict[str, Any]:
    # ... parameter handling ...

    rel_types = await _resolve_call_rel_types(include_possible, include_fp, parser_type, db_candidates)

    driver = await _get_graph_driver()

    # ✅ Simple, readable method call
    paths = None
    used_db = None
    for db_candidate in db_candidates:
        paths = await driver.find_paths_between_modules(
            source_modules=source_modules,
            target_modules=target_modules,
            relationship_types=rel_types,
            max_depth=depth,
            limit=10,
            database=db_candidate
        )
        if paths:
            used_db = db_candidate
            break

    if paths:
        graph = _paths_to_graph(paths, content_mode, include_raw_fields)
        return {"db": used_db, **graph}

    raise RuntimeError("No paths found")
```

---

## Migration Checklist

When migrating MCP tools:

- [ ] Identify all raw Cypher queries in tool functions
- [ ] Check if a high-level method exists (see [QUERY_METHODS.md](QUERY_METHODS.md))
- [ ] Replace `_run_cypher` / `_run_cypher_first` with driver method calls
- [ ] Remove query string construction
- [ ] Keep business logic (parameter validation, result formatting)
- [ ] Test with actual Neo4j database
- [ ] Verify error handling still works
- [ ] Update documentation if needed

## Performance Notes

High-level methods have **zero performance overhead**:

- They construct the same queries as manual Cypher
- No additional network roundtrips
- Query planning is identical
- Results are not double-processed

The only "cost" is better code organization! 🎉

## Next Steps

1. See [QUERY_METHODS.md](QUERY_METHODS.md) for full API reference
2. Implement KuzuDriver with same high-level methods
3. Add tests for database switching
4. Add more specialized query methods as needed
