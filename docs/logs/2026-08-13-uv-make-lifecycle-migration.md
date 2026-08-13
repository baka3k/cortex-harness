# UV Make Lifecycle Migration — 2026-08-13

## Context

The Make lifecycle still created the project virtual environment and installed its root and component dependencies through `python -m venv` and `python -m pip`. The lifecycle bootstrap needed one cross-platform `uv` path for both `make build` and the build phase of `make install` (`Makefile:19`, `scripts/mcp-lifecycle.py:207`, `scripts/mcp-lifecycle.ps1:338`).

## Change

- Exported an overridable `UV` launcher from Make, then changed the Python and PowerShell lifecycle backends to require that launcher, create `.venv` with `uv venv`, and install all available requirements plus the editable root in one `uv pip install --python` invocation (`Makefile:10`, `scripts/mcp-lifecycle.py:158`, `scripts/mcp-lifecycle.py:178`, `scripts/mcp-lifecycle.ps1:156`, `scripts/mcp-lifecycle.ps1:177`).
- Deferred the optional `psutil`-dependent process imports so build and help can bootstrap before runtime dependencies are installed (`scripts/mcp-lifecycle.py:63`).
- Aligned the macOS lifecycle workflow and operator guidance with the `uv` prerequisite, override, and command syntax, and added coverage for virtual-environment creation, missing/custom launchers, and the Windows backend (`.github/workflows/lifecycle-macos.yml:53`, `ReadMe.md:53`, `ReadMe.md:64`, `tests/test_make_lifecycle.py:154`).

## Impact

Risk level: **medium**. `make build` and `make install` now require `uv` on macOS, Linux, and Windows, while existing `.venv` directories remain reusable. Dependency installation no longer upgrades or invokes environment-local pip directly; failures now stop with an explicit missing-`uv` or non-zero-exit error. The main compatibility risk is launcher discovery and cross-platform argument handling, covered by the lifecycle tests and the pinned macOS CI setup (`scripts/mcp-lifecycle.py:158`, `scripts/mcp-lifecycle.ps1:165`, `.github/workflows/lifecycle-macos.yml:53`, `tests/test_make_lifecycle.py:178`).

## Decision

Use `uv` as the single environment and package installer behind the existing Make interface, with `UV` as an escape hatch for non-standard executable locations. Keep the requirements files and editable root package as the dependency inputs, rather than introducing a separate lock/sync contract in this lifecycle change; this limits the migration to installer orchestration while preserving current dependency sources (`scripts/mcp-lifecycle.py:169`, `scripts/mcp-lifecycle.py:192`).

## References

- Make launcher contract: `Makefile:10`
- POSIX lifecycle build: `scripts/mcp-lifecycle.py:178`
- Windows lifecycle build: `scripts/mcp-lifecycle.ps1:177`
- Lifecycle regression coverage: `tests/test_make_lifecycle.py:154`
- macOS lifecycle workflow: `.github/workflows/lifecycle-macos.yml:53`
- Installation guidance: `ReadMe.md:43`
