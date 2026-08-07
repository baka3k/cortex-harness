# Phase 07: Canary, SLOs, rollout, and governance

## Context

Reliability mechanisms are valuable only when required in production paths and
kept from regressing. This phase runs the real canaries, promotes enforcement in
measured waves, preserves rollback, and establishes ownership for future tools.

## Requirements

- All phase blockers and owning-plan acceptance gates are complete.
- Observe-only and required modes are measurable and reversible.
- Run the failing 500-file cohort and full 20,186-file C/Pro*C source canary.
- Compare clean reruns for determinism and idempotency.
- Roll back automatically on correctness, health, or SLO failure.
- Require reliability certification for every new analyzer/provider/mutation
  path in CI and review.
- Maintain an operator runbook and artifact retention policy.

## Architecture

Promotion waves:

1. observe-only contracts/artifacts;
2. C/C++/Pro*C validator quarantine reporting;
3. required validation on disposable/staged stores;
4. verified graph/vector effects and transactional publication;
5. required mode for C/C++/Pro*C;
6. risk-ranked migration of remaining analyzers;
7. removal of compatibility paths after two stable releases.

Automatic rollback selects the last validated generation and disables the
new enforcement flag for new runs. It never reinstates unsafe unlabeled writes,
blind retry, or partial publication as a permanent mode.

## Related Files

- feature/config profile and status/doctor rendering
- active graph/parser/concurrency plan rollout reports
- CI reliability/conformance gates
- operator docs/runbooks under `docs/`
- plan-scoped canary, benchmark, and promotion reports
- analyzer/provider certification registry

## Implementation Steps

1. Confirm all cross-plan blockers and interface versions.
2. Run observe-only comparison and review quarantine samples/yield changes.
3. Run disposable 500-file cold/warm canaries for FalkorDB and Neo4j parity.
4. Run full 20,186-file staged canary with graph/vector verification.
5. Kill/restart at selected high-risk boundaries and prove resume/rollback.
6. Repeat identical clean runs and compare fingerprints, counts, and artifacts.
7. Measure SLOs, resource budgets, retry/quarantine rates, and operator output.
8. Record GO/NO-GO decision; enable required mode only if every correctness
   gate passes.
9. Add CI/review guardrails and certification status for new tools.
10. Publish runbook for status, quarantine review, resume, rollback, artifact
    cleanup, and escalation.

## Todo

- [ ] All owning-plan blockers and acceptance gates are recorded complete.
- [ ] 500-file canary passes without unexplained loss or raw known-error traceback.
- [ ] Full source canary publishes one validated graph/vector generation.
- [ ] Two identical reruns have matching fingerprints and no duplicate effects.
- [ ] Crash/resume and rollback rehearsals pass.
- [ ] SLO/resource/quarantine thresholds pass or rollout stops.
- [ ] Remaining tools have certified or explicitly uncertified status.
- [ ] CI and review prevent new contract-bypass mutation paths.
- [ ] Operator runbook and retention policy are published.

## Risks

- Full canary reveals new parser/provider cohorts. Classify and add fixtures;
  do not weaken accounting or publication gates to force completion.
- Observe-only behavior diverges from required mode. Run both against the same
  staging fixture and compare decisions before promotion.
- Long-lived compatibility flags become permanent. Assign owner/removal version
  and expose usage metrics.

## Success Criteria

- The complete workload finishes with a verified published generation or fails
  safely while the previous generation remains active.
- Known bad records are visible as bounded quarantine, not nuisance crashes.
- Operator output, artifacts, retry behavior, and rollback match the reliability
  contract under real faults.
- New analyzers/providers cannot claim stable support without passing the
  certification matrix.
- Two stable releases complete before compatibility paths are removed.
