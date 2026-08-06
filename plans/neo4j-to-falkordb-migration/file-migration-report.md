# File Migration Report

status: implementation complete for audited scope
updated: 2026-08-06

## Provider and Schema

- `code-tiny/scripts/setup_constraints.py`: provider-neutral CLI, FalkorDB
  schema conversion, unique-constraint status polling, actionable failures.
- `code-tiny/scripts/setup_graph_project.py`: provider selection, native
  FalkorDB uniqueness setup, shared hierarchy query.

## Analyzer Entrypoints

The following entrypoints now call `create_graph_driver_from_args(args)` rather
than constructing `GraphProvider.NEO4J` directly:

- Android Java and Android Kotlin
- C#, Java, JavaScript, Kotlin
- PHP, PL/SQL, Python, SQL, TypeScript
- shared Visual Basic analyzer base

The provider-neutral helper preserves legacy `neo4j_*` argument fields for
downstream writers while selecting the configured driver.

## Maintenance and Workflow Entrypoints

- `scripts/cleanup_repo_graph.py`, `link_project_repos.py`,
  `migrate_repo_file_edges.py`, and `ingest_workflows.py` now execute Cypher
  through the shared `GraphDriver` contract and default to embedded
  FalkorDBLite. Their existing `--neo4j-*` flags remain compatibility aliases;
  Neo4j is used only when `--graph-provider neo4j` is selected and complete
  credentials are supplied.
- `mcp/services/impact_service.py` now obtains its workflow scorer driver from
  the process-local shared runtime. Scorers are cached per logical graph name,
  while Neo4j remains an explicit credential-gated rollback provider.

## MCP Runtime Entrypoints

- CPlus, Fast, Android, and standalone Java MCP use the same process-local
  embedded driver for one physical owner path. Their live database discovery
  calls the provider-neutral driver API, so unscoped queries aggregate every
  registered graph instead of collapsing to an environment default.
- Standalone Java defaults to FalkorDBLite; Neo4j requires explicit provider
  selection and complete rollback credentials.

## doc-tiny

- `graph_store.py` remains the supported provider adapter used by reset,
  indexes, ingest, query, and MCP GraphRAG.
- `neo4j_loader.py` is retained solely as explicit rollback compatibility; it
  has no import-time connection and requires `DOC_ENABLE_LEGACY_NEO4J=1`.
- `open_ai_exec.py` no longer imports Neo4j or `neo4j-graphrag`; it is an
  isolated LLM parsing helper.

## Tests Added

- `code-tiny/tests/test_falkordb_schema_migration.py`
- `code-tiny/tests/test_analyzer_provider_wiring.py`
- `doc-tiny/tests/test_legacy_neo4j_isolation.py`
- `tests/test_active_graph_provider_cutover.py`

## Intentionally Retained

- `Neo4jDriver` and Neo4j schema path for rollback/parity.
- Neo4j naming aliases where removing them would break CLI compatibility.
- Qdrant storage and embedding behavior.
- Historical documentation/examples that explicitly describe rollback-only
  Neo4j behavior.
