# Cypher Migration Report

status: completed for identified provider differences
updated: 2026-08-06

| Purpose | Neo4j/original | FalkorDB/converted | Compatibility and performance note |
| --- | --- | --- | --- |
| Import variables into subquery | `CALL (x) { ... }` | `CALL { WITH x ... }` | Normalized centrally before execution; avoids per-service rewrites |
| Full-text query | `CALL db.index.fulltext.queryNodes(indexName, $query)` | `CALL db.idx.fulltext.queryNodes(label, $query)` | Label-specific index; fallback substring search is slower but safe |
| List databases | `SHOW DATABASES` | client `list_graphs()` | Avoids unsupported schema query and round trip parsing |
| Range index | `CREATE INDEX ... FOR (n:L) ON (...)` | native `create_node_range_index` | Native API used for idempotency and composite properties |
| Unique constraint | `CREATE CONSTRAINT ... REQUIRE ... IS UNIQUE` | native unique constraint + status poll | Setup blocks until usable, preventing unsafe concurrent MERGE |
| Multi-label full text | one named `LabelA|LabelB` index | one native index per label | More setup calls; provider query stays label-selective |
| Project hierarchy | Neo4j MERGE transaction | same single Cypher statement through driver | Constraint setup is provider-specific; business query remains shared |
| `datetime()` | Neo4j function | generated UTC parameter | Avoids unsupported function differences and preserves deterministic shape |

## Shared Queries Retained

`UNWIND`, `MERGE`, `ON CREATE SET`, `ON MATCH SET`, `OPTIONAL MATCH`,
`FOREACH`, `coalesce`, `toLower`, list comprehensions, variable-length paths,
and `shortestPath` remain shared because no repository evidence required a
rewrite. Their normalized result behavior is covered by driver/MCP tests;
production-dataset parity is recorded as not applicable because no source
Neo4j dataset was supplied.

## Result Shape

FalkorDB rows are keyed by returned headers and graph values are normalized to
plain dictionaries/lists. MCP and writer call sites continue to consume the
existing `(records, keys, summary)` driver contract.
