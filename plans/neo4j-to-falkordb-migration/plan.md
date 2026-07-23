# Neo4j to FalkorDB Migration Plan

status: in_progress
created: 2026-07-06
mode: hi-plan --full
scope: code-tiny, doc-tiny
blocks: [260713-1638-framework-parser-integration, 260714-1603-flutter-analyzer-parser, 260714-1702-cobol-analyzer-parser, 260715-1629-perl-analyzer-parser, 260715-2011-aspnet-roslyn-analyzers, 260719-0100-mcp-query-capability-hardening, 260719-2150-parser-mcp-runtime-alignment, 260723-0908-case-insensitive-project-id]

## Cross-Plan Dependency

The framework parser integration plan at `plans/260713-1638-framework-parser-integration/plan.md` depends on this migration stabilizing the provider argument/runtime contract, `GraphDriverFactory` configuration, provider-neutral query results, and schema/index setup. That plan will add MyBatis, Servlet/JSP, and Spring writers and MCP queries on top of these interfaces. Changes to `code-tiny/tools/graph`, `code-tiny/scripts/setup_constraints.py`, and `code-tiny/mcp/unified_mcp.py` must be coordinated across both plans.

The Flutter analyzer parser plan at `plans/260714-1603-flutter-analyzer-parser/plan.md` also depends on the provider-neutral writer and schema contract for its Dart canonical graph and Flutter semantic overlay. Its protocol/parser foundation can proceed independently, but provider-parity acceptance and changes to `code-tiny/tools/graph`, `code-tiny/scripts/setup_constraints.py`, and `code-tiny/mcp/unified_mcp.py` must be coordinated with this migration.

The COBOL analyzer parser plan at `plans/260714-1702-cobol-analyzer-parser/plan.md` depends on the same provider-neutral writer, schema, and query contract for namespaced COBOL semantic facts. Its parser, copybook resolver, and CFG phases can proceed independently, but provider-parity acceptance and changes to `code-tiny/tools/graph`, `code-tiny/scripts/setup_constraints.py`, and `code-tiny/mcp/unified_mcp.py` must be coordinated with this migration.

The Perl analyzer parser plan at `plans/260715-1629-perl-analyzer-parser/plan.md` depends on the provider-neutral writer and provider CLI/runtime contract for canonical Perl language facts. Its grammar, normalized extraction, and conservative incremental-resolution phases can proceed independently, but graph persistence and Neo4j/FalkorDB parity acceptance must be coordinated with this migration.

The ASP.NET Roslyn analyzers plan at `plans/260715-2011-aspnet-roslyn-analyzers/plan.md` depends on provider-neutral generation writes, cleanup, schema/index behavior, and MCP traversal for its shared migration semantic graph. Contract, Roslyn, and framework extraction phases can proceed independently, but graph persistence and Neo4j/FalkorDB parity acceptance must use the stabilized provider contract.

The MCP query capability hardening plan at
`plans/260719-0100-mcp-query-capability-hardening/plan.md` extends provider schema
inspection to node labels, adds provider-neutral web/database overlay writers, and
uses FalkorDB as the active acceptance target. Its live Neo4j parity remains gated
by this migration.

The parser-MCP runtime alignment plan at
`plans/260719-2150-parser-mcp-runtime-alignment/plan.md` separates parser profiles
from exact framework filters, adds live provider schema observability, and
removes provider-specific wording from public MCP metadata. Neo4j/FalkorDB live
parity remains gated by this migration.

The case-insensitive project-scope plan at
`plans/260723-0908-case-insensitive-project-id/plan.md` depends on this migration
for provider-neutral persistence, exact normalized-field predicates, index setup,
and Neo4j/FalkorDB parity. Changes to graph drivers, query helpers, schema setup,
and unified MCP bridge queries must preserve both plans' contracts.

## Objective

Migrate the graph database integration from Neo4j to FalkorDB while preserving existing business logic, query behavior, CLI/API surfaces, and validation confidence.

This plan intentionally starts with a complete inventory and compatibility pass before implementation. The current codebase contains enough Neo4j-specific query, schema, and driver usage that a direct dependency swap would be risky.

## Verified Facts

- `code-tiny` already has a graph database abstraction in `code-tiny/tools/graph/core/base.py`; `GraphProvider.FALKORDB` exists, but `GraphDriverFactory` currently raises `NotImplementedError` for it.
- `code-tiny/tools/graph/driver/neo4j_driver.py` contains the main concrete Neo4j implementation, including connection handling, `execute_query`, batch writes, index creation, full-text search, path queries, and high-level graph query methods.
- `code-tiny/scripts/setup_constraints.py` defines Neo4j uniqueness constraints, many range indexes, and two full-text indexes.
- `code-tiny/mcp/unified_mcp.py` still contains direct Neo4j bridge code and raw Cypher paths outside the driver abstraction.
- `doc-tiny` uses direct `neo4j.GraphDatabase` calls in `0_reset_all.py`, `6_setup_indexes.py`, `graphrag_ingest_langextract.py`, `graphrag_query_langextract.py`, `mcp_graph_rag.py`, `neo4j_loader.py`, and `open_ai_exec.py`.
- `doc-tiny` stores vectors in Qdrant and graph data in Neo4j; the migration should not move Qdrant unless explicitly requested.
- A targeted scan did not find explicit APOC procedure/function usage in the main `code-tiny` and `doc-tiny` paths, but the inventory phase must confirm this across all generated/documented scripts before implementation.
- FalkorDB documentation shows Python usage through `from falkordb import FalkorDB`, `select_graph(...)`, and `graph.query(...)`; it also supports OpenCypher, range indexes, full-text indexes, vector indexes, constraints, and `db.*` procedures, but with different syntax/semantics from Neo4j in several places.

## Sources Checked

- FalkorDB docs home/client examples: https://docs.falkordb.com/
- FalkorDB Neo4j migration guide: https://docs.falkordb.com/operations/migration/neo4j-to-falkordb.html
- FalkorDB constraints: https://docs.falkordb.com/commands/graph.constraint-create.html
- FalkorDB range indexes: https://docs.falkordb.com/cypher/indexing/range-index.html
- FalkorDB procedures: https://docs.falkordb.com/cypher/procedures.html
- FalkorDB limitations: https://docs.falkordb.com/cypher/known-limitations.html
- FalkorDB Python client README: https://github.com/FalkorDB/falkordb-py

## Scope Challenge

1. Should this be a hard cutover or a dual-provider migration?
   - Selected: dual-provider first. Reason: `code-tiny` already has a provider abstraction and existing Neo4j behavior is still the reference oracle.

2. Should Qdrant-backed vector retrieval be folded into FalkorDB vector indexes now?
   - Selected: no. Reason: the current system explicitly uses Qdrant and the user request is Neo4j to FalkorDB. FalkorDB vector indexes can be evaluated later after functional parity.

3. Should all raw Cypher be rewritten immediately into provider-neutral methods?
   - Selected: only where it affects FalkorDB compatibility. Reason: minimize unrelated churn while converting unsupported Neo4j-specific syntax and procedures.

## Assumptions To Validate

- Target FalkorDB deployment is available on RESP port `6379`, with optional username/password.
- One FalkorDB graph name can replace Neo4j database selection in local workflows, unless multi-tenant or multi-project isolation needs separate graphs.
- The desired Python client is `falkordb` from `falkordb-py`; using FalkorDB Bolt support with the Neo4j driver is a fallback only if compatibility tests prove it is safer.
- Existing public CLI argument names may keep `--neo4j-*` aliases temporarily, but new `--falkordb-*` names should be added for clarity.

## Preliminary Inventory

### code-tiny

Main graph abstraction:
- `tools/graph/core/base.py`
- `tools/graph/core/factory.py`
- `tools/graph/driver/neo4j_driver.py`

Neo4j-specific scripts:
- `scripts/setup_constraints.py`
- `scripts/setup_graph_project.py`
- `scripts/migrate_repo_file_edges.py`
- `scripts/link_project_repos.py`
- `scripts/cleanup_repo_graph.py`
- `scripts/ingest_workflows.py`
- `list_db.py`
- `run_migration.py`

Neo4j-specific service paths:
- `mcp/unified_mcp.py`, especially direct bridge driver setup and API call-chain queries.
- backend-specific MCP modules that still issue raw Cypher or Neo4j full-text procedure calls.

Primary labels observed:
- `Project`, `Repository`, `Function`, `File`, `Class`, `Namespace`, `Type`, `Package`, `Field`, `Alias`, `Template`, `FunctionType`, `Message`, `MessageEndpoint`, `InfraNode`, `Workflow`, `Paragraph`, `Chunk`, `Slide`, `AndroidManifest`, `AndroidComponent`, `AndroidResource`, `GradleModule`, `AndroidIntentAction`, `AndroidAnnotation`, `ApiEndpoint`, `ApiCall`, `Controller`, `Service`, `Database`, `DataRepository`.

Primary relationships observed:
- `CONTAINS`, `HAS_REPOSITORY`, `HAS_FILE`, `POSSIBLE_CALLS`, `CALLS`, `CALLS_API`, `MATCHES`, `HANDLES`, `QUERIES`, `HAS_STEP`.

Neo4j-specific areas:
- `SHOW DATABASES`.
- Neo4j driver session/database handling.
- Neo4j schema syntax: `CREATE CONSTRAINT ... REQUIRE ... IS UNIQUE`, `CREATE FULLTEXT INDEX ... FOR (n:Label|Label) ON EACH [...]`, index names with `IF NOT EXISTS`.
- Neo4j procedure names such as `db.index.fulltext.queryNodes`.
- Subquery form `CALL () { ... UNION ALL ... }`.
- `shortestPath` and variable-length relationship patterns.
- Transaction assumptions around concurrent `MERGE` plus uniqueness constraints.

### doc-tiny

Direct driver and graph scripts:
- `0_reset_all.py`
- `6_setup_indexes.py`
- `graphrag_ingest_langextract.py`
- `graphrag_query_langextract.py`
- `mcp_graph_rag.py`
- `neo4j_loader.py`
- `open_ai_exec.py`

Primary labels observed:
- `Document`, `Paragraph`, `Entity`, `Chunk`.

Primary relationships observed:
- `HAS_PARAGRAPH`, `HAS_ENTITY`, `RELATED`.

Primary Cypher patterns observed:
- `MATCH (n) DETACH DELETE n`
- `MERGE (d:Document {id: ...})`
- `MERGE (p:Paragraph {source_id: ..., paragraph_id: ...})`
- `MERGE (e:Entity {id: ...})`
- `MERGE (p)-[r:HAS_ENTITY]->(e)`
- `MERGE (s)-[r:RELATED {...}]->(t)`
- `UNWIND $paragraphs/$entities/$relations AS row`
- `MATCH (e:Entity)-[r]-(related)`
- `MATCH (e:Entity {id: id})-[r:RELATED]-(e2:Entity)`

Schema observed:
- `Chunk(doc_id, id)` composite index.
- `Entity(name)` index.
- `Entity(type)` index.
- `Document(id)` index.

## Compatibility Risks

- FalkorDB unique constraints require a supporting exact-match/range index before creation and are created asynchronously; Neo4j code assumes synchronous `CREATE CONSTRAINT` success.
- FalkorDB full-text procedure names and arguments differ from Neo4j `db.index.fulltext.queryNodes(indexName, query)`.
- FalkorDB graph selection is graph-name based; Neo4j database selection maps poorly to `session(database=...)`.
- FalkorDB documented limitations around relationship uniqueness in patterns and `LIMIT` with eager operations can affect count queries, `MERGE`, `CREATE`, and batch import behavior.
- `MERGE` concurrency guarantees may not match the Neo4j uniqueness-lock behavior described in the current scripts; this requires stress testing before enabling parallel writers.
- `shortestPath`, subqueries, variable-length patterns, list functions, and `CALL` forms must be compatibility-tested against FalkorDB, not assumed.
- Result records returned by `falkordb-py` are row/list oriented; existing code often expects Neo4j `record.data()` dictionaries and node/relationship objects with dict-like properties.
- `neo4j-graphrag` dependencies in `doc-tiny` are likely Neo4j-specific and may need removal, replacement, or isolation if they are still used in runtime paths.

## Target Architecture

### code-tiny

Add `FalkorDBDriver` behind the existing `GraphDriver` interface.

Responsibilities:
- Connect via `falkordb.FalkorDB`.
- Select one graph by configured graph/database name.
- Implement `execute_query` and `execute_query_sync` with Neo4j-compatible return shape: `(records: list[dict], keys: list[str], summary: any)`.
- Implement high-level query methods directly where FalkorDB syntax/procedures differ.
- Preserve `Neo4jDriver` for comparison, fallback, and rollout.
- Update `GraphDriverFactory` and environment loading for `GraphProvider.FALKORDB`.

### doc-tiny

Introduce a minimal adapter module, for example `doc-tiny/graph_store.py`, rather than scattering direct `falkordb` calls.

Responsibilities:
- Hide driver differences from ingest/query/reset scripts.
- Preserve existing function intent and CLI behavior.
- Add FalkorDB config while keeping transitional aliases if useful.
- Keep Qdrant flow unchanged.

## Phase Plan

1. Phase 01 - Inventory And Compatibility Matrix
   - Produce a complete static inventory report.
   - Generate a file/query/schema migration matrix.
   - Decide exact query rewrites based on tested FalkorDB behavior.

2. Phase 02 - FalkorDB Driver Foundation
   - Add dependencies and config.
   - Implement `FalkorDBDriver`.
   - Normalize result parsing and graph selection.

3. Phase 03 - Schema Migration
   - Convert code-tiny and doc-tiny indexes/constraints.
   - Replace Neo4j schema DDL with FalkorDB-compatible schema commands.
   - Add constraint status polling.

4. Phase 04 - Cypher And Service Migration
   - Convert unsupported Neo4j Cypher/procedure patterns.
   - Move remaining direct service queries behind provider-aware code where needed.
   - Keep provider-neutral APIs stable.

5. Phase 05 - doc-tiny Application Migration
   - Replace direct `GraphDatabase` usage with the doc graph adapter.
   - Preserve GraphRAG ingest/query behavior.
   - Keep Qdrant integration unchanged.

### 2026-07-06 Focus Update - Scan Scripts

The current implementation already contains a FalkorDB driver and `doc-tiny`
graph-store adapter. The remaining requested work is to stop scan entrypoints
from deciding graph writes solely from `--neo4j-*` credentials.

Implementation direction:
- Add a shared `code-tiny/tools/graph/cli.py` helper for provider selection,
  FalkorDB CLI/env options, and driver creation.
- Keep existing `--neo4j-*` flags as compatibility aliases.
- Add `--graph-provider falkordb` and `FALKORDB_*` support to analyzer and
  message scan scripts under `code-tiny/tools`.
- Treat the existing internal `neo4j_db` variable as the selected graph/database
  name during the transition, so downstream writer code does not need a broad
  rename.
- Update `doc-tiny/graphrag_ingest_langextract.py` naming/logging so the ingest
  path is graph-store neutral and can run against the existing FalkorDB adapter.

6. Phase 06 - Data Migration And Backfill
   - Add or document export/import flow.
   - Validate labels, relationships, properties, and counts.
   - Support rollback by keeping Neo4j untouched.

7. Phase 07 - Validation, Performance, And Rollout
   - Run parity tests against Neo4j and FalkorDB.
   - Run query-plan/performance checks.
   - Update docs, sample env, and operational checklist.

## Deliverables

- `inventory-report.md`
- `compatibility-matrix.md`
- `schema-migration-report.md`
- `cypher-migration-report.md`
- `file-migration-report.md`
- `validation-checklist.md`
- FalkorDB driver and adapter implementation.
- Updated requirements and docs.
- Tests proving Neo4j-to-FalkorDB parity for supported workflows.

## Acceptance Criteria

- All direct Neo4j runtime dependencies in the requested migration scope are inventoried and either migrated, isolated behind a provider abstraction, or explicitly documented as intentionally retained.
- `code-tiny` can instantiate `GraphProvider.FALKORDB` without `NotImplementedError`.
- `doc-tiny` ingest, query, MCP GraphRAG tools, reset, and schema setup can run against FalkorDB.
- Schema setup is idempotent for FalkorDB and reports asynchronous constraint failures.
- All migrated Cypher has documented original query, converted query, compatibility notes, and performance notes.
- Validation can compare source Neo4j and target FalkorDB graph counts, labels, relationship types, property keys, and selected query outputs.
- No unrelated business logic is rewritten.

## Recommended Cook Command

After review:

```powershell
/hi-cook C:\ai\cortex-harness\plans\neo4j-to-falkordb-migration\plan.md --full
```
