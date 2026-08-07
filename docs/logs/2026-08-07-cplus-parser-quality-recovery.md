# C/C++ parser-quality recovery phases 01-04 — 2026-08-07

## Context

The C/C++ analyzer's non-fatal parser warning mixed file-level failures with
Tree-sitter `ERROR` nodes and did not expose `MISSING` damage, semantic yield, or
recovery provenance. The [parser-quality recovery plan](../../plans/260807-1329-parser-quality-recovery/plan.md)
therefore separates diagnostics, trust classification, cache identity, and
bounded repair before any graph-publication policy is changed
(`plans/260807-1329-parser-quality-recovery/plan.md:25`,
`plans/260807-1329-parser-quality-recovery/plan.md:224`).

## Change

- Added a versioned provider-neutral quality contract with one-pass structural
  damage collection, deterministic tier classification, strict whole-file
  candidate scoring, and bounded private artifact writes
  (`code-tiny/tools/common/parse_quality.py:19`,
  `code-tiny/tools/common/parse_quality.py:214`,
  `code-tiny/tools/common/parse_quality.py:269`,
  `code-tiny/tools/common/parse_quality.py:394`).
- Attached compact backend/context/quality provenance and an explicit strong-edge
  eligibility policy to C/C++ payloads, while exposing `off|report|repair`, capped
  report controls, and compile-database bootstrap suppression in the analyzer CLI
  (`code-tiny/tools/cplus/cplus_analyzer.py:2832`,
  `code-tiny/tools/cplus/cplus_analyzer.py:2852`,
  `code-tiny/tools/cplus/cplus_analyzer.py:5446`,
  `code-tiny/tools/cplus/cplus_analyzer.py:5534`).
- Routed parse-quality policy and budgets through incremental sync and `dev sync
  code`, creating collision-resistant run-scoped artifact manifests and returning
  their aggregate results to the root summary
  (`code-tiny/tools/sync/incremental_sync.py:1058`,
  `code-tiny/tools/sync/incremental_sync.py:1924`,
  `code-tiny/tools/sync/incremental_sync.py:1948`,
  `cortex_harness/dev.py:2177`).
- Added a persistent terminal-outcome queue, bounded recovery budgets, validated
  compile-database ingestion, strict flag/path filtering, isolated clang workers,
  capped request/output transport, process-tree RSS enforcement, and
  strict-improvement selection that retains the Tree-sitter first pass on
  timeout, crash, or non-improvement
  (`code-tiny/tools/cplus/parse_recovery.py:82`,
  `code-tiny/tools/cplus/parse_recovery.py:204`,
  `code-tiny/tools/cplus/parse_recovery.py:268`,
  `code-tiny/tools/cplus/parse_recovery.py:359`,
  `code-tiny/tools/cplus/parse_recovery.py:594`).
- Added a deterministic 100-file corpus and baseline plus focused contract,
  cache, compile-command, worker-isolation, symlink, timeout, crash, dry-run, and
  bounded-repair tests (`tests/fixtures/cplus_parse_quality/baseline-darwin-python-3.12.json:3`,
  `tests/test_parse_quality_contract.py:53`,
  `tests/test_cplus_parse_recovery.py:94`,
  `tests/test_cplus_clang_worker.py:16`,
  `tests/test_incremental_sync_parse_quality.py:91`).

## Impact

**Risk level: medium.** Normal C/C++ ingestion now emits actionable, reconciled
quality telemetry by default, while expensive native recovery remains opt-in and
bounded by file, wall-time, worker, timeout, and circuit-breaker limits. Cache
identity includes parse context, unchanged terminal failures are not repeatedly
retried, and invalid candidates cannot replace a better recovered first pass.
Terminal queue identity includes the candidate backend/version, worker protocol,
and recovery policy, so tool upgrades reopen only the affected outcomes.
The main residual risk is rollout behavior on the representative production
corpus and the still-unapplied graph publication gate.

Phases 01-04 are implemented and marked complete in their phase checklists
(`plans/260807-1329-parser-quality-recovery/phase-01-contract-and-baseline.md:46`,
`plans/260807-1329-parser-quality-recovery/phase-04-bounded-recovery.md:59`).
Phases 05-06 remain dependency-blocked: guarded publication must wait for the
Pro*C output, hardened graph-write contract, and staged concurrency lifecycle,
and rollout validation follows only after that integration
(`plans/260807-1329-parser-quality-recovery/plan.md:200`,
`plans/260807-1329-parser-quality-recovery/plan.md:224`).

## Decision

Recovery preserves the recovered Tree-sitter payload as the baseline and replaces
it only when a provider-neutral structural/semantic tuple is strictly better;
per-node AST merging was intentionally deferred. In `report` and `repair` modes,
compile databases are treated as bounded data, never as executable build
instructions, and native clang work runs in disposable workers. The plan remains `in_progress` instead of overstating
completion because publication enforcement, full security/performance canary,
and rollout belong to the dependency-gated Phases 05-06
(`plans/260807-1329-parser-quality-recovery/plan.md:125`,
`plans/260807-1329-parser-quality-recovery/plan.md:153`,
`plans/260807-1329-parser-quality-recovery/plan.md:276`).

## References

- Plan: [C/C++ parser quality diagnostics and bounded recovery](../../plans/260807-1329-parser-quality-recovery/plan.md)
- Implementation commit: `6836cdb59d742cbafe99bc4934642a922a4ca4de`
- Recovery hardening commit: `8eeceecf4414db03da0ab1265201736b536ccd78`
