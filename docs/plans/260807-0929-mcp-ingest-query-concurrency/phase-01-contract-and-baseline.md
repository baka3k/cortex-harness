# Phase 01: Concurrency contract, generation schema, and baseline

## Context

The report is a `CAUTION`: availability is viable only if readers see a
committed snapshot and writers are isolated. Current locks, retries, and
thread offloading do not define a complete contract.

## Requirements

- Freeze physical-target identity, generation lifecycle, freshness metadata,
  job states, structured errors, and admission terminology.
- Record current behavior before changing concurrency: lease ownership,
  graph/Qdrant client lifecycle, sync lock scope, and MCP retrieval path.
- Define initial SLOs and benchmark dimensions without assuming a safe
  concurrency level from current thread-pool behavior.
- Freeze the single-owner process model, lock hierarchy, executor lanes,
  graceful lifecycle states, and user-facing overload/freshness behavior.
- Define one validated performance profile object (`safe`, `balanced`, or
  `custom`) instead of scattered environment-variable defaults.

## Architecture

Add typed contracts for `PhysicalTargetKey`, `GenerationManifest`,
`IngestionJob`, `FreshnessMetadata`, `StoreHealth`, and gateway errors. The
manifest must identify a graph/vector pair and source revision, not merely a
logical project or collection name.

## Related files

- New: `cortex_harness/storage/contracts.py`.
- Review: `cortex_harness/storage/config.py`, `layout.py`, `lease.py`,
  `code-tiny/tools/common/project_registry.py`, and the two completed plans.
- New tests: `tests/test_storage_concurrency_contract.py`.
- New ADR/report: `docs/decisions/` or plan-scoped report if the repository
  has no established ADR location.

## Implementation steps

1. Map logical `project_id`/graph/collection to a resolved physical target
   key including instance and owner paths.
2. Define generation states: `BUILDING`, `VALIDATING`, `PUBLISHED`,
   `FAILED`, `CANCELLED`, `RETIRING`, `RETIRED`.
3. Define job states and transitions, including duplicate, superseded,
   ambiguous-commit, and cancellation outcomes.
4. Define the initial reader/writer queue configuration surface and whether
   values come from environment, config, or explicit gateway construction.
5. Specify process lifecycle (`STARTING`, `RECOVERING`, `WARMING`, `READY`,
   `DRAINING`, `STOPPED`), one-worker enforcement, child isolation, and
   signal/recovery behavior.
6. Specify the lock order and executable rules: no upgrade, no storage call or
   await under state locks, writer permit held only for write/validate/publish,
   and deterministic ordering for any multi-target operation.
7. Define named bounded executor lanes, safe per-handle concurrency `1`, CPU
   worker/native-thread budgets, model warmup/cache rules, and reserved
   health capacity.
8. Define the UX contract for submit/status/follow/cancel, duplicate jobs,
   freshness waits, overload metadata, and the distinction between an empty
   result and an infrastructure failure.
9. Capture cold/warm baseline metrics and write the benchmark/fault/user-flow
   test matrix against the provisional SLO table in `plan.md`.

## Risks

The existing plan set uses both project-scoped and owner-scoped terminology.
Do not let a logical project ID become the physical directory key or bypass
the registry.

## Success criteria

Contracts are testable and unambiguous; every later phase can reference the
same target, generation, job, freshness, and error types; baseline tests prove
the lease remains fail-closed. Invalid worker counts, capacities, timeout
relationships, or memory/disk budgets fail configuration validation before
the owner opens storage.
