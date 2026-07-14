# COBOL macOS Portability Hardening — 2026-07-14

## Context

The COBOL analyzer portability phase requires a verified Darwin grammar path, not merely a loader that happens to work on one development machine (`plans/260714-1702-cobol-analyzer-parser/phase-05-dialects-portability-and-hardening.md:11`, `plans/260714-1702-cobol-analyzer-parser/phase-05-dialects-portability-and-hardening.md:90`). The bundled grammar is a Darwin binary, so commit `04172c6dfa734e5403c5442d91d93e09edf9e2a0` hardens runtime selection and adds repeatable macOS validation for both Intel and Apple Silicon.

## Change

- Added a GitHub Actions matrix for `macos-15-intel` with Python 3.10 and `macos-15` Apple Silicon with Python 3.12. Each job verifies the runner architecture, performs the COBOL runtime preflight, and executes the complete COBOL test suite (`.github/workflows/cobol-macos.yml:27`, `.github/workflows/cobol-macos.yml:51`, `.github/workflows/cobol-macos.yml:53`, `.github/workflows/cobol-macos.yml:55`).
- Changed automatic runtime selection so an incompatible bundled native grammar can fall back to `tree-sitter-language-pack`, while an explicitly requested CLI or environment override still fails closed and reports its original runtime error (`code-tiny/tools/cobol/parser_runtime.py:122`, `code-tiny/tools/cobol/parser_runtime.py:125`, `code-tiny/tools/cobol/parser_runtime.py:129`, `code-tiny/tools/cobol/parser_runtime.py:149`).
- Added regression coverage for portable fallback, explicit-override failure semantics, and the universal Mach-O header containing both `x86_64` and `arm64` CPU types (`tests/test_cobol_parser_runtime.py:31`, `tests/test_cobol_parser_runtime.py:41`, `tests/test_cobol_parser_runtime.py:50`, `tests/test_cobol_parser_runtime.py:59`). The runtime documentation now records the fallback contract and dual-architecture macOS CI coverage (`code-tiny/tools/cobol/README.md:14`, `code-tiny/tools/cobol/README.md:16`).

## Impact

Risk level: **medium**. macOS users on Intel and Apple Silicon now have an explicit compatibility gate across two supported Python versions. A stale or ABI-incompatible bundled artifact no longer prevents normal startup when the portable language pack is available, reducing machine-specific failures. The fail-closed rule for operator-supplied native libraries preserves auditability and prevents configuration errors from being silently masked. The remaining risk is that CI validates the committed fixture suite rather than every future macOS or Tree-sitter release; dependency ranges and preflight checks remain the compatibility boundary.

## Decision

Use the portable `tree-sitter-language-pack` as the resilience path while retaining the universal Darwin artifact for compatibility and explicit native loading. Automatic bundled discovery may degrade safely to the package runtime, but explicit CLI/environment overrides remain authoritative and must surface failures. Validate both macOS architectures in CI rather than inferring support solely from the Mach-O header, matching the plan's evidence-based portability requirement (`plans/260714-1702-cobol-analyzer-parser/phase-05-dialects-portability-and-hardening.md:21`, `plans/260714-1702-cobol-analyzer-parser/phase-05-dialects-portability-and-hardening.md:73`).

## References

- Plan: `plans/260714-1702-cobol-analyzer-parser/plan.md:94`
- Portability phase: `plans/260714-1702-cobol-analyzer-parser/phase-05-dialects-portability-and-hardening.md:7`
- Runtime documentation: `code-tiny/tools/cobol/README.md:5`
- Commit: `04172c6dfa734e5403c5442d91d93e09edf9e2a0`
