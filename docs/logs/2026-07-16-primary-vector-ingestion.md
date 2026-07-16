# Primary Vector Ingestion Completion — 2026-07-16

## Context

The [primary vector ingestion plan](./plans/260716-1615-primary-vector-ingestion-completion/plan.md) identified five primary analyzers whose CLI exposed Qdrant options but whose execution stopped after graph persistence: Rust, Go, Swift, Perl, and Dart (`plans/260716-1615-primary-vector-ingestion-completion/plan.md:15`, `plans/260716-1615-primary-vector-ingestion-completion/plan.md:31`). The required contract was deterministic, project/parser/root-scoped vector identity and cleanup, with graph-only execution remaining valid when Qdrant is not configured (`plans/260716-1615-primary-vector-ingestion-completion/plan.md:64`).

## Change

- Added a shared primary-analyzer adapter that validates Qdrant targets, redacts credentials, bounds embedding text, derives root-scoped deterministic point IDs, lazily loads vector dependencies, retries writes, batches upserts, and deletes stale points only inside the current project/parser/root scope (`code-tiny/tools/common/primary_vector_sync.py:45`, `code-tiny/tools/common/primary_vector_sync.py:63`, `code-tiny/tools/common/primary_vector_sync.py:75`, `code-tiny/tools/common/primary_vector_sync.py:255`, `code-tiny/tools/common/primary_vector_sync.py:301`).
- Connected Rust, Go, Swift, Perl, and primary Dart to that adapter after graph persistence, while preserving non-zero vector failure results and keeping Flutter overlay mode graph-only (`code-tiny/tools/go/go_analyzer.py:1233`, `code-tiny/tools/go/go_analyzer.py:1371`, `code-tiny/tools/swift/swift_analyzer.py:1167`, `code-tiny/tools/perl/perl_analyzer.py:457`, `code-tiny/tools/flutter/flutter_analyzer.py:121`).
- Propagated embedding settings through root and incremental orchestration. The five new analyzer commands explicitly accept the Android-style vector flags, and the default model is `jinaai/jina-embeddings-v3` (`code-tiny/tools/sync/incremental_sync.py:862`, `code-tiny/tools/sync/incremental_sync.py:900`, `cortex_harness/dev.py:1844`, `tests/test_primary_analyzer_vector_contract.py:150`, `tests/test_primary_analyzer_vector_contract.py:175`). Primary registry entries write vectors; framework overlays remain graph-only and publish their primary seed collections (`code-tiny/tools/sync/incremental_sync.py:53`, `code-tiny/tools/sync/incremental_sync.py:1501`).

## Impact

Risk level: **medium**. Five primary language paths now perform real Qdrant persistence, so failures in embedding, endpoint access, or scoped cleanup can fail ingestion instead of silently producing graph-only state. Risk is bounded by deterministic scope-aware IDs, upsert-before-delete ordering, configured-only activation, fatal configured-write errors, and coverage for secret redaction, delete-only incremental runs, cross-scope isolation, full-scan selection, and direct CLI compatibility (`tests/test_primary_vector_sync.py:92`, `tests/test_primary_vector_sync.py:118`, `tests/test_primary_vector_sync.py:178`, `tests/test_primary_vector_sync.py:236`, `tests/test_primary_analyzer_vector_contract.py:116`). Automated suites and a temporary live-Qdrant smoke verified idempotence, incremental deletion, and preservation of another project/root scope (`plans/260716-1615-primary-vector-ingestion-completion/reports/validation-report.md:25`, `plans/260716-1615-primary-vector-ingestion-completion/reports/validation-report.md:51`).

## Decision

Use one parser-independent vector transport with analyzer-local model mapping instead of duplicating Qdrant lifecycle code or refactoring mature analyzer writers. Keep framework analyzers graph-only and retrieve their facts through primary-language vector seeds plus bounded graph expansion, with framework-filtered graph search as the bounded fallback for unanchored Struts XML facts; create a framework collection only after a named retrieval case proves both paths insufficient (`plans/260716-1615-primary-vector-ingestion-completion/plan.md:50`, `plans/260716-1615-primary-vector-ingestion-completion/plan.md:59`, `plans/260716-1615-primary-vector-ingestion-completion/reports/validation-report.md:35`).

## References

- Plan: [Primary Analyzer Vector Ingestion Completion](./plans/260716-1615-primary-vector-ingestion-completion/plan.md)
- Validation: `plans/260716-1615-primary-vector-ingestion-completion/reports/validation-report.md:5`
- Commit: `d1b8899cf664fb41c82d7c66c148f7a606a43f86`
