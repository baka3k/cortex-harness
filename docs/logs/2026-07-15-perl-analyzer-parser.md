# Perl Analyzer Parser Implementation — 2026-07-15

## Context

The [Perl Tree-sitter analyzer plan](../../plans/260715-1629-perl-analyzer-parser/plan.md) called for a structural Perl 5 analyzer that owns `.pl`, `.pm`, and `.t`, emits deterministic normalized facts, resolves only evidence-backed project-local dependencies, and integrates with the shared graph, sync, CLI, and MCP surfaces (`plans/260715-1629-perl-analyzer-parser/plan.md:16`, `plans/260715-1629-perl-analyzer-parser/plan.md:43`). The plan remains pending because live Neo4j/FalkorDB parity depends on the separate provider migration (`plans/260715-1629-perl-analyzer-parser/plan.md:87`).

## Change

- Added the Perl analyzer package with a version-gated `tree-sitter-perl==1.2.1` runtime and ABI capability checks (`code-tiny/requirements.txt:39`, `code-tiny/tools/perl/parser_runtime.py:13`). The parser extracts packages, named subroutines, attributes/signatures, scoped variables, imports, calls, optional documentation, and bounded recovery diagnostics without executing Perl (`code-tiny/tools/perl/perl_parser.py:180`, `code-tiny/tools/perl/perl_parser.py:256`, `code-tiny/tools/perl/perl_parser.py:304`, `code-tiny/tools/perl/perl_parser.py:421`).
- Added checkout-independent semantic IDs, bounded credential redaction, deterministic serialization, root-contained scanning, symlink exclusion, resource budgets, content-addressed parse caching, and reverse-dependency incremental selection (`code-tiny/tools/perl/models.py:15`, `code-tiny/tools/perl/models.py:33`, `code-tiny/tools/perl/pipeline.py:73`, `code-tiny/tools/perl/pipeline.py:114`, `code-tiny/tools/perl/pipeline.py:159`). Resolution is deliberately conservative: only unique static project-local imports and direct or qualified subroutine targets become resolved facts; dynamic dispatch, `SUPER`, ambiguous targets, and missing targets remain explicit uncertainty (`code-tiny/tools/perl/resolver.py:84`, `code-tiny/tools/perl/resolver.py:135`).
- Mapped normalized facts to the existing `File`, `Namespace`, `Function`, and `Field` graph contract, emitting calls only for resolved endpoints through `LanguageCodeWriter` (`code-tiny/tools/perl/perl_analyzer.py:51`, `code-tiny/tools/perl/perl_analyzer.py:180`, `code-tiny/tools/perl/perl_analyzer.py:212`). Registered Perl ownership and discovery in incremental sync, the root CLI, and unified MCP (`code-tiny/tools/sync/incremental_sync.py:88`, `cortex_harness/dev.py:32`, `code-tiny/mcp/unified_mcp.py:179`). Focused tests cover grammar loading, structural extraction, determinism across checkout roots, redaction/budgets, cache recovery, reverse dependency closure, graph rows, CLI policy, ownership, and MCP routing (`tests/test_perl_parser.py:23`, `tests/test_perl_incremental.py:19`, `tests/test_perl_integration.py:27`).

## Impact

Risk level: **medium**. Perl projects can now participate in the standard deterministic analysis and incremental sync flow, with canonical graph facts and generic MCP discovery. Stable IDs, bounded source-derived output, root/path checks, symlink exclusion, parse-recovery diagnostics, and refusal to fabricate dynamic call targets reduce correctness and data-exposure risk. Residual risk is explicit: standalone `.pod` and extensionless scripts are not owned, Perl runtime dispatch remains unresolved by design, and live Neo4j/FalkorDB parity is not claimed until the provider migration gate is available (`plans/260715-1629-perl-analyzer-parser/plan.md:45`, `plans/260715-1629-perl-analyzer-parser/plan.md:110`).

## Decision

Keep Perl as a primary analyzer inside the existing analyzer architecture instead of adding a language-specific service or graph schema. Pin and validate the grammar at startup, preserve source evidence and uncertainty in normalized records, derive incremental impact from a deterministic dependency index, and reuse provider-neutral writer and MCP contracts. This maintains parity with other primary analyzers while keeping runtime-dependent Perl semantics outside the structural-analysis boundary (`plans/260715-1629-perl-analyzer-parser/plan.md:18`, `plans/260715-1629-perl-analyzer-parser/plan.md:63`).

## References

- Plan: [Perl Tree-sitter Analyzer Parser Plan](../../plans/260715-1629-perl-analyzer-parser/plan.md)
- Commit: `65bd3f88fcf3a3564554aa0a783657f71add5c44`
- Commit: `ae656a269d715039474abdba2c1340a59d7a9c04`
- Commit: `a2952eb0e18937c2e7bcf3dfd4044f7828596956`
