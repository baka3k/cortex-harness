# Phase 02: Canonical schema manifest and automatic preflight

## Context

Schema knowledge is split between a standalone script, analyzer-local index
lists, and minimal sync startup checks. C++ creates indexes after streaming,
and driver failures are logged instead of preventing the run.

## Requirements

- One provider-neutral, validated schema manifest is authoritative.
- Required indexes are created and operational before any cleanup or write.
- Direct writer callers receive the same enforcement as CLI sync callers.
- Required failures abort without partial streaming; optional index failures
  are explicit in the result and metrics.
- Index readiness and caching are scoped to the resolved physical target.

## Architecture

Add typed schema models for node identity, index/constraint declarations,
writer contracts, provider capability, readiness state, and fingerprints.
Drivers compile the models to provider DDL and introspection queries.
Orchestration runs preflight eagerly, while a writer guard provides a final
single-flight invariant before mutation.

## Related files

- New: `code-tiny/tools/graph/schema/{models,manifest,preflight}.py`
- Update: `code-tiny/tools/graph/driver/{base,neo4j,falkordb}_driver.py`
- Refactor: `code-tiny/scripts/setup_constraints.py`
- Update: `code-tiny/tools/sync/incremental_sync.py`
- Update analyzer entry points that bypass incremental sync.

## Implementation steps

1. Define immutable manifest models and strict identifier validation; reject
   malformed entries before opening storage.
2. Populate contracts from the Phase 01 inventory, including C++/Pro*C SQL
   labels and all endpoint labels used by generic writers.
3. Implement provider compilers and exact schema introspection. FalkorDB uses
   label/property range indexes and `CALL db.indexes()` readiness; Neo4j uses
   its existing constraint/index capabilities behind the same result model.
4. Audit duplicates before a uniqueness constraint. Return an actionable
   report and stop; do not merge or delete nodes automatically.
5. Add bounded readiness polling with start/deadline/status output and typed
   timeout. Validate provider support/version before enabling write limits.
6. Cache only successful readiness by `(provider, physical target, database,
   manifest fingerprint)` using single-flight coordination.
7. Call preflight before cleanup/streaming in sync, and enforce a writer-local
   `require_schema_ready()` before the first mutation.
8. Replace standalone and analyzer-local schema lists with manifest consumers;
   keep the setup script as a manual doctor/repair interface only.

## Todo

- [x] Add and validate the canonical schema models and manifest.
- [x] Implement FalkorDB and Neo4j preflight/introspection adapters.
- [x] Prove required indexes become operational before the first mutation.
- [x] Make required preflight errors fail-closed and typed.
- [x] Refactor setup and analyzer-local index declarations to the manifest.
- [x] Test target-scoped single-flight caching and fingerprint invalidation.

## Risks

Index creation can be asynchronous, and uniqueness can fail on dirty graphs.
Merely receiving a successful DDL response is not readiness. Indexing every
known label also imposes write and storage cost, so activate only the contracts
needed for the current run.

## Success criteria

A fresh target requires no manual setup; an existing target with missing,
building, failed, or conflicting schema cannot begin streaming; all setup paths
produce the same manifest fingerprint and readiness report.
