---
title: "ASP.NET Roslyn Analyzers Plan"
status: in_progress
created: 2026-07-15
mode: hi-plan --fast
scope: ASP.NET Core and ASP.NET Framework semantic overlays, shared Roslyn frontend, migration model, graph ingestion, incremental sync, unified MCP
blockedBy: [neo4j-to-falkordb-migration]
relatedPlans: [260713-1638-framework-parser-integration, 260714-1603-flutter-analyzer-parser, 260715-1629-perl-analyzer-parser]
sources:
  - /Users/hieplq1.rpm/Desktop/ASPNet_Migration_Package/ASP.NET_Core_Analyzer_Design_Spec.md
  - /Users/hieplq1.rpm/Desktop/ASPNet_Migration_Package/ASP.NET_Framework_Analyzer_Design_Spec.md
  - /Users/hieplq1.rpm/Desktop/ASPNet_Migration_Package/ASP.NET_Migration_Semantic_Model.md
---

## Execution status (2026-07-15)

Implemented and verified: shared versioned Roslyn worker/adapter with bounded safe
compilation, Core and Framework detectors and artifact parsers, immutable shared
facts/relationships, canonical C# anchors, redaction, staged generation graph
writes, per-module deletion cleanup, incremental registry/CLI/MCP integration,
and deterministic focused fixtures. The worker deliberately reports `partial`
when a project path is supplied but project properties/references cannot be
evaluated safely; it never promotes that evidence as complete semantic coverage.

Remaining acceptance gaps: runtime-specific MVC/Web API filters, validation,
authentication/authorization, injection, result, and configuration-consumer
resolution are not yet fully reconstructed. Live provider parity is also gated
on the repository's external graph services. These are represented as explicit
partial/unresolved output rather than fabricated edges.

Verification evidence: worker Release build passes; ASP.NET plus shared registry,
sync, and MCP tests pass; the full repository run reaches 141 passing tests. The
remaining 23 failures require unavailable pre-existing COBOL/Perl parser
dependencies (`tree-sitter-perl` and the COBOL runtime).

# ASP.NET Roslyn Analyzers Plan

## Overview

Build two independently runnable framework analyzers under `code-tiny/tools/`:

- `aspnet_framework` reconstructs ASP.NET Framework runtime, MVC/Web API, Web Forms, configuration, and view semantics.
- `aspnet_core` reconstructs ASP.NET Core hosting, middleware, routing, controllers, Razor Pages, Minimal APIs, DI, configuration, and view semantics.

Both tools use Roslyn for C# syntax and semantic evidence, parse non-C# artifacts with safe format-specific parsers, and emit the shared ASP.NET migration semantic model. They are framework overlays on the existing `csharp` primary analyzer: `.cs` ownership and canonical C# symbols remain with `code-tiny/tools/csharp/csharp_analyzer.py`; ASP.NET facts link to those symbols through stable coordinates and `SEMANTIC_OF` relationships.

```text
C# primary analyzer ───────────────> canonical File/Namespace/Type/Function facts
                                            ▲
                                            │ SEMANTIC_OF
solution/project + C# ─> shared Roslyn worker ─┐
Razor/ASPX/config/resources ─> safe parsers ───┼─> framework-specific resolver
project/module detector ───────────────────────┘          │
                                                         ▼
                                   unified ASP.NET migration semantic facts
                                                         │
                                  provider-neutral graph + unified MCP queries
```

## Verified Project Context

- `code-tiny/tools/csharp/csharp_analyzer.py` already owns `.cs` files and emits canonical C# graph facts using Tree-sitter. The new analyzers must enrich those facts, not compete for ownership.
- `code-tiny/tools/vb/vb_roslyn_adapter.py` and `code-tiny/tools/vb/roslyn_worker/` provide a proven Python-to-Roslyn subprocess pattern: build locking, runtime selection, manifests, workspace/syntax modes, bounded timeouts, and JSON output. ASP.NET needs a separate C# worker contract, but should reuse the operational pattern.
- Framework overlays are registered in `code-tiny/tools/sync/incremental_sync.py::FRAMEWORK_ANALYZERS` and `cortex_harness/dev.py::FRAMEWORK_ANALYZERS`, run after declared primary parsers, and are detector-gated.
- `code-tiny/mcp/framework_registry.py` owns framework aliases, searchable labels/properties, and default traversal relationships. `code-tiny/mcp/unified_mcp.py` routes these aliases through the shared backend.
- Existing Spring and Servlet/JSP packages are the closest patterns for detectors, non-language artifacts, immutable models, resolution, generation-scoped writes, and query profiles.
- `code-tiny/docs/guide_tool_integrate.md` requires the shared CLI, incremental routing/deletion, graph-provider abstraction, root CLI registration, MCP registration, tests, and documentation.
- `docs/development-rules.md` is absent. The supplied root instructions, integration guide, tool template, and verified repository conventions govern this plan.

## Scope and Architectural Decisions

### Two overlays, one shared foundation

The tools remain separate because their detectors, artifacts, runtime models, and semantic rules differ. Shared code is limited to contracts used by both: Roslyn process/protocol support, stable semantic identities, spans/diagnostics, safe XML/JSON handling, redaction, and project metadata.

Planned package roots:

- `code-tiny/tools/aspnet_framework/`
- `code-tiny/tools/aspnet_core/`
- `code-tiny/tools/common/aspnet/` for shared support; placing it under `tools/common/` prevents it from being advertised as a parser.

### Roslyn boundary

A shared C# worker under `tools/common/aspnet/roslyn_worker/` loads `.sln` or `.csproj` workspaces when possible and emits a versioned deterministic compiler-evidence protocol. It covers resolved symbols, attributes, inheritance, invocations, constant arguments, source spans, project/target-framework metadata, and bounded diagnostics. Framework-specific Python resolvers combine this evidence with markup and configuration facts.

Workspace failure must not silently become successful semantic analysis. Syntax-only Roslyn output is allowed with `coverage_status=partial`, explicit capability/diagnostic records, and no fabricated resolved edges. This is especially important for legacy projects on hosts without compatible reference assemblies or MSBuild workloads.

### Module-level detection

Detection occurs per solution/project module, not once per repository. A repository may contain both legacy and modern projects.

| Overlay | Strong evidence examples |
| --- | --- |
| ASP.NET Core | `Microsoft.NET.Sdk.Web`, ASP.NET Core references, Program/Startup, `appsettings*.json`, Razor conventions |
| ASP.NET Framework | `System.Web`, legacy target metadata, `web.config`, Global.asax, App_Start, `.aspx`/`.ascx`/`.master`, `packages.config` |

Ambiguous modules produce diagnostics and deterministic evidence rather than selecting by directory order. Both overlays may run in one repository when different modules satisfy their detectors.

### Non-C# artifacts

Roslyn is the C# frontend only. Use safe, non-executing parsers for:

- Core: Razor `.cshtml`/`.razor` through a validated Razor parser, plus JSON for appsettings and related metadata.
- Framework: ASPX/ASCX/Master/ASMX/ASHX markup, `web.config`/`packages.config`, `.resx`, and legacy view fragments.

Phase 01 validates exact parser packages/APIs and records unsupported constructs. XML external entities, template execution, analyzed-project builds, generators, and runtime instrumentation are prohibited.

## Unified Semantic Graph Contract

Use the supplied migration model as the public contract. Persist PascalCase node labels and repository-standard uppercase relationships.

### Required node labels

`HttpEndpoint`, `Route`, `Middleware`, `Controller`, `Action`, `RazorPage`, `PageHandler`, `WebFormPage`, `HttpHandler`, `HttpModule`, `Filter`, `Result`, `View`, `Layout`, `PartialView`, `Service`, `Repository`, `Model`, `ViewModel`, `ValidationRule`, `ConfigurationKey`, `SessionState`, `ApplicationEvent`, `AuthenticationScheme`, and `AuthorizationPolicy`.

### Required relationship types

`MAPPED_TO`, `HANDLED_BY`, `PASSES_THROUGH`, `INVOKES`, `INJECTS`, `VALIDATES_WITH`, `RENDERS`, `REDIRECTS_TO`, `FORWARDS_TO`, `LOADS_FROM`, `DEPENDS_ON`, `READS_CONFIG`, `WRITES_SESSION`, `POSTS_BACK_TO`, `INITIALIZES`, and `RETURNS_RESULT`.

`SEMANTIC_OF` links an ASP.NET fact to an existing canonical C# symbol. Properties distinguish `framework=aspnet_core` from `framework=aspnet_framework`; stable identities include project, module, framework, kind, and semantic coordinates. Ordered request/lifecycle sequences carry explicit positions. Dynamic routes, reflection, runtime DI, convention-only views, and unresolved handlers remain partial/unresolved with evidence and confidence.

## Phases

1. [Phase 01 - Freeze contracts and prove parser capabilities](phase-01-contract-and-capabilities.md)
2. [Phase 02 - Build shared Roslyn and semantic foundations](phase-02-shared-roslyn-foundation.md)
3. [Phase 03 - Implement the ASP.NET Framework analyzer](phase-03-framework-analyzer.md)
4. [Phase 04 - Implement the ASP.NET Core analyzer](phase-04-core-analyzer.md)
5. [Phase 05 - Integrate graph, sync, CLI, and MCP](phase-05-harness-integration.md)
6. [Phase 06 - Harden and verify migration acceptance](phase-06-hardening-and-acceptance.md)

Phases 03 and 04 can proceed in parallel after Phase 02. Phase 05 begins only after both analyzers satisfy their normalized JSON contracts, so shared registry and graph changes are made once and tested together.

## Cross-Plan Dependencies

- `neo4j-to-falkordb-migration` blocks provider-parity acceptance because these analyzers add custom labels, relationships, generation cleanup, and MCP traversal. Phases 01-04 can proceed; Phase 05 persistence and Phase 06 live parity require stabilized provider behavior.
- `260713-1638-framework-parser-integration` is a completed pattern, not a blocker. Reuse its overlay ownership, detector gating, stable/generation identity, and framework-registry approach.
- `260714-1603-flutter-analyzer-parser` and `260715-1629-perl-analyzer-parser` are not functional blockers, but all touch analyzer registries, root CLI, unified MCP, supported-tool docs, and common registry/routing tests. Keep edits additive.
- No owner-manifest change is planned: `csharp` remains the exclusive `.cs` owner, and both ASP.NET tools are overlays.

## Target File Map

| Area | Planned files |
| --- | --- |
| Shared support | `code-tiny/tools/common/aspnet/{__init__.py,models.py,identity.py,project_metadata.py,safe_formats.py,roslyn_adapter.py}` and `roslyn_worker/{AspNetRoslynWorker.csproj,Program.cs}` |
| Framework tool | `code-tiny/tools/aspnet_framework/{__init__.py,detector.py,artifact_parsers.py,resolver.py,pipeline.py,aspnet_framework_analyzer.py,README.md}`; split parser modules only when justified |
| Core tool | `code-tiny/tools/aspnet_core/{__init__.py,detector.py,artifact_parsers.py,resolver.py,pipeline.py,aspnet_core_analyzer.py,README.md}`; split Razor/config modules only when justified |
| Integration | `code-tiny/tools/sync/incremental_sync.py`, `cortex_harness/dev.py`, `code-tiny/mcp/framework_registry.py`, `code-tiny/mcp/unified_mcp.py`; review MCP services and `code-tiny/scripts/setup_constraints.py` against the final graph contract |
| Tests | `tests/fixtures/aspnet-framework-application/`, `tests/fixtures/aspnet-core-application/`, optional paired fixture, focused `tests/test_aspnet_*.py`, and additive registry/MCP tests |
| Documentation | `README.md`, `docs/specs/sync-code.md`, `docs/specs/sync-doc.md`, `code-tiny/mcp/Readme.md`; update the integration guide only if a new shared rule is discovered |

## Verification Strategy

1. Roslyn worker build, protocol, workspace, syntax-only, timeout, malformed-output, and runtime tests.
2. Detector tests for Core-only, Framework-only, mixed, ambiguous, unrelated C#, and deleted strong candidates.
3. Parser/resolver golden tests asserting semantic paths and order, not only counts.
4. Determinism across repeated runs, file orders, and checkout roots.
5. Full versus changed/impacted/deleted incremental parity and generation cleanup.
6. Graph tests for labels, directions, anchors, no orphans, redaction, and provider-neutral queries.
7. CLI, registry, `list_parsers`, `activate_project`, search, traversal, endpoint, and impact tests.
8. Python compilation/tests, worker builds/tests, regressions, `git diff --check`, and live provider parity when available.

## Success Criteria

- Both tools are independently runnable detector-gated overlays with `csharp` prerequisites and the shared CLI contract.
- Roslyn produces versioned deterministic evidence and clearly reports workspace versus syntax-only capability.
- Module detection handles Core, Framework, mixed, and unrelated C# fixtures without changing `.cs` ownership.
- Framework output covers Global.asax, modules, handlers, routes/controllers/actions, Web Forms lifecycle/state/postback, views, and config.
- Core output covers hosting/services, ordered middleware, routing, controllers/actions, Razor Pages/handlers, Minimal APIs, filters, validation, DI, views, and config.
- Both emit the same unified vocabulary, retain framework metadata, and anchor compiler facts without duplicating canonical C# nodes.
- Full and incremental results agree; deletion removes only overlay-owned facts after a successful staged generation.
- Configuration secrets are redacted before diagnostics, caches, previews, vectors, or graph persistence.
- Unified MCP discovers and queries both frameworks through the existing backend.
- Existing C#, framework, provider, registry, sync, CLI, and MCP regressions remain green.

## Risks and Mitigations

| Risk | Mitigation |
| --- | --- |
| Legacy workspaces cannot load on the host | Separate workspace/syntax states; test missing reference assemblies; emit partial coverage instead of false links. |
| Roslyn duplicates primary C# facts | Keep overlays, use canonical coordinates and `SEMANTIC_OF`, and never change owner manifests. |
| Detection overlaps in mixed repos | Detect per module with explicit evidence and deterministic ambiguity tests. |
| Pipeline/lifecycle order is lost | Store position/branch/terminal metadata and assert ordered paths. |
| Razor/Web Forms parser support is host-dependent | Make capability validation a Phase 01 gate and degrade explicitly. |
| Configuration leaks credentials | Centralize classification/redaction before every output boundary. |
| Incremental changes miss dependencies | Persist code/config/view/project dependencies and compare with clean full runs. |
| Shared registries conflict with active plans | Update dependencies bidirectionally, make additive edits, and gate with common tests. |
| Provider Cypher diverges | Use graph abstractions, staged generations, and logical provider parity tests. |
