# Phase 03 - Schema Migration

## Goal

Convert Neo4j schema setup into FalkorDB-compatible range, full-text, vector, and constraint setup.

## Tasks

1. Convert range/property indexes.
   - Neo4j: `CREATE INDEX name IF NOT EXISTS FOR (n:Label) ON (n.prop)`
   - FalkorDB target: `CREATE INDEX FOR (n:Label) ON (n.prop)`
   - Preserve composite indexes where FalkorDB supports the property combination.

2. Convert constraints.
   - Neo4j: `CREATE CONSTRAINT name IF NOT EXISTS FOR (n:Label) REQUIRE n.prop IS UNIQUE`
   - FalkorDB target: `GRAPH.CONSTRAINT CREATE graph UNIQUE NODE Label PROPERTIES 1 prop`, through the Python client helper if available.
   - Ensure supporting exact-match/range indexes exist before unique constraints.
   - Poll `CALL db.constraints()` until constraints become operational or failed.

3. Convert full-text indexes.
   - Neo4j named index calls must be mapped to FalkorDB label/property full-text indexes.
   - Replace query procedure calls using `db.index.fulltext.queryNodes(indexName, query)` with FalkorDB-compatible calls such as `db.idx.fulltext.queryNodes(label, query)`.
   - If one Neo4j full-text index spans many labels, either create one FalkorDB full-text index per label or implement provider-specific fan-out.

4. Confirm vector index requirements.
   - Current `doc-tiny` uses Qdrant for vectors, so no FalkorDB vector index is required for parity.
   - Document optional future migration path to FalkorDB vector indexes.

5. Update schema scripts.
   - `code-tiny/scripts/setup_constraints.py`
   - `code-tiny/scripts/setup_graph_project.py`
   - `doc-tiny/6_setup_indexes.py`

## Validation

- `CALL db.indexes()` returns expected FalkorDB range/full-text indexes.
- `CALL db.constraints()` returns expected status for unique/mandatory constraints.
- Re-running schema setup is idempotent or produces handled "already exists" results.

## Risks

- FalkorDB constraints are asynchronous.
- Unique constraints do not apply to missing/null properties and are not enforced on array-valued properties.
- Full-text index grouping differs from Neo4j named multi-label indexes.

