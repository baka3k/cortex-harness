# Phase 02 - FalkorDB Driver Foundation

## Goal

Implement a FalkorDB provider behind the existing `code-tiny` graph abstraction without breaking Neo4j.

## Tasks

1. Add dependency.
   - Add `falkordb` to `code-tiny/requirements.txt`.
   - Keep `neo4j` during dual-provider rollout.

2. Implement `tools/graph/driver/falkordb_driver.py`.
   - Mirror `Neo4jDriver` public behavior.
   - Connect with `FalkorDB(host, port, username, password)` or equivalent supported client config.
   - Select graph using configured `database`/`graph` name.
   - Implement `execute_query`, `execute_query_sync`, `close`, `verify_connection`, `get_node_count`, `get_edge_count`, `batch_write_nodes`, `batch_write_edges`, and high-level query methods.

3. Normalize result shape.
   - Convert FalkorDB `result_set` rows into dictionaries keyed by headers.
   - Normalize returned nodes, relationships, and paths into the shape expected by MCP services.
   - Add tests for scalar results, node results, relationship results, and paths.

4. Update factory/config.
   - Wire `GraphProvider.FALKORDB`.
   - Add `FALKORDB_HOST`, `FALKORDB_PORT`, `FALKORDB_USERNAME`, `FALKORDB_PASSWORD`, and `FALKORDB_GRAPH`.
   - Decide whether `database` maps to FalkorDB graph name.

5. Keep provider fallback explicit.
   - Do not silently fall back from FalkorDB to Neo4j.
   - Make configuration errors actionable.

## Validation

- Unit tests instantiate FalkorDB driver with a mock/stub client.
- Integration smoke test against a local FalkorDB instance runs `RETURN 1 AS test`.
- Existing Neo4j driver tests still pass.

## Risks

- FalkorDB client return types may not map directly to Neo4j node/path objects.
- Async API may require a separate implementation if current callers expect concurrent awaits.

