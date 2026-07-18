# Phase 02: Replace the Run Lock and Stabilize Scan Scope

## Context

On Windows the lock is currently an exclusive-created file. Process termination can leave that file behind permanently. The cache base also depends on the caller's current working directory, allowing the same source root to obtain different locks and baselines.

## Requirements

- Kernel-backed ownership on Windows and POSIX.
- Automatic release on process termination.
- Stable scope identity independent of command working directory and path casing.
- Bounded wait with actionable owner diagnostics.
- No broad change to unrelated analyzer cache behavior.

## Architecture

Introduce a focused `ProjectRunLock`/`ScanScope` helper using a direct `portalocker` dependency. Open a persistent metadata file, acquire the OS-backed lock non-blockingly or with a bounded timeout, then overwrite/flush diagnostic metadata only after ownership succeeds. Use `normcase(realpath(root))` for identity on Windows.

## Related Files

- `code-tiny/tools/sync/incremental_sync.py`
- New helper under `code-tiny/tools/common/`
- `pyproject.toml`, `code-tiny/requirements.txt`, `uv.lock`
- `code-tiny/tools/common/analyzer_cache.py` only if a non-breaking path helper is required
- `cortex_harness/dev.py`
- New lock/scope tests

## Implementation Steps

1. Extract scope resolution before summary/lock/state path construction.
2. Anchor the default sync-control cache to the source root and retain explicit cache overrides.
3. Declare `portalocker` directly and wrap it behind one project-owned context manager so callers do not depend on library-specific exceptions.
4. Make pre-existing unlocked files acquirable and overwrite stale metadata after acquisition.
5. Add `--lock-timeout-seconds` and summary fields for wait duration, owner metadata, and lock path.
6. Prevent the outer generic CLI retry loop from retrying lock-busy/usage exit code `2`; preserve retries for transient analyzer failures where currently intended.
7. Add a legacy cache-state discovery/migration warning when state is found only under the old working-directory-derived path.
8. Verify unlock in normal return, exception, child failure, Ctrl+C, and forced-process termination tests.
9. Detect/report remote cache locations where possible and document that filesystem locks are not distributed consensus.

## Todo

- [x] Stale lock files are harmless on Windows and POSIX.
- [x] Live contention still excludes a second writer.
- [x] Same root from different CWDs resolves one scope.
- [x] Lock metadata is diagnostic and never authoritative.
- [x] Generic retry behavior does not hide or delay lock-busy failures.

## Risks

- Byte-range locking semantics can vary on network filesystems; document support and add an explicit diagnostic if lock acquisition behaves unexpectedly.
- PID reuse makes PID-only stale detection unsafe; OS ownership remains authoritative.
- Changing the global `safe_cache_root` default would affect every analyzer; keep the change scoped to incremental orchestration.

## Success Criteria

- A force-killed owner can be followed immediately by a successful acquisition.
- Two concurrent processes for the same scan scope cannot both enter analyzer execution.
- Different source roots sharing a project ID remain independent.
- State and lock paths no longer depend on invocation CWD.
