---
title: "MCP Query Capability Hardening and Semantic Overlays"
status: complete
created: 2026-07-19
mode: hi-plan --fast
scope: unified MCP routing, schema gates, web/database overlays, and fixture-backed acceptance
blockedBy: [neo4j-to-falkordb-migration]
dependencyOverride: implement and verify against the active provider-neutral FalkorDB contract; Neo4j parity remains externally gated
relatedPlans: [260715-2200-mcp-capability-routing, 260713-1638-framework-parser-integration, 260715-2011-aspnet-roslyn-analyzers]
reviewed: 2026-07-19
---

# MCP Query Capability Hardening and Semantic Overlays

## Overview

Make Unified MCP explicit and truthful about the distinction between source
ingestion, parser capability profiles, and the shared graph-query engine. Reject
unknown parser names, expose `query_engine=graph_generic` instead of the internal
`cplus` backend name, gate endpoint/database tools against the provider's actual
labels and relationships, and add missing semantic overlays for Python, JavaScript,
PHP, SQL, and PL/SQL.

This plan preserves the existing architecture: primary analyzers own canonical
`File`/`Class`/`Function` facts, while framework/database overlays add semantic
nodes and edges linked to canonical symbols. It does not create one MCP server per
language.

## Contract Decisions

- Internal backend key `cplus` remains temporarily for module dispatch; public
  responses expose `query_engine=graph_generic`. Android exposes
  `query_engine=android_graph`.
- An omitted parser may use the generic profile with a structured warning. A
  non-empty unknown parser is an `unsupported_parser` error and never dispatches.
- Support is reported by dimension: `symbols`, `calls`, `endpoints`, and
  `database`. The legacy scalar support level may remain only as a deprecated
  compatibility field during the transition.
- Capability gates inspect both node labels and relationship types. Required
  schema absence returns `capability_unavailable`; it is not an empty success.
- Web and database semantics are non-exclusive overlays. Primary language
  ownership and vector collections remain unchanged.
- Acceptance uses source fixtures and extracted graph facts. Alias-only routing
  assertions are insufficient.

## Phases

1. [Phase 01 - Public routing and support contract](phase-01-routing-support-contract.md)
2. [Phase 02 - Provider schema gates](phase-02-provider-schema-gates.md)
3. [Phase 03 - Web framework ingestion overlays](phase-03-web-framework-overlays.md)
4. [Phase 04 - SQL and PL/SQL semantic profile](phase-04-database-semantic-profile.md)
5. [Phase 05 - Fixture-backed acceptance matrix](phase-05-acceptance-matrix.md)

## Dependencies

- Extends completed plan `260715-2200-mcp-capability-routing` without replacing
  its canonical registry.
- Reuses the overlay orchestration model from
  `260713-1638-framework-parser-integration`.
- Reuses provider-neutral driver/writer interfaces from
  `neo4j-to-falkordb-migration`; live Neo4j parity is not claimed until that plan
  completes.
- Must preserve existing Android, Spring, Servlet/JSP, MyBatis, Struts, Flutter,
  ASP.NET, COBOL, Perl, and generic language behavior.

## Expected File Areas

- `code-tiny/mcp/framework_registry.py`
- `code-tiny/mcp/unified_mcp.py`
- `code-tiny/mcp/cplus/cplus_mcp.py`
- `code-tiny/tools/sync/incremental_sync.py`
- `code-tiny/tools/web_framework/` (new shared overlay package)
- `code-tiny/tools/database_schema/` (new shared overlay package)
- `code-tiny/tools/graph/writer/` (provider-neutral overlay writers)
- `tests/fixtures/` and `tests/test_*capability*` / `tests/test_*overlay*`
- `docs/` capability/acceptance documentation

## Verification Strategy

- Contract unit tests for unknown parser rejection and public engine naming.
- Provider-schema tests for missing labels, missing relationships, unavailable
  inspection, and fully supported schemas.
- Pure extraction tests over real FastAPI, Django, Express JS, Laravel, SQL, and
  PL/SQL fixture files.
- Recording-driver writer tests proving exact node/edge rows and project scoping.
- Incremental orchestration tests proving overlays run after their prerequisite
  primary analyzers and respect changed/deleted manifests.
- Unified MCP acceptance tests that query fixture-derived graph data and assert
  successful/blocked tools according to the capability matrix.
- Focused regression suite; no full embedding sync is required for acceptance.

## Success Criteria

- Unknown non-empty `parser_type` returns `unsupported_parser` with supported
  canonical profiles/aliases.
- Public tool responses no longer identify the generic query engine as `cplus`.
- Endpoint tools require their declared labels and relationships and return
  `capability_unavailable` when absent.
- Capability discovery reports independent symbol/call/endpoint/database support.
- FastAPI/Django, Express JS, and Laravel fixtures create `ApiEndpoint` plus
  handler links to canonical symbols.
- SQL/PLSQL fixtures create `Table`, `View`, and `Procedure` facts plus
  `READS_FROM`, `WRITES_TO`, and `REFERENCES_TABLE` edges.
- Every advertised canonical parser appears in the acceptance matrix; the target
  web/database profiles have fixture-backed semantic assertions, while unaffected
  profiles retain existing real-parser regression evidence.
- Targeted tests, Python compilation, PowerShell/bash lifecycle regressions, and
  diff checks pass.

## Plan Review

Reviewed before implementation under the `hi-craft` hard gate.

- The design keeps physical backend count stable and limits breaking changes to
  the requested public response contract.
- Schema gating is implemented before overlays so unsupported live graphs fail
  honestly during rollout.
- Shared overlay packages avoid three copy-pasted query backends and keep primary
  analyzers authoritative.
- Fixture parsing and recording-driver tests provide deterministic acceptance
  without the previously observed long-running CPU embedding sync.
- Main residual risk is provider parity; FalkorDB is the active acceptance target
  and Neo4j parity stays explicitly blocked by the migration plan.

## Completion

- Focused MCP/overlay regression: 69 tests passed.
- Lifecycle/provider regression: 21 passed, 7 environment-dependent tests skipped.
- Post-review fixture suite: 39 tests passed.
- Python compilation and `git diff --check` passed.
- Full unittest discovery was stopped after approximately 60 seconds without
  output; no embedding or full ingestion sync was run.
