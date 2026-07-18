# Phase 06: Stress, Regression, and Rollout Validation

## Context

Locking, filesystem state, Git topology, and analyzer side effects create platform and crash boundaries that unit tests alone cannot validate. Rollout needs deterministic CI coverage plus recorded Windows/POSIX smoke evidence.

## Requirements

- Run the complete topology/change/lock matrix.
- Prove no analyzer/overlay/vector/provider regressions.
- Exercise crash recovery and mid-run source drift.
- Measure normal incremental overhead and reconciliation cost.
- Define rollback and state downgrade handling.

## Architecture

Use three validation layers:

1. Pure unit tests for parsing, path normalization, state, hashing, and merge rules.
2. Local subprocess Git fixtures for real locks, commits, worktrees, modules, and recursive submodules.
3. One end-to-end `dev sync code` smoke with analyzers stubbed for deterministic CI, plus optional live graph/vector validation using existing environment gates.

## Related Files

- All new focused tests from Phases 01-05
- Existing incremental, analyzer registry, framework overlay, vector contract, and graph setup tests
- `plans/260718-2159-incremental-scan-reliability/reports/validation-report.md`
- Relevant CI workflow files if platform matrix coverage is missing

## Implementation Steps

1. Run focused state, change detector, lock, module, submodule, CLI, and summary suites.
2. Run the existing incremental bootstrap, framework overlay, analyzer registry, primary vector, graph setup, and parser-specific incremental tests.
3. On Windows and POSIX, start two contenders, force-kill the owner, and prove immediate reacquisition plus state integrity.
4. Inject file changes during analyzer execution and prove the baseline remains dirty/old.
5. Exercise state v1 migration with mandatory bootstrap, explicit-cache migration, conflicting legacy states, corrupt/missing inventory generations, orphan generation recovery, filter-version changes, and rollback from the backup.
6. Benchmark no-change, one-file dirty, 1k/10k-file candidate, and full reconciliation fixtures; record Git calls, files hashed, and elapsed time.
7. Run module/submodule acceptance with parent gitlink unchanged and nested dirty worktrees.
8. Run special-character/NUL-delimited Git path cases, overlapping configured roots, sparse checkout, and partial submodule coverage.
9. Write the validation report with exact commands, platform details, pass counts, performance results, exclusions, and remaining limitations.
10. Roll out behind the default `hybrid` behavior only after the old commit-only behavior remains available as `--change-detection committed` for one compatibility window.

## Todo

- [x] Focused and affected regression suites pass.
- [x] Windows and POSIX crash-recovery evidence is recorded.
- [x] Mid-run drift cannot mark clean.
- [x] Performance budgets are met or explicitly tuned.
- [x] Rollback/migration procedure is verified.
- [x] Validation report is complete.

## Risks

- CI may not provide Windows and POSIX runners; local evidence cannot replace the missing platform gate, so completion remains conditional until both run.
- Full repository tests have known environment-dependent parser failures; separate pre-existing exclusions from regressions with recorded baselines.
- Hash reconciliation can be I/O-heavy on network volumes; report files hashed and expose an explicit reconciliation control.

## Success Criteria

- All plan-level success criteria have reproducible evidence.
- Normal one-file incremental scans remain proportional to the candidate set.
- A failed or killed run cannot lose pending changes or permanently block future scans.
- The rollout can be reverted without discarding the retained state-v1 backup.
