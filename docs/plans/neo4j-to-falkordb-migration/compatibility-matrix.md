# Neo4j to FalkorDB Compatibility Matrix

status: completed for embedded runtime
updated: 2026-08-06

| Capability | Neo4j behavior | FalkorDB implementation | Verification |
| --- | --- | --- | --- |
| Driver creation | Bolt `GraphDatabase.driver` | `GraphDriverFactory` selects FalkorDB driver | Factory source + analyzer wiring tests |
| Graph selection | Session database | Selected FalkorDB graph name | Embedded graph-isolation and restart tests |
| Query result | Record objects | Header/row result set normalized to dictionaries | Driver source; expanded driver fixture coverage still recommended |
| Scalar query | `RETURN 1 AS test` | Same normalized tuple contract | Embedded doctor round-trip |
| Range index | Named Neo4j DDL | `create_node_range_index(label, *properties)` | Schema unit tests |
| Composite index | Multi-property DDL | Multi-property native range-index call | DDL parser unit test |
| Unique constraint | Synchronous DDL | Prerequisite range index, native create, status polling | Operational/failure unit tests |
| Constraint failure | Neo4j exception | Failed status or timeout raises | Unit tests |
| Full-text creation | Named multi-label index | One native index per label | Unit test |
| Full-text query | `db.index.fulltext.queryNodes(name, query)` | `db.idx.fulltext.queryNodes(label, query)` | Driver normalization and schema tests; external dataset parity not applicable |
| Graph listing | `SHOW DATABASES` | Client `list_graphs()` | Existing driver test |
| Relationship types | `db.relationshipTypes()` | Same procedure with normalized result | Driver normalized-result tests |
| Importing subquery | `CALL (x) { ... }` | Rewritten to `CALL { WITH x ... }` | Driver source |
| `UNWIND` batch writes | Supported | Shared Cypher | Writer and embedded fixture tests |
| `MERGE` updates | Constraint-backed | Constraint polled before sensitive project MERGE | Schema/project unit tests |
| Paths/`shortestPath` | Supported | Shared queries retained | MCP acceptance; production dataset benchmark not applicable |
| Document GraphRAG | Direct Neo4j sessions | `graph_store` compatibility adapter | Project-scoped document fixture tests |
| Qdrant vectors | External Qdrant | Unchanged | Existing contract tests |
| Legacy `neo4j-graphrag` | Direct runtime | Explicit opt-in compatibility module | Isolation test |

## Known Boundaries

- FalkorDB schema operations use native client methods, not rewritten DDL.
- Async constraint status is a hard failure boundary; setup does not silently
  continue after failure or timeout.
- Missing full-text indexes may still use the driver's substring fallback.
- Local embedded path ownership is defined by the local-file-storage plan.
