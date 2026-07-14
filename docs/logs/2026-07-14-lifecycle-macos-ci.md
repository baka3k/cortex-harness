# Lifecycle Commands macOS CI Gate — 2026-07-14

## Context

The global `dev` command had reached one-to-one parity with the root Make lifecycle interface, but that contract was only covered by platform-neutral and mocked tests. A regression in the POSIX wrapper, AppleScript command construction, or architecture-specific setup could therefore reach macOS users without a native CI signal. Commit `90f7fc5c901baa95a3a42841b587f4d71d3a42d5` adds a focused compatibility gate for the lifecycle work that originated in the Make MCP lifecycle plan (`plans/make-mcp-lifecycle/plan.md:1`) and was completed by the global parity change (`docs/logs/2026-07-14-dev-make-lifecycle-parity.md:1`).

## Change

- Added a path-filtered GitHub Actions workflow for lifecycle-related source, configuration, and tests, with manual dispatch available for explicit checks (`.github/workflows/lifecycle-macos.yml:4`, `.github/workflows/lifecycle-macos.yml:24`).
- Added native matrix coverage for Intel `x86_64` on Python 3.10 and Apple Silicon `arm64` on Python 3.12. Each job confirms the runner architecture, presence of `/usr/bin/osascript`, Make, and the installed `dev` executable before running the lifecycle suites (`.github/workflows/lifecycle-macos.yml:35`, `.github/workflows/lifecycle-macos.yml:51`, `.github/workflows/lifecycle-macos.yml:56`, `.github/workflows/lifecycle-macos.yml:66`).
- Added a smoke test that changes to a temporary directory and invokes `dev help`, proving the editable package exposes a location-independent global command on both macOS architectures (`.github/workflows/lifecycle-macos.yml:62`). The POSIX install test now also verifies that `~/.local/bin/dev` is executable and can render its command surface when launched outside the repository (`tests/test_make_lifecycle.py:58`, `tests/test_make_lifecycle.py:68`).
- Reused the lifecycle contract suites to cover Make-to-Python dispatch, Make/`dev` command parity, matching action dispatch, failure-code propagation, launcher generation, PID records, and macOS AppleScript construction (`tests/test_make_lifecycle.py:19`, `tests/test_make_lifecycle.py:33`, `tests/test_make_lifecycle.py:82`, `tests/test_make_lifecycle.py:114`, `tests/test_dev_lifecycle_commands.py:52`, `tests/test_dev_lifecycle_commands.py:70`, `tests/test_dev_lifecycle_commands.py:76`, `tests/test_dev_lifecycle_commands.py:94`). Documented the two-architecture CI guarantee for users (`ReadMe.md:81`).

## Impact

Risk level: **low**. This commit changes CI, tests, and documentation rather than runtime lifecycle behavior. Pull requests and pushes to `develop` that touch the lifecycle surface now receive native Intel and Apple Silicon signals for the executable global wrapper and POSIX lifecycle/unit contracts. The gate reduces the chance of silently breaking `dev`/Make parity, cross-directory invocation, macOS launcher construction, or start/stop state handling.

The workflow is intentionally headless: the start test replaces terminal execution while checking generated launchers and PID state, and the macOS test validates the `osascript` command structure (`tests/test_make_lifecycle.py:82`, `tests/test_make_lifecycle.py:114`). It does **not** launch and observe a real Terminal GUI session in GitHub Actions. End-to-end confirmation that Terminal opens visible windows and hosts both MCP processes remains a manual test on an interactive Mac.

## Decision

Use two native macOS runner variants instead of treating one macOS architecture as representative of both. The matrix makes architecture identity an explicit assertion and exercises the supported Python range while keeping the job lightweight (`.github/workflows/lifecycle-macos.yml:31`, `.github/workflows/lifecycle-macos.yml:37`). Reuse the existing POSIX unit contracts and add an installed-wrapper smoke test rather than duplicating lifecycle behavior in CI-only scripts.

A real Terminal GUI launch was not made a CI requirement because GitHub-hosted jobs are headless and GUI automation would be brittle and unable to prove the same interactive behavior users observe. The chosen boundary verifies prerequisites, command construction, dispatch, and state management in CI, while preserving interactive launch as a manual acceptance check.

## References

- Lifecycle plan: `plans/make-mcp-lifecycle/plan.md:1`
- macOS workflow: `.github/workflows/lifecycle-macos.yml:1`
- POSIX lifecycle tests: `tests/test_make_lifecycle.py:19`
- Global CLI contract tests: `tests/test_dev_lifecycle_commands.py:10`
- User-facing compatibility note: `ReadMe.md:81`
- Prior parity decision: `docs/logs/2026-07-14-dev-make-lifecycle-parity.md:1`
- Commit: `90f7fc5c901baa95a3a42841b587f4d71d3a42d5`
