# Phase 05: Integrate CLI, Summaries, Migration, and Documentation

## Context

Correct detection is insufficient if `lock_busy`, `no_changes`, partial submodule coverage, and failed dirty state are indistinguishable to operators. The root CLI also currently retries all non-zero exits and requires Git for code sync.

## Requirements

- Preserve command compatibility and existing parser/overlay execution.
- Make every early exit and coverage gap explicit.
- Migrate legacy state without silently changing baselines.
- Align documentation with executable behavior.
- Provide safe troubleshooting for lock/state inspection without instructing blind deletion.

## Architecture

Keep one orchestrator summary as the machine-readable contract and render a concise root-CLI result from it. Use additive fields and stable reason codes rather than parsing free-form logs.

## Related Files

- `cortex_harness/dev.py`
- `code-tiny/tools/sync/incremental_sync.py`
- `code-tiny/tools/common/incremental_sync_state.py`
- `docs/specs/sync-code.md`
- `docs/HARNESS_WORKFLOW.md`
- `code-tiny/README.md`
- CLI/summary contract tests

## Implementation Steps

1. Pass change-detection, lock-timeout, reconciliation, submodule, strict, and stable cache-scope settings through `dev sync code` and `all`.
2. Replace the hard Git-required skip with Git-or-hash capability selection.
3. Define stable statuses/reasons: `success`, `no_changes`, `lock_busy`, `partial_coverage`, `source_changed_during_scan`, and `failed` while retaining compatibility for consumers expecting zero/non-zero outcomes.
4. Add per-source counts for committed, staged, unstaged, untracked, hash, revert, delete, rename, and submodule evidence.
5. Report repository coverage, lock wait/owner metadata, state schema/migration, and reconciliation work without exposing credentials or source contents.
6. Implement one-time state backup and migration reporting. Do not auto-select between conflicting legacy states.
7. Document normal edit-before-commit behavior, non-Git mode, module-root behavior, submodule limitations, strict mode, and safe lock troubleshooting.
8. Correct outdated claims about mtime fallback and clarify that `dev sync code all` means all configured folders/analyzers, not necessarily forced full scan unless `--full-scan` is supplied.
9. Report/deduplicate overlapping selected roots before subprocess launch so a parent and child do not ingest the same files twice.

## Todo

- [x] Existing CLI invocations remain valid.
- [x] Summary reason codes are covered by tests.
- [x] Lock/no-change/partial/failure output is distinguishable.
- [x] Legacy migration is visible and recoverable.
- [x] Documentation matches tests and `--help` output.

## Risks

- Changing the meaning of exit code `2` globally can affect unrelated CLI commands; scope non-retryable handling to sync invocation or configurable retry policy.
- Summary consumers may assume `status == success`; add new reasons carefully and retain a compatible top-level success outcome where appropriate.
- Printing Git commands or environment values can leak credentials; summaries contain paths/hashes/counts only.

## Success Criteria

- Operators can identify why no parser ran without opening source code.
- Non-Git projects are no longer skipped merely for lacking `.git`.
- State migration leaves a backup and reports its origin.
- Help text, specifications, workflow guide, and README describe the same behavior.
