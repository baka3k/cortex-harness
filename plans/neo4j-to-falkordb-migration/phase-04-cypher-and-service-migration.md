# Phase 04 - Cypher And Service Migration

## Goal

Migrate query behavior while preserving existing service APIs.

## Tasks

1. Convert Neo4j-only procedure calls.
   - `SHOW DATABASES` becomes FalkorDB graph listing/client list.
   - `CALL db.relationshipTypes()` is compatible in FalkorDB, but verify output shape.
   - `CALL db.index.fulltext.queryNodes(...)` becomes FalkorDB full-text query procedures.

2. Test and convert query syntax.
   - `CALL () { ... UNION ALL ... }`
   - `shortestPath(...)`
   - variable-length relationship patterns
   - `UNWIND` batch writes
   - `MERGE ... ON CREATE SET ... ON MATCH SET`
   - `FOREACH`
   - `OPTIONAL MATCH`
   - `any(...)`, `coalesce(...)`, `toLower(...)`, list comprehensions

3. Move direct Neo4j bridge code.
   - Refactor `code-tiny/mcp/unified_mcp.py` direct `_neo4j.GraphDatabase.driver(...)` usage to the graph driver abstraction or a provider-aware bridge.
   - Keep raw Cypher only where it is provider-specific and isolated.

4. Preserve query outputs.
   - For every migrated query, document original query, converted query, behavior notes, and performance considerations.
   - Add regression fixtures for representative output shapes.

## Validation

- MCP graph query tools produce equivalent JSON payloads under Neo4j and FalkorDB for a seed graph.
- Path and subgraph queries return parseable path structures.
- Full-text fallback paths still work when full-text indexes are missing.

## Risks

- FalkorDB documented relationship pattern behavior can affect queries that count nodes while not referencing relationship aliases.
- `LIMIT` does not constrain eager write operations in FalkorDB, so import queries must not rely on `LIMIT` to reduce writes.

