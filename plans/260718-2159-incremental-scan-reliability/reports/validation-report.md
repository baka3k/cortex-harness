# Incremental Scan Reliability Validation

Date: 2026-07-18

Platform: Windows host, Ubuntu WSL POSIX smoke
Review verdict: GO, 9.6/10, 0 critical/high/blocking-medium findings

## Result

The default scan path now uses an OS-backed scope lock, schema-v2 state, Git candidate collection, and a SHA-256 source inventory. A full scan followed by an unstaged edit scans once; an immediate unchanged repeat returns `status: success`, `outcome: no_changes` without invoking an analyzer.

Module-relative Git paths, initialized/dirty/independently committed submodules, non-Git roots, unborn repositories, unavailable submodule coverage, shared cache isolation, failure retry, source drift, and legacy-state migration have deterministic tests.

## Commands and evidence

Windows focused gate:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_incremental_sync*.py' -q
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_git_change_detection.py -q
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_source_inventory.py -q
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_dev_sync_reliability.py -q
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_dev_init_graph_provider.py -q
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_common_analyzer_registry.py -q
```

Result: 50 focused/affected tests passed. `py_compile` and `git diff --check` passed.

POSIX lock smoke used Ubuntu WSL with the workspace portalocker package on `PYTHONPATH`:

```bash
python3 -m unittest discover -s tests -p test_incremental_sync_lock.py -v
```

Result: 4/4 passed, including forced owner termination followed by immediate reacquisition. The subprocess fixture emits a non-fatal `ResourceWarning` for test-owned pipes.

## Performance

Local Windows temporary-filesystem benchmark:

| Files | Full SHA-256 | No change | One candidate |
| ---: | ---: | ---: | ---: |
| 1,000 | 9.8285 s | 0.1852 s | 0.1725 s |
| 10,000 | 92.9587 s | 0.8895 s | 0.9779 s |

Normal hybrid runs remain candidate-proportional and below one second at 10k files on this host. Full reconciliation is intentionally explicit because antivirus/filesystem overhead makes it I/O-heavy.

## Migration and rollback

- A legacy unscoped state is probed, its v1 SHA is retained, and a `.v1.bak` is written before conservative bootstrap.
- Inventory generation is published before the scoped state pointer.
- Missing/corrupt inventory forces a visible conservative bootstrap.
- Rollback can restore the retained v1 backup; the new implementation never overwrites that backup.

## Repository-wide suite note

The complete repository discovery run executed 210 tests and reported 24 failures plus 20 errors in existing ASP.NET, COBOL, Dart, Flutter, Perl, and framework fixture/runtime suites. Those failures are environment/fixture dependent and occur outside the incremental scan change surface. Focused analyzer registry, graph setup, CLI compatibility, and incremental orchestration gates pass.

## Remaining non-blocking hardening

- Add a dedicated multi-level nested-submodule end-to-end fixture (recursive discovery is implemented; current acceptance covers first-level dirty and independently committed repositories).
- Add a dedicated strict-mode partial-topology integration fixture.
- Bound recursive topology depth in addition to the existing visited-realpath cycle guard.
- Validate lock behavior on network filesystems separately; portalocker is advisory and not distributed consensus.
