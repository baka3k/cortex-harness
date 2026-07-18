# Phase 04: SQL and PL/SQL Semantic Profile

## Context

SQL and PL/SQL currently advertise full generic symbol support but their MCP
profiles do not describe database objects or data-access relationships.

## Requirements

- Add specialized labels `Table`, `View`, and `Procedure`.
- Add `READS_FROM`, `WRITES_TO`, and `REFERENCES_TABLE` relationships.
- Parse common SQL/PLSQL DDL and DML conservatively from real files.
- Link procedure facts to canonical primary-analyzer symbols when possible.

## Architecture

Create a shared database-schema overlay after `sql`/`plsql` primary analysis.
Normalize quoted/schema-qualified identifiers and mask comments/string literals
before relationship extraction.

## Related Files

- `code-tiny/tools/database_schema/`
- `code-tiny/tools/graph/writer/database_schema_writer.py`
- `code-tiny/mcp/framework_registry.py`
- `code-tiny/tools/sync/incremental_sync.py`
- SQL/PLSQL fixture tests

## Implementation Steps

1. Define database fact and relationship contracts.
2. Implement DDL/DML extraction and identifier normalization.
3. Implement provider-neutral writer and overlay orchestration.
4. Upgrade SQL/PLSQL query profiles and database support dimensions.

## Todo

- [ ] Add SQL/PLSQL fixtures and failing semantic tests.
- [ ] Implement extractor/writer.
- [ ] Register profiles/overlay.
- [ ] Verify database capability queries.

## Risks

- SQL dialect ambiguity; unsupported dynamic SQL remains unresolved and explicit.

## Success Criteria

- Fixture procedures/views produce deterministic database nodes and read/write
  lineage edges without false references from comments or string literals.

