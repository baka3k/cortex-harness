# Incremental Scan Reliability Upgrade — 2026-07-18

## Context

`dev sync code` could skip a second scan after an uncommitted edit because the incremental baseline compared commit SHAs without reliably representing staged, unstaged, untracked, or reverted worktree content. On Windows, lock-file existence could also outlive the owning process and falsely block later runs (`plans/260718-2159-incremental-scan-reliability/plan.md:36`, `plans/260718-2159-incremental-scan-reliability/plan.md:37`). The [incremental scan reliability plan](../../plans/260718-2159-incremental-scan-reliability/plan.md) expands the contract to monorepo module roots, initialized and unavailable submodules, non-Git roots, state migration, and observable partial coverage.

## Change

- Replaced existence-based ownership with a canonical project/root scan scope and an OS-backed `portalocker` lock. Lock metadata is diagnostic, acquisition is bounded, and a terminated owner no longer leaves a permanent logical lock (`code-tiny/tools/common/sync_scope.py:21`, `code-tiny/tools/common/sync_scope.py:32`, `code-tiny/tools/common/sync_scope.py:43`, `code-tiny/tools/sync/incremental_sync.py:1171`).
- Added schema-v2 sync state and immutable SHA-256 inventory generations. Legacy state is backed up before conservative migration, inventory writes are atomic, and a clean baseline advances only after source-set and content verification (`code-tiny/tools/common/incremental_sync_state.py:16`, `code-tiny/tools/common/incremental_sync_state.py:133`, `code-tiny/tools/common/source_inventory.py:69`, `code-tiny/tools/common/source_inventory.py:138`, `code-tiny/tools/common/source_inventory.py:191`).
- Combined Git history and worktree candidates with content fingerprints, constrained Git paths to configured module roots, and introduced repository-scope discovery plus visible coverage warnings for unavailable submodules (`code-tiny/tools/common/git_diff.py:156`, `code-tiny/tools/common/git_diff.py:196`, `code-tiny/tools/sync/incremental_sync.py:1295`, `code-tiny/tools/sync/incremental_sync.py:1511`).
- Exposed `hybrid|committed|hash` detection, lock timeout, reconciliation, and recursive/ignored submodule controls through both the incremental runner and `dev sync code`. Parent orchestration now uses invocation-unique child summaries and treats lock-busy as non-retryable (`code-tiny/tools/sync/incremental_sync.py:2000`, `code-tiny/tools/sync/incremental_sync.py:2006`, `code-tiny/tools/sync/incremental_sync.py:2017`, `cortex_harness/dev.py:1060`, `cortex_harness/dev.py:1837`).

## Impact

Impact level: **high**. Normal developer edits are now scanned without requiring a commit, while unchanged repeats return a successful `no_changes` outcome without rerunning analyzers (`tests/test_incremental_sync_worktree.py:92`). Scope locking is recoverable after forced process termination and remains exclusive for live owners (`tests/test_incremental_sync_lock.py:40`, `tests/test_incremental_sync_lock.py:57`). Module-relative diffs, dirty submodules, explicit submodule ignore behavior, failure retry, source drift, legacy migration, and shared-cache isolation have focused regression coverage (`tests/test_git_change_detection.py:33`, `tests/test_incremental_sync_submodules.py:32`, `tests/test_incremental_sync_submodules.py:65`, `tests/test_incremental_sync_state_migration.py:23`).

The recorded validation verdict is GO at 9.6/10 with no critical, high, or blocking-medium findings; 50 focused/affected tests, compilation, diff checks, and Windows/POSIX lock verification passed (`plans/260718-2159-incremental-scan-reliability/reports/validation-report.md:5`, `plans/260718-2159-incremental-scan-reliability/reports/validation-report.md:26`). Full SHA-256 reconciliation remains intentionally explicit because the measured 10,000-file full pass was I/O-heavy, while no-change and one-candidate runs remained below one second on the validation host (`plans/260718-2159-incremental-scan-reliability/reports/validation-report.md:43`).

## Decision

Keep Git as the fast source of history, rename/delete evidence, ignore rules, and repository boundaries; use SHA-256 inventory state as the correctness baseline for working-tree and non-Git content. Do not replace Git with MD5 or a full hash walk on every run. Treat each initialized submodule as an independent repository scope, preserve unavailable or explicitly ignored submodule inventory instead of deleting it, and surface partial coverage in the run summary. Retain conservative v1 migration and publish the immutable inventory generation before updating the state pointer so a crash cannot create a clean state that references missing evidence (`plans/260718-2159-incremental-scan-reliability/plan.md:70`, `code-tiny/tools/common/source_inventory.py:104`, `code-tiny/tools/sync/incremental_sync.py:1517`, `code-tiny/tools/sync/incremental_sync.py:1908`).

## References

- Plan: [Incremental Code Scan Reliability Upgrade](../../plans/260718-2159-incremental-scan-reliability/plan.md)
- Validation: [Incremental Scan Reliability Validation](../../plans/260718-2159-incremental-scan-reliability/reports/validation-report.md)
- Baseline commit: `cf3184f34fbc43756eb0355ee37949986bc9b77f`
- Follow-up: final validation hardening described above remains uncommitted in the working tree at the time of this log; no commit identifier is assigned to it here.
