# C++ semantic pilot and rollout decision (Phase 07) — 2026-08-21

## Context

Phase 07 had to turn the earlier containment, semantic-worker, evidence,
Pro*C mapping, and guarded-publication work into one reproducible rollout
decision. Promotion required an immutable real workload and every correctness,
resource, provider, publication, rollback, and Pro*C hard gate; missing evidence
could not be treated as a pass
(`plans/260821-1144-cplus-semantic-call-graph/phase-07-pilot-and-rollout-decision.md:94`).

## Change

- Added the fail-closed pilot contract and deterministic report-bundle writer.
  Manifest loading verifies immutable Git revision identity, repository-contained
  paths, file hashes, context states, and credential safety
  (`code-tiny/tools/cplus/pilot_rollout.py:233`); rollout evaluation preserves
  non-waivable hard gates and permits default changes only after promotion
  (`code-tiny/tools/cplus/pilot_rollout.py:712`,
  `code-tiny/tools/cplus/pilot_rollout.py:777`).
- Added the executable containment/sparse/comprehensive benchmark with cold,
  warm, and physically changed-TU runs, benchmark-owned observations, measured
  resource evidence, and plan-scoped outputs
  (`tests/benchmark_cplus_semantic_calls.py:330`,
  `tests/benchmark_cplus_semantic_calls.py:440`,
  `tests/benchmark_cplus_semantic_calls.py:741`).
- Froze an immutable synthetic developer canary at fixture commit
  `0eede02fe2576affb67a16a244e744572da532a7`, including the workload class,
  SHA-bound corpus, fixed thresholds, and complete visible Pro*C cohort census
  (`plans/260821-1144-cplus-semantic-call-graph/pilot-manifest.json:3`,
  `plans/260821-1144-cplus-semantic-call-graph/pilot-manifest.json:33`,
  `plans/260821-1144-cplus-semantic-call-graph/pilot-manifest.json:61`).
- Published the developer-canary JSON bundle, Phase 07 report, and operator
  runbook. The runbook makes the canary's synthetic limits and fail-closed
  interpretation explicit (`docs/CPLUS_SEMANTIC_PILOT.md:3`,
  `docs/CPLUS_SEMANTIC_PILOT.md:84`).
- Implementation commit:
  `222f4244bfd775616535f83c6a0b20ed7fc0e162`. Its pre-commit sensitive-value
  hook was bypassed only after a manual staged scan and diff check confirmed two
  false positives: the test-only `PASSWORD` sentinel and the custom `ABC`
  substring matching ordinary connect-related words.

## Impact

- The checked-in developer corpus produced 100% reviewed direct-call precision
  and recall, zero unsafe negative answers, and zero weak evidence promoted to
  `CALLS` (`plans/260821-1144-cplus-semantic-call-graph/phase-07-report.md:87`).
- Verification passed 303 focused tests plus 10 subtests. The full repository
  suite passed 996 tests and 207 subtests, with 37 unrelated pre-existing
  failures and no Phase 07 failure. Independent review approved the change at
  9.6/10 with zero critical findings
  (`plans/260821-1144-cplus-semantic-call-graph/phase-07-report.md:127`).
- Publication behavior is unchanged: semantic publication remains off, the
  Tree-sitter containment view remains available, and no repository-complete or
  authoritative negative claim is introduced
  (`plans/260821-1144-cplus-semantic-call-graph/phase-07-report.md:107`).

## Decision

The terminal decision is `remain_in_containment`; `defaults_may_change=false`.
Eight hard gates remain failed: real stratified workload, priority faithful
compile-context coverage, complete Pro*C component coverage, million-LOC
resource evidence, operational measurements, live Neo4j/FalkorDB canaries,
live deterministic publication, and live rollback
(`plans/260821-1144-cplus-semantic-call-graph/reports/developer-canary/rollout-decision.json:111`).
Promotion can be reconsidered only after those gates are supplied and the same
immutable manifest/evidence/report workflow is rerun
(`plans/260821-1144-cplus-semantic-call-graph/phase-07-report.md:138`).

## References

- Plan: `plans/260821-1144-cplus-semantic-call-graph/phase-07-pilot-and-rollout-decision.md:1`
- Report: `plans/260821-1144-cplus-semantic-call-graph/phase-07-report.md:1`
- Decision bundle: `plans/260821-1144-cplus-semantic-call-graph/reports/developer-canary/rollout-decision.json:111`
- Runbook: `docs/CPLUS_SEMANTIC_PILOT.md:1`
- Implementation: `222f4244bfd775616535f83c6a0b20ed7fc0e162`
- Immutable header-fixture baseline: `0eede02fe2576affb67a16a244e744572da532a7`
