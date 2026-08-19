# Scoped graph materialization safety — 2026-08-19

## Context

A C-family ingestion run could discover source records locally yet fail to
materialize their relationships. Investigation isolated two identity hazards:
relationship endpoints were not consistently constrained to one project scope,
and malformed whitespace-padded symbols could reach an indexed lookup. Selecting
a nested scan root could also create a second relative-path identity namespace.
This extends the existing [graph write-path hardening plan](../../plans/260807-1202-graph-ingest-write-path-hardening/plan.md).

## Change

- Typed relationship rows now require a normalized project scope, and both
  endpoint matches use the label, identity, and normalized scope
  (`code-tiny/tools/graph/writer/query_contract.py:41`,
  `code-tiny/tools/graph/writer/query_contract.py:85`).
- A read-only endpoint-cardinality audit runs before mutation; unresolved or
  ambiguous endpoints fail the batch without partially creating otherwise valid
  edges. A post-write count mismatch also fails with bounded audit evidence
  (`code-tiny/tools/graph/writer/query_contract.py:103`,
  `code-tiny/tools/graph/writer/language_writer.py:1484`,
  `code-tiny/tools/graph/writer/language_writer.py:1551`).
- C-family identities with leading or trailing whitespace are quarantined before
  graph writes, including dependent relations and calls
  (`code-tiny/tools/common/payload_validation.py:201`,
  `tests/test_analyzer_payload_validation.py:83`).
- First-parse and cached C-family payloads now follow the same normalization
  path, while SQL-bearing custom nodes and parse-run records carry normalized
  project scope (`code-tiny/tools/cplus/cplus_analyzer.py:3035`,
  `code-tiny/tools/cplus/cplus_analyzer.py:3241`,
  `code-tiny/tools/cplus/cplus_analyzer.py:4465`,
  `code-tiny/tools/cplus/cplus_analyzer.py:5576`).
- The sync command rejects a selected nested root when a configured ancestor
  exists, preventing path-relative identities from changing namespace
  (`cortex_harness/dev.py:344`, `cortex_harness/dev.py:370`).

## Impact

**Risk level: high.** Relationship materialization is now project-isolated and
fail-closed, which prevents cross-project joins, silent partial batches, and
unqueryable malformed endpoint identities. The change affects the shared writer
and therefore every analyzer that emits typed relationships.

A clean disposable graph run completed all 41 C-family write buffers over 20,186
files, materialized 121,631 function nodes, and reported no duplicate scoped file
or function identities. Focused contract and embedded-driver tests cover exact
endpoint selection, repeated identical input rows, preflight failure, and
idempotent relationship creation
(`code-tiny/tests/test_relationship_query_contract.py:73`,
`code-tiny/tests/test_relationship_query_contract.py:99`,
`tests/test_falkordb_driver_local.py:253`).

## Decision

Endpoint scope is part of relationship identity rather than optional metadata.
The writer audits cardinality before `MERGE` so a bad row cannot leave a partial
batch behind. Parser leakage is quarantined at the payload boundary instead of
being normalized into a potentially different symbol. Scan roots remain
canonical and ancestor-based so independent analyzers can join on stable file
IDs.

The successful verification used a fresh disposable graph. The existing shared
store was not replaced in place; rebuilding into a new generation, verifying it,
and switching configuration with the previous generation retained for rollback
is the required rollout path.

## References

- Plan: [Graph ingestion write-path hardening](../../plans/260807-1202-graph-ingest-write-path-hardening/plan.md)
- Contract tests: `code-tiny/tests/test_relationship_query_contract.py:73`
- Embedded-driver test: `tests/test_falkordb_driver_local.py:253`
- C-family scope test: `tests/test_cplus_graph_runtime.py:158`
- Root-selection tests: `tests/test_dev_sync_reliability.py:583`
