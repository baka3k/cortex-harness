---
type: research
date: 2026-07-18
---

# Repository Findings: Incremental Scan Reliability

## Summary

Direct source inspection confirms that the active `dev sync code` path delegates to `code-tiny/tools/sync/incremental_sync.py`. The current flow has separate correctness gaps in run locking, working-tree detection, cache/scope identity, nested module paths, and submodule coverage. No code changes were made during research.

## Active Call Path

```text
cortex_harness/dev.py::sync_code / sync_code_all
  -> _run_with_retry(... incremental_sync.py ...)
  -> incremental_sync.py::_run_incremental
  -> _ProjectRunLock
  -> load_sync_state
  -> collect_git_diff_entries(before_sha, HEAD)
  -> group changed/deleted paths by parser/framework
  -> analyzer subprocesses
  -> mark_clean(last_good_sha=HEAD)
```

The older `_git_status_since()` helper in `cortex_harness/dev.py` already uses `--relative`, but it is not used by the active code-sync orchestration.

## Findings

| Claim | Source | Confidence |
| --- | --- | --- |
| Windows lock ownership is inferred from exclusive file creation; forced process death can leave a permanent false lock | `code-tiny/tools/sync/incremental_sync.py:340-390` | Confirmed |
| Lock busy returns exit code 2, while the parent retries every non-zero exit uniformly | `incremental_sync.py:1181-1190`, `cortex_harness/dev.py:1013-1036` | Confirmed |
| Default lock/state/summary/manifest base depends on `os.getcwd()`, so the same root can have different control state | `code-tiny/tools/common/analyzer_cache.py:25-37` | Confirmed |
| Cache identity does not apply `normcase`, allowing Windows path-casing aliases | `analyzer_cache.py:16-22` | Confirmed |
| Incremental detection only compares saved commit SHA to `HEAD`; staged, unstaged, and untracked files are absent | `incremental_sync.py:1249-1264`, `git_diff.py:53-70` | Confirmed |
| Empty commit diff marks state clean even when the worktree may be dirty | `incremental_sync.py:1279-1297` | Confirmed |
| A nested configured module receives Git top-level-relative paths because active diff lacks `--relative` and pathspec | `git_diff.py:53-63`, `incremental_sync.py:307-328` | Confirmed |
| Full scan and incremental scan use different path contracts for nested roots | `incremental_sync.py:1082-1098` versus `git_diff.py:53-63` | Confirmed |
| Submodule scanning only handles first-level initialized submodules whose parent gitlink changed | `git_diff.py:74-150` | Confirmed |
| Submodule diff failures are silently skipped | `git_diff.py:96-99` | Confirmed |
| No focused tests exist for run lock, working-tree states, nested module roots, submodules, CWD scope, or source TOCTOU | exact search under `tests/` | Confirmed |

## Design Alternatives

| Alternative | Assessment |
| --- | --- |
| Commit-only Git diff | Retain only as temporary compatibility mode; does not meet the developer workflow |
| Git status without content inventory | Detects dirty files but rescans an unchanged dirty worktree forever and cannot prove what content succeeded |
| Full SHA-256 of all sources every run | Correct but potentially expensive on large/network trees; appropriate for reconciliation/hash-only mode |
| `size + mtime_ns` only | Fast but not a correctness identity; misses preserved timestamps and same-size rewrites |
| Hybrid Git + versioned SHA-256 inventory | Recommended: Git narrows work and preserves rename/delete evidence; inventory represents the last successful content |
| PID-based stale-lock takeover | Rejected: PID reuse and contender races do not prove ownership |
| Direct `portalocker` dependency | Recommended: OS-backed release on crash with one tested platform abstraction |
| Parent gitlink-only submodule baseline | Rejected: misses dirty, independently committed, and nested submodules |
| Per-repository baselines within one scan scope | Recommended despite added state/report complexity |

## Required Test Matrix

- Lock: live contention, timeout, normal exit, forced kill, stale metadata file, same root/different CWD, Windows case alias, different roots.
- Git: committed/staged/unstaged/untracked add-modify-delete-rename, staged+unstaged same file, delete/recreate, ignored policy, special-character paths with NUL-delimited parsing.
- Dirty repeat: first edit scans, unchanged second run skips, next edit scans, later matching commit does not duplicate work.
- Module roots: top-level and nested roots, sibling exclusion, rename into/out of scope, Windows separators/case.
- Submodules: gitlink update, dirty/staged/untracked, independent HEAD advance, nested initialized, uninitialized, missing commit, conflict, explicit ignore, overlapping configured root.
- Inventory: same-size/restored-mtime rewrite, corrupt/missing generation, orphan generation, v1 migration, filter-version change.
- TOCTOU: modify/create/delete while analyzer is blocked; state remains dirty and the clean inventory is not published.
- Failure: primary/overlay failure, partial success, lock busy, no-change with prior dirty state, idempotent retry.

## Cross-Plan Relationships

The work is not blocked by the Neo4j-to-FalkorDB migration because it occurs before provider persistence. It has shared-file coordination with the active Flutter, Perl, and ASP.NET analyzer plans through `incremental_sync.py`, `cortex_harness/dev.py`, sync documentation, and routing tests. The reliability plan must preserve additive parser/overlay registry edits.

## Gaps

- Actual network-filesystem locking semantics depend on the deployment filesystem and require environment-specific validation.
- Absolute performance budgets require representative repository sizes and storage types; the plan records complexity/count metrics and benchmark evidence rather than inventing a universal latency threshold.
- Pre/post content validation provides strong eventual consistency but not immutable snapshot isolation; true snapshot isolation would require a copied tree/worktree and is out of scope.

## Coverage

- Repository source and Git history: used.
- Semantic code index: used for candidate discovery, then verified against direct source.
- Project document index: returned no useful passages; direct specifications were inspected.
- Focused tests: inspected; identified missing cases above.

