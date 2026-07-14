# Global `dev` and Make Lifecycle Parity — 2026-07-14

## Context

The earlier `dev start` addition proved that repository lifecycle actions could be invoked globally, but the CLI still exposed only part of the root Make interface. That left users switching between `dev` and `make` and created a maintenance risk: new or existing Make lifecycle targets could lack a matching global command without detection (`docs/logs/2026-07-14-global-dev-start.md:1`, `Makefile:8`). Commit `4c24b50df35172779a375e52c72bb442ee871ae1` completes the one-to-one command surface and makes parity an enforced invariant.

## Change

- Added thin Click commands for `help`, `build`, `install`, `uninstall`, `infra-down`, and `stop`, completing the existing `infra-up`, `doctor`, and `start` mappings. Every command delegates its identically named action through the shared repository-aware lifecycle dispatcher (`cortex_harness/dev.py:1373`, `cortex_harness/dev.py:1404`, `cortex_harness/dev.py:1455`).
- Added a parity test that derives the authoritative lifecycle target set from the root Makefile's `.PHONY` declaration and fails if any target is absent from the `dev` CLI (`Makefile:8`, `tests/test_dev_lifecycle_commands.py:70`). A companion dispatch test verifies that all nine commands forward the exact matching action, preventing aliases from silently diverging (`tests/test_dev_lifecycle_commands.py:76`).
- Updated Windows and POSIX lifecycle help to present `make <action>` and `dev <action>` as equivalent forms, and documented the complete globally callable command set (`scripts/mcp-lifecycle.ps1:57`, `scripts/mcp-lifecycle.py:61`, `ReadMe.md:65`).

## Impact

Risk level: **medium**. Users now have one consistent lifecycle surface from any working directory for environment setup, CLI installation, infrastructure, MCP processes, diagnostics, and help. Reusing the established dispatcher keeps Windows, macOS, and Linux behavior aligned with Make and avoids duplicate lifecycle logic. Operationally, commands such as `dev uninstall`, `dev infra-down`, and `dev stop` intentionally change local state, so their broader availability carries the same cautions as the corresponding Make targets. The Makefile-derived parity test lowers future drift risk by turning a missing global command into a test failure.

## Decision

Treat the root Makefile lifecycle targets as the compatibility contract and keep each global command as a minimal adapter to `_run_lifecycle`. This preserves a single implementation path while making `dev` a complete, location-independent front end. A manually maintained command list alone was rejected because it could drift unnoticed; deriving the expected set from `.PHONY` makes omissions mechanically detectable. Exact per-action dispatch assertions were retained alongside the set check because surface parity does not by itself prove semantic parity.

## References

- Lifecycle contract: `Makefile:8`
- Global dispatcher and command adapters: `cortex_harness/dev.py:1373`, `cortex_harness/dev.py:1404`
- Parity and dispatch regression tests: `tests/test_dev_lifecycle_commands.py:70`, `tests/test_dev_lifecycle_commands.py:76`
- Cross-platform lifecycle help: `scripts/mcp-lifecycle.ps1:57`, `scripts/mcp-lifecycle.py:61`
- User documentation: `ReadMe.md:65`
- Prior `dev start` context: `docs/logs/2026-07-14-global-dev-start.md:1`
- Commit: `4c24b50df35172779a375e52c72bb442ee871ae1`
