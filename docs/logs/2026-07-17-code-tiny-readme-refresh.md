# Code-Tiny README Refresh — 2026-07-17

## Context

The `code-tiny` README still emphasized older Neo4j-oriented analyzer examples after primary vector ingestion added real Qdrant writes and standardized `jinaai/jina-embeddings-v3` propagation (`plans/260716-1615-primary-vector-ingestion-completion/plan.md:15`, `plans/260716-1615-primary-vector-ingestion-completion/plan.md:64`). This was a direct README maintenance task to make current orchestration, persistence choices, and runnable analyzer commands discoverable.

## Change

Rewrote the README around the current primary-analyzer and framework-overlay inventory, documented FalkorDB as the default/recommended graph provider, and retained Neo4j as an explicit compatibility path (`code-tiny/README.md:13`, `code-tiny/README.md:44`, `code-tiny/README.md:71`, `code-tiny/README.md:324`). Added the preferred `dev sync code` workflow, the direct incremental orchestrator command, and portable graph/vector argument contracts (`code-tiny/README.md:96`, `code-tiny/README.md:109`, `code-tiny/README.md:130`). Added full FalkorDB + Qdrant examples and a sample catalog for every registered primary analyzer using `jinaai/jina-embeddings-v3`, plus graph-only framework examples and MCP/CUDA operational notes (`code-tiny/README.md:156`, `code-tiny/README.md:204`, `code-tiny/README.md:297`, `code-tiny/README.md:407`, `code-tiny/README.md:458`).

## Impact

Risk level: **low**. This commit changes documentation only. Operators now have one current reference for FalkorDB-first graph persistence, optional Qdrant ingestion, Jina embedding configuration, analyzer-specific collection naming, framework overlay behavior, and Neo4j compatibility. The remaining risk is command drift as analyzer CLIs evolve; the README reduces that risk by centralizing shared arguments and recommending orchestration for configured projects (`code-tiny/README.md:98`, `code-tiny/README.md:132`, `code-tiny/README.md:206`).

## Decision

Prefer the root incremental workflow for normal use, while keeping direct analyzer commands for diagnostics and controlled scans. Use explicit FalkorDB arguments in direct examples so they do not inherit legacy analyzer-local defaults, factor common PowerShell settings into reusable `$graph` and `$vector` arrays, and isolate Neo4j instructions in a compatibility section instead of duplicating provider-specific examples for every analyzer (`code-tiny/README.md:100`, `code-tiny/README.md:208`, `code-tiny/README.md:324`).

## References

- Plan: `plans/260716-1615-primary-vector-ingestion-completion/plan.md:1`
- README: `code-tiny/README.md:1`
- Commit: `319a0bc470635b8175eccff4a3df9a99730a59ad`
