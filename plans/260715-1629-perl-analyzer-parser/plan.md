---
title: "Perl Tree-sitter Analyzer Parser Plan"
status: pending
created: 2026-07-15
mode: hi-plan --fast
source: /Users/hieplq1.rpm/Downloads/Analyzer_Design_Spec_Perl_Tree_Sitter.md
scope: Perl 5 primary parsing, normalized structural facts, graph ingestion, incremental sync, unified MCP
blockedBy: [neo4j-to-falkordb-migration]
relatedPlans: [260714-1603-flutter-analyzer-parser, 260715-2011-aspnet-roslyn-analyzers, 260716-1615-primary-vector-ingestion-completion, 260718-2159-incremental-scan-reliability]
---

# Perl Tree-sitter Analyzer Parser Plan

## Overview

Build a new primary Perl 5 analyzer under `code-tiny/tools/perl/`. The analyzer will use Tree-sitter as its syntax front end, emit deterministic normalized JSON, and integrate with the existing provider-neutral graph, incremental sync, root CLI, and unified MCP surfaces.

The source specification deliberately limits the analyzer to structural analysis. This plan preserves that boundary: packages, subroutines, variables, imports, dependencies, call references, comments, and POD are in scope; runtime execution, type inference, symbolic-reference evaluation, and complete Perl dispatch resolution are not.

```text
Perl files
  -> bounded deterministic scanner
  -> pinned Tree-sitter Perl grammar
  -> normalized file/package/subroutine/variable/import/reference records
  -> conservative project-local resolution + dependency index
  -> deterministic JSON preview
  -> provider-neutral LanguageCodeWriter + optional existing vector output
  -> incremental sync, owner manifest, root CLI, and unified MCP
```

## Verified Project Context

- `code-tiny/tools/perl/` does not exist.
- Primary analyzers are registered independently in `code-tiny/tools/sync/incremental_sync.py`, `code-tiny/tools/sync/owner_manifest.py`, and `cortex_harness/dev.py`; `tests/test_common_analyzer_registry.py` requires these registries to agree.
- Existing primary Tree-sitter analyzers, especially `code-tiny/tools/go/go_analyzer.py`, already use canonical file, namespace, function, field, relation, and call records plus the shared `LanguageCodeWriter`.
- `code-tiny/requirements.txt` contains the shared Tree-sitter runtime and per-language grammar dependencies, but no Perl grammar dependency is currently declared.
- `code-tiny/mcp/unified_mcp.py` routes generic language parsers to the C++ backend and discovers tool directory names for `list_parsers`; Perl must be added to the routing contract and routing instructions.
- Perl does not require a framework overlay or a language-specific MCP server for the syntax-level scope in the supplied specification.
- `docs/development-rules.md` is absent. The supplied root instructions, `code-tiny/docs/guide_tool_integrate.md`, `code-tiny/docs/Tool_Template.md`, and existing repository conventions govern this plan.
- Vector completion review on 2026-07-16 found that `perl_analyzer.py` accepts Qdrant settings but currently persists only graph rows. `260716-1615-primary-vector-ingestion-completion` owns the missing optional embedding, incremental vector cleanup, and acceptance tests required by this plan.

## Scope and Decisions

### MVP source ownership

The proposed primary extension set is `.pl`, `.pm`, and `.t`. Phase 01 must confirm this set against the selected grammar and owner-manifest behavior before registration. Inline POD in these files is supported when documentation extraction is enabled. Standalone `.pod` files and extensionless shebang scripts are excluded from automatic ownership until a repository-wide content-classification policy is explicitly approved.

### Structural output contract

The normalized result contains:

- project identity, normalized root, analyzer/grammar versions, parser capabilities, coverage, and counters;
- file records with parse status and source evidence;
- package, subroutine, and variable symbol records with stable IDs and lexical/package scope;
- `use`, `require`, and `no` import/dependency records;
- direct, qualified, and method-call reference records with confidence and resolution status;
- comments/POD payloads only when explicitly enabled and bounded;
- dependency index, diagnostics, and changed/deleted-path metadata.

The core must run without Neo4j, FalkorDB, Qdrant, credentials, or network access. Persistence and vector indexing are adapters applied only after a valid analysis result exists.

### Graph model

Reuse canonical graph labels and writer APIs rather than introducing Perl-specific labels:

| Perl record | Canonical graph representation |
| --- | --- |
| source file | `File` |
| package declaration | `Namespace` |
| named subroutine | `Function` |
| `my` / `our` / `local` declaration | existing field/property representation with declaration kind and scope |
| `use` / `require` dependency | `IMPORTS` relation with raw and normalized module evidence |
| conservatively resolved call | `CALLS` |
| ambiguous/dynamic call | unresolved JSON reference/diagnostic; `POSSIBLE_CALLS` only when both endpoints have evidence |

No `framework_registry.py` profile or schema/index change is expected unless implementation proves that canonical labels and relationships cannot answer the promised structural queries.

## Phases

1. [Phase 01 - Validate the grammar and freeze contracts](phase-01-contract-and-grammar.md)
2. [Phase 02 - Implement deterministic Perl extraction](phase-02-parser-and-normalization.md)
3. [Phase 03 - Add conservative resolution and incremental analysis](phase-03-resolution-and-incremental.md)
4. [Phase 04 - Integrate graph, CLI, sync, and MCP](phase-04-harness-integration.md)
5. [Phase 05 - Harden, document, and verify acceptance](phase-05-hardening-and-acceptance.md)

## Cross-Plan Dependencies

- `neo4j-to-falkordb-migration` blocks provider-parity acceptance because the Perl analyzer will write canonical language facts through the same provider-neutral graph abstraction and exercise the same provider CLI/runtime contract. Phases 01-03 can proceed independently; Phase 04 graph persistence and Phase 05 provider-parity gates require the stabilized contract.
- `260714-1603-flutter-analyzer-parser` is not a functional blocker, but both plans change `code-tiny/requirements.txt`, analyzer registries, owner manifests, root CLI mappings, unified MCP routing/instructions, documentation, and common registry tests. Keep all edits additive and merge registry expectations rather than replacing either parser family.
- `260715-2011-aspnet-roslyn-analyzers` is not a functional blocker, but it overlaps root/framework analyzer registries, unified MCP routing/instructions, documentation, and common registry/MCP tests. Keep parser and alias expectations additive and preserve primary-versus-overlay ownership.

## Target File Map

| Area | Planned files |
| --- | --- |
| Perl package | `code-tiny/tools/perl/__init__.py`, `models.py`, `parser_runtime.py`, `perl_parser.py`, `resolver.py`, `pipeline.py`, `perl_analyzer.py`, `README.md` |
| Dependency | `code-tiny/requirements.txt` |
| Shared sync/ownership | `code-tiny/tools/sync/incremental_sync.py`, `code-tiny/tools/sync/owner_manifest.py` |
| Root CLI | `cortex_harness/dev.py` |
| Unified MCP | `code-tiny/mcp/unified_mcp.py`; review `framework_registry.py` but change it only if custom behavior becomes necessary |
| Tests/fixtures | `tests/fixtures/perl-application/`, focused `tests/test_perl_*.py`, and additive updates to common registry/routing tests |
| Supported-tool docs | `README.md`, `docs/specs/sync-code.md`, `code-tiny/mcp/Readme.md`; review `code-tiny/docs/guide_tool_integrate.md` for any newly discovered shared integration point |

## Verification Strategy

1. Grammar import/ABI smoke tests and node-type contract tests.
2. Parser unit and golden JSON tests covering valid, malformed, dynamic, POD, and boundary cases.
3. Determinism tests across repeated runs and different checkout roots.
4. Resolution tests that prove unresolved/ambiguous calls are not fabricated as resolved edges.
5. Full, changed, impacted, and deleted incremental tests with cache hit/miss/invalidation coverage.
6. Registry, owner-manifest, root discovery, and shared CLI-contract tests.
7. Provider-neutral graph contract tests plus live Neo4j/FalkorDB parity smoke tests when available.
8. Unified MCP `list_parsers`, `activate_project(parser_type="perl")`, search, traversal, and project-scope tests.
9. `python -m py_compile` for changed Python files, focused unittest/pytest suites, relevant regressions, and `git diff --check`.

## Success Criteria

- A clean environment can import the pinned Perl grammar and parse the representative fixture without executing Perl.
- `.pl`, `.pm`, and `.t` files are exclusively routed to the `perl` primary parser by root discovery, incremental sync, and owner manifests.
- Identical sources produce byte-stable normalized JSON and stable semantic IDs across repeated runs and checkout paths.
- Packages, named subroutines, `my`/`our`/`local` declarations, `use`/`require`/`no`, direct/qualified/method calls, and optional inline POD are extracted with source ranges.
- `eval`, symbolic references, dynamic module names, and unresolved method receivers remain explicitly unresolved with bounded diagnostics.
- Full and incremental analysis agree for affected files/modules; deleted files remove stale graph/vector facts only after a successful staged analysis.
- The shared CLI contract works in dry-run and persistence modes and returns non-zero exit codes for argument, analysis-policy, and persistence failures as documented.
- `list_parsers` exposes `perl`, `activate_project(parser_type="perl")` selects the generic language backend, and existing MCP tools query Perl facts without a dedicated server.
- Existing parser, provider, registry, MCP, and sync regression tests remain green.

## Risks and Mitigations

| Risk | Mitigation |
| --- | --- |
| Perl grammar is stale or incompatible with the repository's Tree-sitter ABI | Make grammar/package selection a Phase 01 gate; pin versions; test real Perl constructs before building extractors. |
| Grammar node names drift | Centralize node mappings and parser/grammar versions; add compatibility and golden tests. |
| Dynamic Perl constructs create false call/import edges | Resolve only evidence-backed targets; retain raw references, confidence, and unresolved diagnostics. |
| Multiple `package` declarations and lexical scopes break symbol ownership | Track package boundaries and explicit scope stacks from AST ranges; test multi-package files and nested blocks. |
| Incremental changes miss reverse dependents | Persist a normalized import/package dependency index and compare affected incremental results with a clean full run. |
| Registry edits conflict with Flutter/provider work | Coordinate additive changes through the common registry test and bidirectional plan dependencies. |
| Provider failure leaves partial generations | Stage validated results, write through shared provider APIs, and apply cleanup/publish only after successful writes. |
| POD/comments or large files inflate output | Make documentation extraction optional, enforce per-file/total budgets, and report deterministic truncation. |
