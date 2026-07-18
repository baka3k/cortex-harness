# Phase 04: Support Monorepo Modules and Recursive Submodules

## Context

The configured source root may be a repository root, a normal module directory inside a monorepo, or a tree containing one or more nested Git submodules. These topologies cannot share one unqualified `HEAD` baseline and repository-root-relative path stream.

## Requirements

- Emit paths relative to the configured source root.
- Exclude changes outside a configured module root.
- Scan initialized submodule changes even when the parent gitlink is unchanged.
- Recurse nested submodules safely.
- Surface uninitialized/unreadable/ambiguous repository coverage.

## Architecture

Discover a repository topology before collecting changes:

```text
ScanScope(root)
  RepositoryScope(parent repo, source_prefix=".")
  RepositoryScope(submodule A, source_prefix="vendor/a")
  RepositoryScope(nested B, source_prefix="vendor/a/libs/b")
```

Each repository scope has its own canonical root, relative prefix, current `HEAD`, last-good commit, dirty paths, and coverage status. Repository-local paths are prefixed once when merged into the scan-root `ChangeSet`.

## Related Files

- `code-tiny/tools/common/git_diff.py`
- New repository-topology helper under `code-tiny/tools/common/`
- `code-tiny/tools/common/incremental_sync_state.py`
- `code-tiny/tools/sync/incremental_sync.py`
- Framework/module detector regression tests
- New monorepo/submodule fixtures

## Implementation Steps

1. Resolve `git rev-parse --show-toplevel` and source-root prefix for every configured root.
2. Run commit/status commands from the Git top-level with an explicit source-root pathspec and relative output.
3. Reject or normalize any returned path that escapes the configured source root.
4. Replace hand parsing of only the root `.gitmodules` with recursive topology discovery using Git plumbing/status output.
5. Track initialized, uninitialized, missing-commit, and unreadable submodules separately; use visited canonical roots and a depth bound.
6. Maintain/migrate a baseline per repository scope. Bootstrap newly discovered submodules conservatively without resetting the parent baseline.
7. Merge repository-local changes into scan-root paths exactly once and preserve delete/rename provenance.
8. Route the global normalized set through existing primary and framework classifiers unchanged.
9. Add tests for normal modules, sibling isolation, dirty submodules, submodule commits without parent gitlink commits, parent gitlink updates, nested submodules, and submodule removal.
10. Detect selected parent/child source-root overlap and deduplicate the child as `covered_by_parent`; preserve child-only scans when the parent is not selected.
11. Add sparse checkout, Git LFS pointer, conflicted submodule, missing historical commit, rename into/out of module scope, and special-character path diagnostics/tests.

## Todo

- [ ] Nested monorepo roots produce correct relative manifests.
- [ ] Out-of-module changes are excluded.
- [ ] Dirty and independently committed submodules scan without a parent commit.
- [ ] Nested submodules are covered.
- [ ] Partial coverage is explicit and strict-mode fatal.
- [ ] Overlapping configured roots cannot double-ingest the same tree.

## Risks

- Prefixing a path twice recreates the current module-root bug; enforce one path-domain type per boundary and test containment.
- Historical parent gitlinks may reference submodule commits not available locally. Fall back to the saved repository baseline or conservative submodule bootstrap with a warning.
- Symlinked repositories can alias the same real path; visited-realpath protection prevents cycles and duplicate scans.
- Automatically cloning/updating submodules would mutate user repositories and is explicitly out of scope.

## Success Criteria

- Every changed path passed to an analyzer exists under the configured root unless it is an intentional deletion tombstone.
- Changes in sibling modules do not trigger a module-scoped scan.
- All initialized recursive submodules have an explicit coverage/baseline record.
- No submodule failure is silently converted to an empty change set.
