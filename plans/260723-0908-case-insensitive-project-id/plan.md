---
title: "Case-Insensitive MCP Project Scope Lookup"
status: completed
created: 2026-07-23
mode: hi-plan --fast
scope: code-tiny MCP project-scoped graph and Qdrant retrieval
blockedBy: [neo4j-to-falkordb-migration]
---
# Case-Insensitive MCP Project Scope Lookup

## Overview

Make every MCP argument that scopes retrieval by a project identifier compare that identifier without regard to letter case. Inputs such as `HIEP`, `hiep`, and `hiEp` must select the same logical project scope while the originally stored `project_id` remains unchanged in graph nodes, vector payloads, identifiers, and returned data.

The implementation must cover the complete retrieval path rather than only the public MCP wrapper. Repository evidence shows that project scope is currently enforced through exact comparisons in three places:

- Python candidate filtering in `code-tiny/tools/common/project_scope.py` and direct driver post-filters.
- Exact graph predicates in shared retrieval, graph expansion, workflow, backend MCP, and bridge-query paths.
- Exact Qdrant payload filters built by `qdrant_project_filter()` and used by the fast, C++, Android, Java, and intelligent-retrieval backends.

The plan introduces one internal, indexed comparison field, `project_id_normalized`, computed from the trimmed identifier with Unicode-aware `casefold()`. The raw `project_id` remains the source of truth for display and identity. Every case variant therefore maps to the same comparison key without renaming projects or changing symbol IDs, point IDs, collection names, database names, or graph names.

## Verified Current Behavior

- `normalize_project_id()` trims input but preserves case; `matches_project_scope()` then uses exact equality.
- `qdrant_project_filter()` emits an exact `project_id` match, and existing tests assert that exact payload contract.
- `GraphExpander`, intelligent retrieval, backend MCP modules, graph drivers, and bridge queries contain exact `project_id = $project_id` predicates or exact Python equality checks.
- Unified MCP metadata exposes `project_id`, `be_project_id`, and `fe_project_id`; `find_screen_workflows` requires `project_id`, while semantic/explore and full-stack bridge tools accept optional project scopes.
- Primary vector mapping preserves the raw `project_id` in the Qdrant payload and deterministic point identity.
- The active `neo4j-to-falkordb-migration` plan owns provider-neutral graph query and index behavior in files this work must touch.
- `docs/development-rules.md` is not present in this checkout, so no additional repository-specific development rules could be applied.

## Decisions

| Topic | Decision |
| --- | --- |
| Comparison | Use a dedicated `project_id_normalized` key generated with `str(value).strip().casefold()`. |
| Raw values | Preserve `project_id` exactly except for existing trimming behavior; do not rewrite user-visible IDs. |
| Scope semantics | Case-only variants are one logical scope and may return records stored under any of those variants. |
| Query performance | Filter the normalized field with exact indexed predicates; do not wrap indexed raw fields in `toLower()` on every request. |
| Existing data | Backfill graph and Qdrant records before enabling the new filter contract; the migration is idempotent and reports collisions. |
| Empty input | Preserve current semantics: blank optional scope remains unscoped; required scope still fails validation. |
| Non-project names | Do not change case handling for database, graph, collection, parser, symbol, endpoint, module, file, or node identifiers. |

## Scope

### In scope

- Shared normalization and comparison helpers.
- All public MCP inputs named `project_id`, `be_project_id`, or `fe_project_id`.
- Project-scope predicates in graph retrieval, semantic search, graph expansion, workflows, and full-stack bridge queries.
- Qdrant payload production and exact server-side filtering by the normalized field.
- Provider-aware graph and Qdrant backfill, normalized-field indexes, metadata updates, and regression tests.

### Out of scope

- Renaming projects or changing the stored raw `project_id`.
- Changing project identity inside symbol IDs, deterministic vector point IDs, cache keys, collection names, or database names.
- General case-insensitive matching for symbols, paths, modules, endpoints, or parser names.
- Refactoring unrelated analyzer, graph-provider, or MCP behavior.

## Phases

1. [Phase 01 - Add one normalized project-scope contract](phase-01-normalized-scope-contract.md)
2. [Phase 02 - Backfill, index, and verify all MCP paths](phase-02-migration-and-verification.md)

## Dependencies

- `blockedBy: neo4j-to-falkordb-migration` because normalized graph persistence, indexes, and query predicates must behave identically through both providers.
- The dependency is bidirectional in plan metadata: the migration plan lists this plan in `blocks` and records the shared query/index surface.
- Completed MCP capability and parser-runtime plans are implementation context, not blockers.

## Risks

| Risk | Mitigation |
| --- | --- |
| Two intentionally distinct projects differ only by case | Treat them as one scope as requested, report every collision during dry-run/backfill, and do not delete or rename either raw ID. |
| Partial rollout hides legacy records | Gate query-contract activation on successful backfill/index verification for both graph and Qdrant stores. |
| A backend keeps an exact raw predicate | Inventory generated MCP metadata and exact query occurrences, then add cross-backend contract tests for every scoped tool family. |
| Normalization accidentally changes identities | Keep raw IDs in deterministic point IDs, symbol IDs, output payloads, and persistence keys; normalize only the comparison field. |
| Provider-specific index/query drift | Add equivalent Neo4j and FalkorDB assertions and coordinate with the active provider migration. |

## Success Criteria

- `HIEP`, `hiep`, and `hiEp` produce the same project-scoped result set for graph-only, vector-only, combined semantic, graph-expansion, workflow, and full-stack bridge MCP paths.
- `be_project_id` and `fe_project_id` follow the same rule independently.
- A different identifier such as `hiep-2` is never admitted into the `hiep` scope.
- Blank optional scopes remain unscoped and required scopes remain validated.
- Raw `project_id` values and all existing identity formats remain unchanged in responses and storage.
- Existing graph and Qdrant data can be upgraded idempotently, with collision and missing-field counts reported before mutation.
- Exact normalized-field filters are indexed and used server-side; no unscoped oversampling or client-only filtering is introduced.
- Focused project-scope tests and relevant MCP/provider regressions pass on both supported graph providers, with live-service checks recorded when the services are available.

## Task Hydration

No session tasks are created because this plan has fewer than three phases, matching the `hi-plan` task-management rule.

## Verification

- Focused project-scope, Qdrant, graph expansion, unified MCP, framework routing, and FalkorDB regressions: 75 passed with 59 subtests passed.
- Repository-wide suite: 273 passed with 174 subtests passed; 30 unrelated environment/baseline failures remain in COBOL and Perl parser runtimes, local Neo4j-dependent incremental-sync tests, and macOS temporary-path canonicalization tests.
- Python compilation and `git diff --check` completed successfully.
- Live graph/Qdrant smoke checks were not run because the required services are not available in this workspace.
