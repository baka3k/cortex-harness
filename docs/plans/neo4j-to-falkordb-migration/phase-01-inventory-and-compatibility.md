# Phase 01 - Inventory And Compatibility Matrix

## Goal

Create the complete migration inventory required before changing runtime code.

## Tasks

1. Build a static query inventory.
   - Scan `code-tiny` and `doc-tiny` for `GraphDatabase`, `session.run`, `execute_query`, Cypher string literals, schema DDL, and procedure calls.
   - Capture file path, function/class, line number, original query, parameters, and caller purpose.

2. Build schema inventory.
   - Extract all constraints, range/property indexes, composite indexes, full-text indexes, and any vector-index references.
   - Classify Neo4j-only syntax versus FalkorDB-compatible syntax.

3. Build graph model inventory.
   - Extract labels, relationship types, property keys, and identifier conventions.
   - Mark source of truth as verified from code, verified from live database, or inferred.

4. Build transaction and batch inventory.
   - Identify all `MERGE`, `UNWIND`, batch write, retry, transaction, and concurrent writer paths.
   - Flag any path relying on Neo4j uniqueness locks.

5. Build dependency inventory.
   - Classify `neo4j`, `neo4j-graphrag`, and FalkorDB replacement dependencies by module.

## Output Files

- `inventory-report.md`
- `compatibility-matrix.md`

## Validation

- Static report includes every file returned by `rg -l -i "neo4j|GraphDatabase|session.run|execute_query|CREATE INDEX|CREATE CONSTRAINT|CREATE FULLTEXT|db.index|shortestPath|UNWIND|MERGE" code-tiny doc-tiny`.
- Report distinguishes verified facts, assumptions, and unresolved questions.

## Risks

- Some Cypher is built dynamically from string fragments; inventory tooling must include string assembly sites, not only triple-quoted strings.
- Documentation files contain migration examples that should not be treated as runtime code unless they are executable scripts.

