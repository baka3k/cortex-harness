---
title: "Primary Analyzer Vector Ingestion Completion"
status: complete
created: 2026-07-16
mode: hi-plan --fast
scope: audit and complete Qdrant ingestion for registered primary analyzers while preserving graph-only framework overlay semantics
blocks: [260714-1603-flutter-analyzer-parser, 260715-1629-perl-analyzer-parser]
relatedPlans: [260713-1638-framework-parser-integration, 260714-1702-cobol-analyzer-parser, 260715-2011-aspnet-roslyn-analyzers]
---

# Primary Analyzer Vector Ingestion Completion

## Overview

Complete the gap between analyzer CLI contracts and actual Qdrant writes. Several recently added primary analyzers accept `--qdrant-*` and embedding options, and the incremental orchestrator assigns them project-scoped collections, but their execution paths stop after graph persistence. The implementation must add real, incremental-safe vector writes without changing parsers that already work or creating unused framework collections.

The target flow is:

```text
dev sync code
  -> incremental_sync selects primary parser and collection
  -> analyzer completes parse and resolution
  -> graph persistence succeeds
  -> bounded semantic documents are embedded and upserted
  -> stale vectors for changed/deleted paths are removed
  -> scan summary reports graph and vector counts
```

## Verified Findings

| Analyzer family | Current evidence | Decision |
| --- | --- | --- |
| Rust | Qdrant CLI flags exist; `main()` only calls `_write_graph()` | Add vector mapping, cleanup, upsert, and tests |
| Go | Qdrant CLI flags exist; `main()` only calls `_write_graph()` | Add vector mapping, cleanup, upsert, and tests |
| Swift | Qdrant CLI flags exist; `main()` only calls `_write_graph()` | Add vector mapping, cleanup, upsert, and tests |
| Perl | Active plan requires optional vector output; implemented `main()` only calls `_write_graph()` | Complete the existing Perl plan's vector adapter and acceptance criteria |
| Dart primary mode | `flutter_analyzer.py` accepts Qdrant settings and already has `qdrant_payloads`, but `main()` only writes graph facts | Add primary Dart vector execution; avoid duplicate Flutter-overlay points |
| COBOL | `main()` calls `sync_qdrant()` and reports `vectors=...`; focused contract tests exist | No feature change; retain as a reference and add regression coverage only |
| Classic ASP | `.asp` is owned by the VBScript primary analyzer, which uses `vb_analyzer_base` and a real Qdrant writer | No feature change; verify routing and regression coverage |
| ASP.NET Core/Framework | Overlay CLIs accept Qdrant arguments, but the registry deliberately sets `writes_vectors=False`; C# remains the primary vector seed | Keep graph-only initially; validate semantic retrieval through C# seeds and graph expansion |
| Spring, Servlet/JSP, MyBatis, Struts, Flutter overlay | Existing framework plan deliberately avoids direct framework collections | Preserve the decision; add direct vectors only if retrieval acceptance tests prove an unanchored-fact gap |
| Existing mature primary analyzers | C/C++, Delphi, Java, Kotlin, Python, JS/TS, PHP, C#, SQL/PLSQL, Android, and VB construct or reuse real Qdrant writers | Do not refactor; run regression tests |

`docs/development-rules.md` is absent. Root instructions, existing analyzer contracts, and active plans govern this work.

## Scope and Decisions

### In scope

- A small shared vector-sync adapter for the missing primary analyzers, with parser-specific document mappers kept next to each analyzer when their normalized models differ.
- Deterministic point IDs, project/language/file payload fields, bounded embedding text, safe endpoint/collection validation, batching, retry behavior, and incremental deletion cleanup.
- Explicit propagation of embedding configuration from `cortex_harness/dev.py` through `incremental_sync.py` to analyzer subprocesses.
- Vector counts and failure behavior in analyzer and incremental-sync summaries.
- Unit, contract, incremental cleanup, CLI propagation, and optional live-Qdrant verification.
- Retrieval validation for ASP.NET and the other graph-only overlays using primary-language vector seeds plus graph expansion.

### Out of scope

- Rewriting mature analyzer-specific Qdrant implementations.
- Moving vectors from Qdrant to FalkorDB or coupling this plan to the active graph-provider migration.
- Creating separate vector collections for every framework by default.
- Changing parser ownership, graph schemas, MCP servers, or source-analysis semantics except where required to expose stable vector documents.

### Persistence contract

- Analysis remains graph/vector independent until a valid result exists.
- Missing Qdrant configuration is non-fatal; a configured Qdrant write failure is fatal and must not mark incremental state clean.
- Full scans replace only the current project/parser/root scope. Incremental scans delete stale points only for changed, impacted, or deleted files before upserting current points.
- Stable semantic IDs, not batch order or absolute checkout paths, determine point IDs.
- Payloads include at least `node_type`, `symbol_id`, `project_id`, `project_name`, `language`, `repo`, `file_path`, `name`, `qualified_name`, and bounded searchable text where available.
- Secrets and credentials are excluded or redacted before embedding and payload creation.

## Phases

1. [Phase 01 - Freeze the vector contract and coverage matrix](phase-01-contract-and-coverage.md)
2. [Phase 02 - Implement missing primary vector writers](phase-02-primary-vector-writers.md)
3. [Phase 03 - Wire orchestration and configuration](phase-03-orchestration-and-configuration.md)
4. [Phase 04 - Validate retrieval, cleanup, and regressions](phase-04-validation-and-retrieval.md)

## Dependencies

- This plan is not blocked by `neo4j-to-falkordb-migration`; Qdrant remains the vector store and graph-provider parity is tested separately.
- It blocks completion of the vector acceptance criteria in `260714-1603-flutter-analyzer-parser` and `260715-1629-perl-analyzer-parser`.
- It reuses the completed COBOL Qdrant contract as evidence, without importing COBOL-specific models into generic analyzers.
- It preserves `260713-1638-framework-parser-integration`'s graph-only overlay decision unless Phase 04 demonstrates a measured retrieval gap.
- It coordinates with `260715-2011-aspnet-roslyn-analyzers` but does not make ASP.NET a primary owner of `.cs` files.

## Expected Files

| Area | Files |
| --- | --- |
| Shared vector adapter | New focused module under `code-tiny/tools/common/` plus tests |
| Generic primary analyzers | `code-tiny/tools/{rust,go,swift}/*_analyzer.py` |
| Model-specific primary analyzers | `code-tiny/tools/perl/perl_analyzer.py`, `code-tiny/tools/flutter/flutter_analyzer.py`, their existing normalizers/models as needed |
| Orchestration | `code-tiny/tools/sync/incremental_sync.py`, `cortex_harness/dev.py` |
| Regression and contracts | New focused tests plus existing COBOL, Dart, ASP.NET, registry, sync, and Qdrant suites |
| Documentation | Analyzer READMEs and this plan's validation report after implementation |

## Success Criteria

- Rust, Go, Swift, Perl, and Dart primary runs with Qdrant configured create non-empty, project-scoped collections with deterministic point IDs.
- Re-running an unchanged full input is idempotent; incremental updates replace affected points and deletions remove stale points without touching other files/projects/parsers.
- COBOL and all mature primary analyzers retain their existing vector behavior.
- Classic ASP continues to vectorize through VBScript ownership.
- ASP.NET and other framework overlays are retrievable through primary vector seeds and bounded graph expansion; direct framework vectors are introduced only when a named acceptance query cannot reach an unanchored fact.
- Configured vector failures produce non-zero analyzer/sync results and leave incremental state dirty; absent vector configuration remains a supported graph-only run.
- Targeted tests, relevant repository regression tests, and an available local-Qdrant smoke run pass with recorded commands and counts.

## Risks

- Embedding full source bodies can increase latency and leak secrets; bound text, prefer semantic summaries/comments/signatures, and apply existing redaction rules.
- Full-replace cleanup can delete another scope if collection naming or filters drift; require project/parser/root filters and cross-scope tests.
- Adding common CLI flags directly to every command can break analyzers with different parsers; propagate shared settings through a normalized environment and add CLI options only where the analyzer owns them.
- Framework-only artifacts may be unreachable from base-language vector seeds; Phase 04 defines an evidence-based fallback rather than creating speculative collections.
