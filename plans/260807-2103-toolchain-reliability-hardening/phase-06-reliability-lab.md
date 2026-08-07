# Phase 06: Reliability laboratory and fault injection

## Context

Unit tests with recording drivers cannot prove persistence, crash recovery,
provider behavior, or subprocess UX. Stability requires a repeatable lab that
tests real temporary stores, real child processes, and controlled faults across
every certified analyzer/provider path.

## Requirements

- Never use registered user graph/vector paths.
- Exercise real temporary FalkorDB/Qdrant and Neo4j-compatible paths where
  available.
- Test deterministic fixtures, randomized/property-based contracts, and
  process-level fault injection.
- Assert final storage state, active generation, baseline, artifacts, exit code,
  and rendered UX—not only exceptions.
- Produce machine-readable conformance and benchmark reports.
- Make certification matrix visible by analyzer and provider.

## Architecture

Build a reusable test harness with:

- isolated storage instance/owner roots;
- deterministic run clock/IDs where needed;
- analyzer fixture builders and payload mutation hooks;
- storage fault proxy/hooks for partial count, timeout, crash, disk-full,
  corruption, and delayed acknowledgment;
- process kill points at journal and publication transitions;
- graph/vector snapshot and fingerprint comparison;
- CLI stdout/stderr/result-artifact capture;
- resource sampling for CPU, RSS, disk, threads, and latency.

## Fault Matrix

| Domain | Required scenarios |
| --- | --- |
| Input | malformed identity, control characters, invalid spans, oversized record, path escape, Unicode/CP932 |
| Parser | ERROR/MISSING recovery, crash, timeout, OOM, stale cache, conflicting duplicate, Pro*C masking edge |
| Graph | missing endpoint, duplicate ID, under-count, count/readback disagreement, timeout before/after commit, corrupt index/schema |
| Vector | partial upsert, duplicate point, payload mismatch, unavailable store, graph/vector count mismatch |
| Orchestration | child crash before result, corrupt result, lock contention, source changed, cancellation, SIGTERM/SIGKILL |
| Durability | journal corruption, expired lease, incompatible fingerprint, disk full, publication crash, rollback |
| UX | known failure, unknown defect, JSON mode, debug mode, retry exhaustion, status during active write |

## Related Files

- root `tests/` and `code-tiny/tests/`
- existing graph/storage lifecycle and parser-quality suites
- new shared fixtures/fault helpers under existing test conventions
- temporary-store integration scripts and plan-scoped reports
- CI workflow/sharding configuration if present

## Implementation Steps

1. Define certification matrix and fixture/fault APIs.
2. Port the motivating 500-file behavior into a minimized deterministic fixture.
3. Add contract/property tests for accounting, identity, references, result
   serialization, retry taxonomy, and state transitions.
4. Add real-store provider conformance with snapshot/readback assertions.
5. Add subprocess and kill-point tests for result artifacts, resume, rollback,
   and CLI rendering.
6. Add graph/vector generation consistency and incremental baseline tests.
7. Add performance/resource baselines and regression thresholds.
8. Run tests repeatedly and under randomized ordering to detect flakes; record
   seed and artifacts for every failure.
9. Publish a certification report per analyzer/provider combination.

## Todo

- [ ] Minimized incident fixture fails old behavior and passes new contracts.
- [ ] Every fault class asserts storage and publication state.
- [ ] Provider tests use real temporary stores.
- [ ] Crash matrix covers every durable transition.
- [ ] CLI output tests guarantee one concise known-failure summary.
- [ ] Flake/repeat runs and seeds are archived.
- [ ] Certification matrix marks unsupported/uncertified paths explicitly.

## Risks

- Fault tests become slow. Separate fast contract, provider integration, crash,
  and full-canary tiers with deterministic sharding.
- Embedded-store hooks may not simulate engine faults accurately. Combine hooks
  with real process termination and readback assertions.
- Fixture drift hides regressions. Version expected fingerprints and require
  reviewed updates with rationale.

## Success Criteria

- Every required failure mode has a deterministic automated reproduction.
- Tests catch silent loss, partial publication, blind retry, and raw known-error
  traceback regressions.
- Analyzer/provider certification is evidence-based and machine-readable.
- Repeated lab runs meet the agreed flake and resource thresholds.
