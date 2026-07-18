---
title: "Incremental Code Scan Reliability Upgrade"
status: pending
created: 2026-07-18
mode: hi-plan --full
scope: cross-platform run locking, hybrid Git/content change detection, monorepo module roots, recursive Git submodules, state migration, observability, and regression coverage
relatedPlans: [260714-1603-flutter-analyzer-parser, 260715-1629-perl-analyzer-parser, 260715-2011-aspnet-roslyn-analyzers, 260716-1615-primary-vector-ingestion-completion]
---

# Incremental Code Scan Reliability Upgrade

## Overview

Upgrade `dev sync code` so a successful full scan is followed by reliable incremental scans across Windows, Linux, normal monorepo modules, Git submodules, and non-Git source roots. The design keeps Git as the fast source of commit/rename/delete evidence and adds SHA-256-backed working-tree state for staged, unstaged, untracked, reverted, and reconciliation cases.

The target flow is:

```text
dev sync code
  -> resolve stable ScanScope(project_id + canonical source root)
  -> acquire OS-backed cross-platform run lock
  -> load/migrate sync state
  -> discover repository topology and source-root boundaries
  -> collect committed + staged + unstaged + untracked + prior-dirty changes
  -> supplement with SHA-256 reconciliation when required
  -> route one normalized changed/deleted set to primary parsers and overlays
  -> verify selected sources did not change during the run
  -> atomically advance repository baselines and content fingerprints
  -> release the OS lock and write an actionable summary
```

## Verified Findings

| Finding | Evidence | Required response |
| --- | --- | --- |
| Incremental detection compares only `last_good_sha` to `HEAD` | `incremental_sync.py::_run_incremental`, `git_diff.py::collect_git_diff_entries` | Include working-tree and untracked state; do not mark clean merely because commit SHAs match |
| Windows uses file existence as lock ownership | `_ProjectRunLock.acquire()` uses `O_CREAT | O_EXCL` when `fcntl` is unavailable | Replace with kernel-released byte-range/advisory locking; lock-file contents become diagnostics only |
| Lock/state cache base defaults to `os.getcwd()/.cache` | `analyzer_cache.py::safe_cache_root` | Anchor sync control state to canonical source root unless an explicit cache directory is supplied |
| A configured module root inside a larger Git repo receives repository-root-relative paths | `git diff` is called without `--relative` or a module pathspec | Resolve Git top-level and constrain/relativize every diff to the configured source root |
| Submodule collection is shallow and gitlink-dependent | `git_diff.py::_collect_submodule_diff_entries` | Discover initialized/uninitialized repositories recursively and maintain a baseline per repository scope |
| Submodule errors are silently skipped | `CalledProcessError` is converted to `continue` | Report partial coverage; fail in existing strict mode |
| Existing tests cover bootstrap and analyzer routing, not lock/worktree/submodule behavior | focused search under `tests/` | Add isolated Git fixtures and subprocess lock tests before implementation |
| Code-sync docs claim timestamp fallback that is not implemented | `docs/specs/sync-code.md`, `docs/HARNESS_WORKFLOW.md` | Update behavior and documentation from one executable contract |

## Scope Challenge Decisions

| Question | Decision | Rationale |
| --- | --- | --- |
| Preserve existing CLI/state compatibility or redesign the command? | Preserve public commands and existing summary fields; add versioned state and additive options/fields | Operators and active analyzer plans already depend on the current invocation contract |
| Is consistency limited to committed Git history? | No. Default hybrid mode includes committed, staged, unstaged, untracked, reverted, and deleted inputs | The normal developer workflow edits code before committing and expects immediate sync |
| Should submodules be flattened into the parent baseline? | No. Treat each initialized Git repository as an independent repository scope under one scan scope | Parent gitlinks cannot represent dirty or independently committed submodule worktrees |

## Architecture Decisions

### Stable scan scope

- Define scope identity from `project_id` plus `normcase(realpath(source_root))`.
- Default lock, state, manifests, and summaries to `<source_root>/.cache/...`; preserve an explicit `--cache-dir`/`QDRANT_CACHE_DIR` override.
- Include the canonical root digest in every control-artifact path, even when the explicit cache directory is shared.
- Add a one-time migration probe for the legacy current-working-directory cache path; never merge two divergent legacy states silently.

### Cross-platform run lock

- Add `portalocker` as a direct runtime dependency and use its OS-backed lock abstraction on Windows and POSIX instead of maintaining separate platform branches.
- Keep PID, process start time, scope ID, root, and acquisition time as diagnostics; file existence is never proof that a process owns the lock.
- Let the OS release ownership after normal exit, exception, terminal close, or forced process termination.
- Add a bounded `--lock-timeout-seconds` option (default 10 seconds, `0` for fail-fast) and make lock-busy exit code non-retryable by the outer generic retry loop.
- Treat network/shared-cache locking as filesystem-dependent, not distributed consensus; warn when the resolved cache path is remote where it can be detected.

### Hybrid change detector

- Git supplies commit history, renames, deletions, index/worktree status, ignore rules, and repository boundaries.
- SHA-256 supplies content identity for prior-dirty paths, repeated dirty scans, revert-to-HEAD detection, non-Git roots, and explicit/periodic reconciliation.
- Use `size + mtime_ns` only as a fast prefilter. A changed prefilter must be confirmed with a streaming SHA-256 before skipping or recording content.
- Identity is `(repository_scope, relative_path, content_sha256)`; matching content at a different path is still a rename/add-delete event.
- Store digests and relative paths only; never persist source contents or secrets in state.
- Parse Git name/status output in NUL-delimited form so spaces, tabs, Unicode, quoting, and renames are lossless.

### Versioned source inventory

- Make a successful inventory generation the content baseline; Git is the candidate accelerator, not the sole source of truth.
- Write a new immutable inventory generation first, then atomically update state to point at it. A crash can leave an orphan generation but cannot point state at a missing snapshot.
- Version the source-filter contract. Changes to supported extensions, exclusions, sensitivity rules, or parser ownership force reconciliation rather than silently preserving an incomplete baseline.
- For state v1, retain the legacy `last_good_sha` for diagnostics/rollback but require one conservative full scan before publishing the first clean v2 inventory. Merely hashing current files must not legitimize data that an older scan may have skipped.

### Repository topology

- Resolve the Git top-level separately from the configured source root.
- Constrain Git commands to the source-root pathspec and emit paths relative to that root.
- Recursively enumerate initialized and uninitialized submodules with visited-realpath protection and bounded depth.
- Maintain `last_good_sha` and working-tree fingerprints per repository scope.
- On migration from state v1, preserve the parent baseline and conservatively bootstrap each discovered submodule once.
- Detect overlapping configured source roots before execution. When a selected parent already owns a nested root under the same project, deduplicate the nested invocation and report `covered_by_parent` instead of ingesting it twice.

### Crash and race consistency

- State advances only after every selected analyzer/overlay succeeds.
- Preserve the current dirty-state retry behavior and old `last_good_sha` on failure.
- A prior dirty state can never become clean through an empty Git diff alone; it must replay/verify the pending inventory transition successfully.
- Hash/stat selected files before and after analyzer execution. If a source changes during the run, do not mark the state clean; return an actionable dirty result for retry.
- State writes remain atomic (`temp + replace`) and receive a schema version. Corrupt or ambiguous migration state fails visibly rather than resetting the baseline.

## Public Contract

Keep these commands valid:

```text
dev sync code
dev sync code all
dev sync code --full-scan
```

Add narrowly scoped controls:

```text
--change-detection hybrid|committed|hash  # default: hybrid; committed preserves legacy commit-only behavior
--lock-timeout-seconds N                  # default: 10; 0 requests fail-fast behavior
--reconcile                          # force full content-manifest reconciliation
--submodules recursive|ignore        # default: recursive; ignore is explicit opt-out
```

Existing `--strict` also makes uninitialized/unreadable submodules and partial topology discovery fatal. Existing summary keys (`status`, `before_sha`, `after_sha`, `diff`, `parsers`, `state_before`, `state_after`) remain; new fields are additive under `scope`, `lock`, `change_sources`, `repositories`, `reconciliation`, and `coverage_warnings`.

## Phases

1. [Phase 01 - Freeze contracts, fixtures, and state migration](phase-01-contract-fixtures-and-state.md)
2. [Phase 02 - Replace the run lock and stabilize scan scope](phase-02-cross-platform-lock-and-scope.md)
3. [Phase 03 - Implement hybrid Git and SHA-256 change detection](phase-03-hybrid-change-detection.md)
4. [Phase 04 - Support monorepo modules and recursive submodules](phase-04-module-and-submodule-topology.md)
5. [Phase 05 - Integrate CLI, summaries, migration, and documentation](phase-05-cli-observability-and-docs.md)
6. [Phase 06 - Stress, regression, and rollout validation](phase-06-validation-and-rollout.md)

## Research and Reviews

- [Repository findings](research/repository-findings.md)
- [Red-team review](reports/red-team.md)
- [Plan validation](reports/plan-validation.md)

## Dependencies and Cross-Plan Coordination

- This plan is not blocked by `neo4j-to-falkordb-migration`; it changes pre-analyzer orchestration and preserves provider-neutral analyzer commands.
- `260714-1603-flutter-analyzer-parser`, `260715-1629-perl-analyzer-parser`, and `260715-2011-aspnet-roslyn-analyzers` overlap `incremental_sync.py` and `cortex_harness/dev.py`. Keep registry/detector edits additive and rebase the reliability changes around their final analyzer registrations.
- `260716-1615-primary-vector-ingestion-completion` is a completed contract reference: configured persistence failures must remain fatal and must not advance the clean baseline.
- No graph schema, analyzer ownership, parser semantics, MCP routing, or vector collection naming change is required.

## Expected Files

| Area | Files |
| --- | --- |
| Lock/scope | `code-tiny/tools/sync/incremental_sync.py`, a focused helper under `code-tiny/tools/common/`, direct dependency files (`pyproject.toml`, `code-tiny/requirements.txt`, `uv.lock`), `code-tiny/tools/common/analyzer_cache.py` only if a backward-compatible helper is needed |
| Change detection | `code-tiny/tools/common/git_diff.py`, new `source_inventory.py` and focused topology helpers, `code-tiny/tools/common/incremental_sync_state.py` |
| Orchestration | `code-tiny/tools/sync/incremental_sync.py`, `cortex_harness/dev.py` |
| Tests | new lock, working-tree, module-root, submodule, state migration, and end-to-end sync tests under `tests/` |
| Documentation | `docs/specs/sync-code.md`, `docs/HARNESS_WORKFLOW.md`, `code-tiny/README.md`, troubleshooting guidance |
| Validation artifacts | `plans/260718-2159-incremental-scan-reliability/reports/` |

## Success Criteria

- After a full scan, editing a tracked file without committing causes exactly that file and its graph-impacted dependents to scan.
- Staged, unstaged, untracked, deleted, renamed, reverted-to-HEAD, and newly re-edited dirty files are detected without repeatedly rescanning an unchanged dirty tree.
- A killed Windows sync cannot leave a false permanent lock; a live concurrent sync remains excluded.
- Invoking the same root from different working directories resolves the same state and lock scope.
- A configured module directory inside a larger monorepo emits only in-scope, module-relative paths.
- Initialized first-level and nested submodules scan committed and working-tree changes even when the parent gitlink is unchanged.
- Uninitialized/unreadable submodules are visible in summaries and fatal under `--strict`.
- Parent+nested configured roots are not ingested twice in one project run.
- State v1 upgrades without losing the parent `last_good_sha`; rollback to the previous release remains possible after retaining a backup during the migration window.
- No analyzer, overlay, provider, vector, or deletion-cleanup regression occurs.
- Focused tests, relevant repository tests, Windows/POSIX lock subprocess tests, and a recorded end-to-end fixture matrix pass.

## Out of Scope

- Parallel execution of analyzers within one scan scope.
- Graph transaction rollback across multiple analyzers.
- Replacing Git as the primary history/rename engine.
- Content-addressing with MD5 or storing source text in sync state.
- Changing `.gitignore`, sensitive-file, symlink, or parser ownership policy beyond making current behavior explicit.
- Automatic initialization, cloning, or updating of missing submodules during scan.
