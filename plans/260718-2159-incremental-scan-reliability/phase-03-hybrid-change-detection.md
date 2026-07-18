# Phase 03: Implement Hybrid Git and SHA-256 Change Detection

## Context

Commit-to-commit diff misses the developer working tree. A pure full-tree hash fixes correctness but wastes I/O and loses Git rename/history information. The desired detector combines Git evidence with bounded content verification.

## Requirements

- Detect committed, staged, unstaged, untracked, deleted, renamed, reverted, and repeatedly edited dirty files.
- Avoid rescanning an unchanged dirty worktree on every invocation.
- Support non-Git roots without forcing `git init`.
- Preserve deleted-file cleanup and graph impact expansion.
- Detect source drift during analyzer execution.

## Architecture

Build a normalized `ChangeSet` with provenance from a versioned `ProjectInventory`:

```text
ChangeSet
  changed[path] -> {sources[], before_hash?, after_hash?}
  deleted[path] -> {sources[], before_hash?}
  renamed[]     -> {old_path, new_path, sources[]}
  warnings[]
```

Git collection uses the saved commit baseline plus index/worktree/untracked evidence. The previous successful dirty-path set is always reconsidered so reverting a file to `HEAD` is detected. The last published SHA-256 inventory is the content source of truth; Git narrows candidate work between reconciliations.

## Related Files

- `code-tiny/tools/common/git_diff.py`
- `code-tiny/tools/common/incremental_sync_state.py`
- New focused content-manifest/change-detector helper
- `code-tiny/tools/sync/incremental_sync.py`
- `cortex_harness/dev.py`
- New working-tree and hash fallback tests

## Implementation Steps

1. Extend Git status collection to cover baseline-to-HEAD, index, worktree, and untracked files with one NUL-delimited normalized status parser.
2. Union current dirty paths with the prior successful dirty-path set to detect revert-to-HEAD transitions.
3. Implement streaming SHA-256 with containment, sensitivity, size/mtime prefilter, and stat-before/stat-after stability checks.
4. Merge Git and hash evidence without dropping rename/delete semantics or path provenance.
5. Add hash-only mode for non-Git roots and stop requiring/auto-initializing Git merely to run incremental sync; retain an explicit opt-in initialization path if desired.
6. Write an immutable inventory generation only after analyzers succeed, then atomically publish its state pointer; tolerate and clean orphan generations.
7. Re-hash selected files after execution; if any changed during the run, leave state dirty and report `source_changed_during_scan`.
8. Add `--change-detection hybrid|committed|hash` and `--reconcile` plumbing; keep hybrid as default and committed-only as the temporary legacy compatibility mode.
9. Measure hash calls and assert that normal Git incremental runs hash candidates/prior-dirty paths rather than the entire tree unless reconciliation is requested.
10. Keep legacy analyzer `commit-sha-after=HEAD` semantics for dirty snapshots and add `snapshot_id`/`worktree_dirty` to orchestration state/summary instead of inventing a synthetic Git SHA.

## Todo

- [x] Every working-tree state has a passing test.
- [x] Unchanged dirty files do not repeatedly invoke analyzers.
- [x] Reverted and re-edited files are detected.
- [x] Non-Git roots support incremental hash mode.
- [x] Mid-run changes cannot advance a clean baseline.

## Risks

- Relying only on size/mtime can miss same-size writes with restored timestamps; SHA-256 is the final identity check.
- Hash suppression must not suppress a path rename with identical content.
- Working-tree changes during analysis create TOCTOU risk; post-run verification is mandatory.
- Full content manifests can grow large; keep candidate hashing bounded and benchmark the chosen state representation.

## Success Criteria

- The second scan after an uncommitted edit scans the file; an immediate third scan with no further edit does not.
- New untracked source files and deleted/renamed paths reach the correct analyzer manifests.
- A non-Git fixture runs full once and content-incremental thereafter.
- Git remains the fast primary source and SHA-256 provides correctness without MD5.
