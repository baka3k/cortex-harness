# Migration Validation Checklist

status: completed for the embedded local-runtime cutover
updated: 2026-08-06

## Static and Unit Validation

- [x] `GraphProvider.FALKORDB` is wired through `GraphDriverFactory`.
- [x] Audited analyzer entrypoints use shared provider-aware driver creation.
- [x] FalkorDB range/composite index declarations are parsed.
- [x] Supporting range indexes precede unique constraints.
- [x] Constraint pending status is polled to operational.
- [x] Constraint failed status is surfaced as an error.
- [x] Multi-label full-text declarations expand per label.
- [x] Project/repository setup uses provider-native constraints and shared MERGE.
- [x] doc-tiny supported runtime uses `graph_store`.
- [x] Legacy Neo4j loader has explicit opt-in and no import-time connection.
- [x] Qdrant behavior was not migrated into FalkorDB.
- [x] Required migration report artifacts exist.

## Commands

```bash
PYTHONPATH=code-tiny:doc-tiny python -m pytest -q \
  code-tiny/tests/test_falkordb_schema_migration.py \
  code-tiny/tests/test_analyzer_provider_wiring.py \
  doc-tiny/tests/test_legacy_neo4j_isolation.py

PYTHONPATH=code-tiny:doc-tiny python -m pytest -q \
  code-tiny/tests code-tiny/tools/common/test_*.py doc-tiny/tests
```

## Embedded FalkorDB Gates

- [x] Embedded `RETURN 1 AS test` succeeds in the dependency-managed runtime.
- [x] Schema setup succeeds twice without unhandled already-exists errors.
- [x] Schema setup reports the declared range/full-text inventory (143 range,
  242 full-text entries in the current canonical declaration set).
- [x] Schema setup reports all 102 current unique constraints operational.
- [x] Document fixture tests create project-scoped nodes and relationships.
- [x] Reset tests clear only the selected graph and owner-scoped collection.
- [x] MCP representative scalar/node/relationship/path outputs are parseable.
- [x] CPlus, Fast, Android, and standalone Java MCP share one embedded driver,
  discover live graph names provider-neutrally, and aggregate two real graph
  shards without mocked discovery.

## Neo4j-to-FalkorDB Data Parity Disposition

- [x] No source Neo4j dataset was supplied; node-count parity is recorded as
  not applicable rather than inferred.
- [x] No source Neo4j dataset was supplied; relationship-count parity is
  recorded as not applicable rather than inferred.
- [x] Required property-key and duplicate-key behavior is covered by canonical
  writer/schema tests against the embedded target.
- [x] Stable-ID and normalized-result behavior is covered by deterministic
  driver, ingest, and two-project fixture tests.
- [x] Existing Neo4j data is left untouched; operators can export/re-ingest it
  later through the documented migration path.

## Performance and Rollout Gates

- [x] Automated schema, ingest, query, reset, restart-persistence, and
  two-project isolation checks complete within the repository test budget.
- [x] Full-text native setup and fallback behavior are covered by driver/schema
  tests; production latency is dataset-dependent and no dataset was supplied.
- [x] Path and GraphRAG result normalization is covered by MCP acceptance;
  production latency/memory baselines remain deployment-specific.
- [x] Embedded owner leases fail fast on concurrent cross-process opens; the
  local contract intentionally forbids concurrent writers to one `.rdb` file.
- [x] Neo4j provider remains available as an explicit rollback path.
- [x] This requested full-plan execution records local FalkorDBLite as the
  default cutover and the explicit Neo4j provider as the rollback path.
