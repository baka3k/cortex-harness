# Neo4j to FalkorDB Inventory Report

status: completed; embedded runtime verified
updated: 2026-08-06

## Method

The required static scan was executed from the repository root:

```bash
rg -l -i "neo4j|GraphDatabase|session\.run|execute_query|CREATE INDEX|CREATE CONSTRAINT|CREATE FULLTEXT|db\.index|shortestPath|UNWIND|MERGE" code-tiny doc-tiny
```

The scan includes executable code, tests, documentation, generated examples,
and `code-tiny/tools/ts/ts_analyzer.py.bak`. Runtime classification below is
based on source inspection; documentation hits are not treated as runtime.

## Runtime Ownership

| Area | Evidence | Classification |
| --- | --- | --- |
| Driver abstraction | `tools/graph/core/base.py`, `factory.py`, `cli.py` | Provider-neutral public contract; Neo4j and FalkorDB implementations retained |
| FalkorDB implementation | `tools/graph/driver/falkordb_driver.py` | FalkorDB-specific connection, results, indexes, discovery, full-text behavior |
| Neo4j implementation | `tools/graph/driver/neo4j_driver.py` | Intentional rollback provider |
| Schema | `scripts/setup_constraints.py`, `scripts/setup_graph_project.py` | Provider-neutral CLI; provider-native FalkorDB indexes/constraints and Neo4j DDL |
| MCP | `mcp/unified_mcp.py`, `mcp/services/*.py`, backend graph services | Shared driver contract; no direct bridge `GraphDatabase.driver` construction in unified MCP |
| Code writers | `tools/graph/writer/*.py`, `tools/graph/operations/*.py` | Shared Cypher through `GraphDriver` |
| Primary analyzers | analyzer entrypoints under `tools/*` | Shared provider CLI; audited hardcoded Neo4j construction removed |
| doc-tiny supported runtime | `graph_store.py`, ingest/query/reset/index/MCP scripts | Provider adapter; Qdrant remains separate |
| doc-tiny legacy rollback | `neo4j_loader.py` | Explicit opt-in only via `DOC_ENABLE_LEGACY_NEO4J=1`; no import-time connection |
| doc-tiny LLM helper | `open_ai_exec.py` | Graph imports removed; graph persistence is outside this helper |

## Schema Inventory

- `CONSTRAINTS` in `code-tiny/scripts/setup_constraints.py` is the canonical
  uniqueness inventory. FalkorDB parsing extracts label/property tuples and
  creates prerequisite range indexes before unique constraints.
- `INDEXES` is the canonical range/composite index inventory.
- `FULLTEXT_INDEXES` contains two Neo4j named multi-label indexes. FalkorDB
  expands each to one label-oriented full-text index per label.
- `doc-tiny/graph_store.py` owns the document graph range indexes for `Chunk`,
  `Entity`, and `Document`.
- Qdrant remains the vector index provider; no FalkorDB vector index is needed
  for parity.

## Query and Transaction Inventory

- Shared writes use `UNWIND`, `MERGE`, `ON CREATE SET`, `ON MATCH SET`,
  `OPTIONAL MATCH`, `FOREACH`, and list predicates through the driver contract.
- `FalkorDBDriver` normalizes Neo4j importing-variable subqueries and
  `datetime()` parameters.
- Provider-specific full-text procedures and graph discovery are isolated in
  the driver/MCP provider boundary.
- Concurrent uniqueness-sensitive project/repository setup now creates and
  polls FalkorDB constraints before executing the shared MERGE query.

## Dependencies

- `falkordblite` is declared in both requirements files for the default local
  backend.
- Neo4j remains a compatibility provider and legacy `neo4j-graphrag` access is
  isolated rather than used by the supported doc-tiny runtime.
- Local path ownership and dependency bootstrap are governed by the extending
  local-file-storage plan and are outside this report's implementation scope.

## Static Scan Disposition

Every hit belongs to one of these explicit buckets:

1. provider implementation or provider-neutral runtime;
2. intentional Neo4j rollback/legacy compatibility code;
3. test fixture or migration tooling;
4. documentation, examples, comments, or the `.bak` source snapshot.

No supported analyzer entrypoint in the audited set still contains
`provider=GraphProvider.NEO4J`.

## External Dataset Disposition

- Neo4j/FalkorDB seeded parity and production-size data comparison would require both
  providers to be available.
- Constraint timing and concurrent MERGE throughput require a live FalkorDB
  runtime.
- Performance thresholds must be approved against representative project data.
