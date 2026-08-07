# Phase 06: Security, performance, canary, and rollout

## Context

Recovery must earn promotion through measured semantic improvement, not simply a
lower warning count. The 100-file corpus is the canary for correctness, security,
resource use, incremental behavior, and operator experience.

## Requirements

- Run the complete test matrix on temporary roots and stores.
- Benchmark default report mode and bounded repair mode separately.
- Verify containment, privacy, crash isolation, and compile-flag filtering.
- Compare symbol/type/call correctness against reviewed expected facts.
- Roll out `repair` behind an opt-in flag until all gates pass.
- Preserve `report` mode and existing recovered parsing as rollback paths.

## Architecture

Produce a versioned canary report containing corpus manifest, parser/context
versions, quality transitions, semantic-yield deltas, graph deltas, p50/p95
latency, RSS, timeouts, queue stops, and security-test outcomes. Promotion changes
configuration defaults only; it does not remove safety budgets or rollback.

## Related files

- all new parser-quality and recovery tests
- existing C/C++, sync, graph runtime, and CLI reliability tests
- benchmark/report tooling under the existing test convention
- operator documentation for sync diagnostics and repair policy

## Implementation steps

1. Run unit, integration, incremental, graph publication, and CLI suites.
2. Execute adversarial fixtures: oversized/deep input, malformed compile JSON,
   unsafe flags, external symlinks, worker hang/crash/OOM, control characters,
   lossy decoding, and report truncation.
3. Benchmark clean-only, mixed 100-file, and scaled synthetic cohorts in cold and
   warm cache states.
4. Require default report mode overhead <=10% on the representative corpus.
5. Require at least 30% of attempted pilot retries to materially improve the
   accepted quality/yield tuple before promoting broader repair use.
6. Review graph cardinality and expected symbol/call facts; warning reduction
   alone is not acceptance.
7. Roll out in stages: report default, repair opt-in, limited canary projects,
   then evidence-based default review.
8. Document rollback, artifact retention, policy tuning, and how to inspect each
   quality tier without exposing source.

## Todo

- [ ] Pass full functional and adversarial test matrix.
- [ ] Publish cold/warm latency and RSS baselines.
- [ ] Review semantic-yield and graph-correctness deltas.
- [ ] Verify report privacy, retention, and truncation behavior.
- [ ] Complete opt-in canary and rollback rehearsal.
- [ ] Record promotion decision and final tuned budgets.

## Risks

A high retry improvement rate can still hide graph regressions or unacceptable
tail latency. Promotion requires correctness, resource, security, and operational
gates together.

## Success criteria

All acceptance gates in `plan.md` pass; no adversarial file escapes isolation;
report mode stays within overhead budget; repaired results improve reviewed
semantic facts; rollout and rollback are documented and rehearsed.
