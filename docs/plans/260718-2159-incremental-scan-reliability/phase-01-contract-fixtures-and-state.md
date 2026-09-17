# Phase 01: Freeze Contracts, Fixtures, and State Migration

## Context

The current incremental tests prove first-run bootstrap and selected analyzer integrations but do not encode the failure modes under review. Implementation must begin with failing behavioral tests and a versioned state contract.

## Requirements

- Freeze the definition of a scan scope, repository scope, change source, and clean baseline.
- Preserve all public CLI commands and existing summary fields.
- Define state v2 and deterministic v1 migration/rollback behavior.
- Build isolated Git fixtures without network dependencies.
- Record source-ignore, sensitive-file, symlink, and case-normalization behavior explicitly.

## Architecture

Use a small state model with root metadata plus per-repository records. Keep parser manifests as derived artifacts, not the source of truth. Store only relative paths, metadata, and SHA-256 digests.

```text
SyncStateV2
  scan_scope {id, project_id, canonical_root, schema_version}
  repositories[] {repo_root, source_prefix, last_good_sha, previous_dirty_paths}
  inventory_generation {id, filter_version, path -> size, mtime_ns, sha256, repository_scope}
  migration {source_version, migrated_at, backup_path}
```

## Related Files

- `code-tiny/tools/common/incremental_sync_state.py`
- `code-tiny/tools/common/git_diff.py`
- New `code-tiny/tools/common/source_inventory.py`
- `code-tiny/tools/sync/incremental_sync.py`
- `tests/test_incremental_sync_bootstrap.py`
- New focused fixtures/tests under `tests/`

## Implementation Steps

1. Add contract tests for summary compatibility and state v1 loading.
2. Add failing tests for tracked unstaged, staged, untracked, delete, rename, revert, unchanged dirty repeat, and second edit.
3. Add failing subprocess tests for live contention, pre-existing stale lock file, and abrupt owner termination.
4. Add monorepo fixtures with two normal modules and changes both inside/outside the configured root.
5. Add initialized, dirty, committed-without-parent-update, nested, and uninitialized submodule fixtures using local repositories only.
6. Define immutable inventory generation, atomic state-pointer publication, orphan cleanup, filter-version invalidation, and size guard/benchmark policy.
7. Define state v2 serialization, validation, backup, migration, and corruption policy; v1 requires one conservative bootstrap full scan before clean v2 publication.
8. Add a fault-injection seam for source drift and analyzer failure before any implementation behavior changes.

## Todo

- [x] Behavioral matrix is executable and initially fails for every confirmed gap.
- [x] State v2 schema and v1 migration contract are reviewed.
- [x] Existing summary/CLI compatibility assertions are present.
- [x] Fixtures are deterministic on Windows and POSIX.
- [x] No test depends on live graph/vector services.

## Risks

- Git file mode, symlink, and case-only rename behavior differs by platform; tests must assert normalized public behavior, not platform-specific raw output.
- Submodule fixture setup can accidentally use network remotes; use local paths with explicit test-only file protocol configuration.
- A large manifest embedded directly in the main state JSON can make atomic updates expensive; benchmark fixture scale before freezing storage layout.

## Success Criteria

- Tests precisely distinguish lock-busy, no-change, partial-coverage, dirty failure, and success states.
- A state v1 file migrates deterministically, retains its parent baseline, and cannot silently seed a clean inventory.
- The implementation phases can be completed without changing the agreed public contract.
