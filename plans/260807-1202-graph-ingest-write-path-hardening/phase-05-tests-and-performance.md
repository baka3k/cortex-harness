# Phase 05: Correctness, query-plan, and scale gates

## Context

Existing tests cover individual driver and schema helpers but do not enforce
preflight ordering, endpoint index scans, relationship integrity, or behavior
as total graph size grows. A regression can therefore reintroduce the same
apparent hang without failing CI.

## Requirements

- Unit tests cover manifest validation, ordering, compilation, integrity, and
  resume rules.
- Real temporary FalkorDB tests inspect schema readiness and query plans.
- Provider parity is retained without weakening FalkorDB performance gates.
- Scale/fault tests are deterministic and never use registered user stores.
- Acceptance failures include artifacts sufficient to diagnose the query.

## Architecture

Use layered gates: fast pure-contract tests, fake-driver ordering tests, real
temporary-store integration tests, and an opt-in/CI benchmark job. Store
explain plans, schema state, latency distributions, versions, and fixture seeds
as test artifacts.

## Related files

- `tests/test_falkordb_driver.py`
- `code-tiny/tests/test_falkordb_schema_migration.py`
- `tests/test_cobol_graph_contract.py` and language writer contract suites
- New query-plan, ingestion-recovery, and benchmark suites

## Implementation steps

1. Unit-test manifest validation, strict identifiers, activation, fingerprints,
   and malformed/duplicate declarations.
2. Assert with a fake driver that preflight reaches operational before cleanup
   or the first mutation and that required failure yields zero batches.
3. Test grouping by endpoint labels/type/scope and exact result reconciliation,
   including missing/ambiguous endpoints and cross-project IDs.
4. Start isolated FalkorDBLite instances and assert `CALL db.indexes()` state
   plus `GRAPH.EXPLAIN` index scans for both endpoints.
5. Make `All Node Scan`/Cartesian product in critical endpoint plans a hard
   failure, not a warning or snapshot update.
6. Add a repository-wide static test prohibiting unlabeled identity mutations;
   explicitly test C++, Pro*C, Android Java/Kotlin, COBOL, Shell, JP1, Struts,
   TypeScript backend, topology, and cross-edge fixture paths.
7. Run fixed 1,000-row batches at 1k/10k/100k/500k total nodes and enforce the
   plan-level latency/scaling gates with cold/warm results separated.
8. Execute fault cases: DDL failure, index readiness timeout, duplicate identity,
   missing endpoint, malformed manifest, query timeout, cancel/restart, stale
   checkpoint, and ambiguous commit.
9. Run twice and compare final node/relationship counts/properties, including
   relations whose old path increments counters on rerun. Compare
   Neo4j/FalkorDB contract results for supported semantics.

## Todo

- [x] Add pure contract and fake-driver ordering tests.
- [x] Add temporary FalkorDB schema/readiness integration tests.
- [x] Enforce explain-plan index usage for critical endpoint lookups.
- [x] Add scale fixtures and publish baseline versus fixed results.
- [x] Cover every known direct writer/analyzer mutation path and static guard.
- [x] Cover all recovery and integrity fault cases.
- [x] Prove idempotency, provider parity, and user-store isolation.

## Risks

Wall-clock thresholds can be noisy. The structural explain-plan invariant and
scaling ratio are primary; absolute latency is reported by hardware profile and
revised only from recorded evidence, never simply relaxed after a failure.

## Success criteria

CI prevents unlabeled endpoint scans and preflight regressions; the supported
scale profile meets the plan gates; all faults produce typed, recoverable
outcomes without partial publication or user-store contamination.
