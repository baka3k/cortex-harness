# Global `dev start` Lifecycle Command — 2026-07-14

## Context

The installed `dev` CLI already exposed repository lifecycle operations from any working directory, while starting the code-tiny and doc-tiny MCP servers still required invoking `make start` from the repository. Commit `21c74e1048e7bbea85c6f220582e9a1bf803409b` closes that usability gap by making the same start action globally available.

## Change

- Added `dev start` to the Click root command and routed it through the existing repository-aware lifecycle dispatcher (`cortex_harness/dev.py:1371`, `cortex_harness/dev.py:1408`). This preserves the existing Windows PowerShell and macOS/Linux Python execution paths and always runs from `REPO_ROOT` (`cortex_harness/dev.py:1373`, `cortex_harness/dev.py:1387`, `cortex_harness/dev.py:1394`).
- Added a regression test that invokes the command from an isolated working directory and verifies that macOS dispatches the `start` action to the repository lifecycle script (`tests/test_dev_lifecycle_commands.py:52`, `tests/test_dev_lifecycle_commands.py:57`, `tests/test_dev_lifecycle_commands.py:61`).
- Documented the globally callable command and its behavioral equivalence to the existing Make target (`ReadMe.md:65`, `ReadMe.md:73`, `Makefile:31`).

## Impact

Risk level: **low**. Users can now launch code-tiny on port 8788 and doc-tiny on port 8789 with `dev start` from any directory after installing the global CLI. The command reuses the established lifecycle implementation, so server launch behavior and platform handling remain aligned with `make start`. The main operational risk is unchanged: invoking the command opens terminal windows and starts local MCP processes, which users must later stop through the normal lifecycle command.

## Decision

Expose a thin CLI alias over `_run_lifecycle("start")` instead of duplicating server discovery or process-launch logic in the Click command. Reusing the same lifecycle action keeps `dev start` and `make start` behavior consistent across Windows, macOS, and Linux, while repository-root execution makes the command independent of the caller's current directory (`cortex_harness/dev.py:1371`, `Makefile:32`). A separate implementation was rejected because it would create two launch paths that could drift in platform behavior and process management.

## References

- CLI command: `cortex_harness/dev.py:1408`
- Cross-directory regression test: `tests/test_dev_lifecycle_commands.py:52`
- Make lifecycle target: `Makefile:31`
- User documentation: `ReadMe.md:65`
- Commit: `21c74e1048e7bbea85c6f220582e9a1bf803409b`
