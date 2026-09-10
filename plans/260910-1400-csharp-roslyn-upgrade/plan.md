---
title: "C# Roslyn Primary Analyzer Upgrade and Semantic Framework Items"
status: in_progress
created: 2026-09-10
mode: hi-plan --full
scope: "Replace Tree-sitter with Roslyn-first parsing in csharp_analyzer.py, add full C# member inventory, semantic framework items (DI, Config, EF, gRPC, SignalR, Background services, Auth, Middleware, Minimal APIs, Logging, NuGet), and Tree-sitter fallback"
blockedBy: []
blocks: []
relatedPlans:
  - 260715-2011-aspnet-roslyn-analyzers
  - 260713-1638-framework-parser-integration
  - 260807-1329-parser-quality-recovery
  - 260821-1144-cplus-semantic-call-graph
sources:
  - code-tiny/tools/csharp/csharp_analyzer.py
  - code-tiny/tools/common/aspnet/roslyn_adapter.py
  - code-tiny/tools/vb/vb_roslyn_adapter.py
  - code-tiny/tools/aspnet_core/resolver.py
  - code-tiny/mcp/framework_registry.py
  - code-tiny/tools/sync/incremental_sync.py
---

# C# Roslyn Primary Analyzer Upgrade and Semantic Framework Items

## Overview

Nâng cấp toàn bộ `csharp_analyzer.py` từ Tree-sitter (syntax-only) sang **Roslyn-first** parsing với Tree-sitter fallback. Tạo C# Roslyn worker riêng (`CSharpRoslynWorker`) tối ưu cho primary analysis, bổ sung full member inventory và semantic framework items phổ thông cho các dòng dự án C#.

### Motivation

Tree-sitter C# parser hiện tại chỉ extract được:
- Namespaces, Types (class/struct/interface/enum)
- Methods, Constructors, Local functions
- Invocation calls (name-based, không resolved)

**Thiếu:**
- Properties (get/set/init), Fields, Events, Delegates
- Generics (type parameters, constraints)
- Attributes/Annotations (resolved names)
- Inheritance chains, Interface implementations (resolved)
- XML documentation comments (`///`)
- Partial classes (merged)
- Using directives (resolved)
- Lambda/anonymous functions
- Extension methods (resolved)
- Project/Solution awareness (.csproj, .sln, references, NuGet)
- Async/await patterns, LINQ, Pattern matching, Records, Nullable

### Architecture

```text
csharp_analyzer.py
    │
    ├── Roslyn-first path (default)
    │   ├── CSharpRoslynWorker (.NET subprocess)
    │   │   ├── Modeled on AspNetRoslynWorker (common/aspnet/roslyn_worker/)
    │   │   ├── Uses CSharpCompilation.Create() with TrustedPlatformReferences
    │   │   ├── Workspace mode: .sln/.csproj via MSBuildWorkspace → full semantic
    │   │   └── Syntax mode: standalone .cs → CSharpCompilation without project
    │   ├── Full member inventory (properties, fields, events, delegates)
    │   ├── Resolved inheritance/implementation chains
    │   ├── Attributes, generics, XML docs, accessibility
    │   ├── Project metadata (target framework, NuGet refs, project refs)
    │   └── Framework-agnostic items (EF, gRPC, SignalR, Auth, Logging, NuGet)
    │       └── NOT duplicating ASP.NET overlay items (DI, middleware, minimal API, config)
    │
    ├── Tree-sitter fallback (when Roslyn unavailable)
    │   └── Existing parser with parse-quality metadata
    │
    └── Graph + Vector ingestion
        ├── Enhanced node labels and relationships
        ├── Framework-agnostic semantic nodes
        └── Parse provenance (roslyn_workspace / roslyn_syntax / tree_sitter)
```

### Researcher Findings (Incorporated)

1. **AspNetRoslynWorker** at `common/aspnet/roslyn_worker/Program.cs` (460 lines) is the best template — already uses `CSharpCompilation.Create()` with `TrustedPlatformReferences`
2. **ASP.NET overlays already handle**: DI registration, middleware pipeline, minimal API endpoints, configuration binding, controller/action detection → primary does NOT duplicate these
3. **Primary C# analyzer focuses on**: language-level semantics (properties, fields, events, delegates, records, generics, attributes, accessibility, resolved calls) + framework-agnostic patterns (EF, gRPC, SignalR, Background services, Auth, Logging, NuGet)
4. **Protocol reuse**: ASP.NET worker's evidence format (TypeEvidence, MemberEvidence, InvocationEvidence, AttributeEvidence) can be extended for C# primary use

## Scope Challenge Decisions

| Decision | Choice | Rationale |
| --- | --- | --- |
| Roslyn worker | Tạo CSharpRoslynWorker riêng | Tối ưu cho primary analysis, không risk ảnh hưởng ASP.NET overlays |
| Framework items | Full spectrum | DI, Config, EF, gRPC, SignalR, Background services, Auth, Middleware, Minimal APIs, Logging, NuGet |
| Fallback strategy | Roslyn-first, Tree-sitter fallback | Maximize coverage, graceful degradation, parse provenance tracking |

## Verified Baseline (Researcher Findings 2026-09-10)

| Signal | Evidence | Planning implication |
| --- | --- | --- |
| VB Roslyn worker | `code-tiny/tools/vb/roslyn_worker/Program.cs` (1076 lines, gitignored) | Proven pattern: Roslyn syntax tree walking, extracts classes/interfaces/enums/constants/variables/methods |
| ASP.NET Roslyn worker | `code-tiny/tools/common/aspnet/roslyn_worker/AspNetRoslynWorker.csproj` + `Program.cs` (gitignored) | Shared worker for framework overlays, emits types/members/attributes evidence |
| ASP.NET resolver | `code-tiny/tools/aspnet_core/resolver.py` | Already extracts Controller, Action, RazorPage, PageHandler, Repository, Service, Model from Roslyn evidence |
| C# profile | `framework_registry.py:518` — `_generic_profile("csharp", ...)` | Very basic, uses GENERIC_LABELS only — needs expansion |
| Current data model | FunctionDef, FileDef, NamespaceDef, TypeDef, RelationEdge, CallEdge | Missing: properties, fields, events, delegates, generics, attributes, XML docs, accessibility |
| .gitignore | Lines 43, 125-126, 130-131 | VB and ASP.NET roslyn_worker dirs are gitignored (build artifacts) |

## Cross-Plan Dependencies

### `260715-2011-aspnet-roslyn-analyzers` (in_progress)
- ASP.NET analyzers dùng `AspNetRoslynWorker` riêng qua `tools/common/aspnet/roslyn_adapter.py`
- Plan này tạo worker mới dưới `tools/csharp/roslyn_worker/` — **không conflict**
- ASP.NET overlays chạy SAU primary csharp, link qua `SEMANTIC_OF` → không thay đổi
- **Action**: Ensure new worker does not compete for `.cs` ownership; primary analyzer vẫn exclusive owner

### `260713-1638-framework-parser-integration` (complete)
- Completed pattern cho framework overlay integration
- Reuse: overlay ownership, detector gating, framework registry approach
- **Action**: Update `framework_registry.py` csharp profile với labels/relationships mới

### `260807-1329-parser-quality-recovery` (in_progress)
- C/C++ parse quality recovery — different language, no direct conflict
- Reuse: parse-quality contract pattern (`tools/common/parse_quality.py`)
- **Action**: Emit parse provenance compatible with quality contract

### `260821-1144-cplus-semantic-call-graph` (in_progress)
- C++ semantic call graph — different language, no conflict
- Reuse: resolved call-graph pattern, strict/conservative views

## Data Model Enhancements

### New/Enhanced Data Structures

```python
@dataclass
class PropertyDef:
    symbol_id: str
    qualified_name: str
    name: str
    type_name: str           # resolved type
    accessibility: str       # public, private, protected, internal
    has_getter: bool
    has_setter: bool
    is_static: bool
    is_virtual: bool
    is_override: bool
    is_abstract: bool
    is_auto_property: bool
    attributes: List[str]    # resolved attribute names
    file_path: str
    start_line: int
    end_line: int
    code: str
    comment: str = ""

@dataclass
class FieldDef:
    symbol_id: str
    qualified_name: str
    name: str
    type_name: str
    accessibility: str
    is_static: bool
    is_const: bool
    is_readonly: bool
    constant_value: Optional[str]
    attributes: List[str]
    file_path: str
    start_line: int
    end_line: int
    code: str
    comment: str = ""

@dataclass
class EventDef:
    symbol_id: str
    qualified_name: str
    name: str
    delegate_type: str
    accessibility: str
    is_static: bool
    attributes: List[str]
    file_path: str
    start_line: int
    end_line: int
    code: str

@dataclass
class DelegateDef:
    symbol_id: str
    qualified_name: str
    name: str
    return_type: str
    type_parameters: List[str]
    parameter_types: List[str]
    accessibility: str
    attributes: List[str]
    file_path: str
    start_line: int
    end_line: int
    code: str

@dataclass
class EnhancedTypeDef:
    # Extends existing TypeDef with:
    base_types: List[str]           # resolved
    implemented_interfaces: List[str]  # resolved
    type_parameters: List[str]
    type_constraints: Dict[str, List[str]]
    is_abstract: bool
    is_sealed: bool
    is_static: bool
    is_partial: bool
    is_record: bool
    accessibility: str
    attributes: List[str]
    xml_doc: str

@dataclass
class EnhancedFunctionDef:
    # Extends existing FunctionDef with:
    return_type: str
    type_parameters: List[str]
    is_async: bool
    is_static: bool
    is_virtual: bool
    is_override: bool
    is_abstract: bool
    is_extension_method: bool
    accessibility: str
    attributes: List[str]
    parameter_details: List[ParameterDef]
    xml_doc: str
    is_lambda: bool
    is_local_function: bool

@dataclass
class ParameterDef:
    name: str
    type_name: str
    is_optional: bool
    default_value: Optional[str]
    is_params: bool
    is_ref: bool
    is_out: bool
    is_in: bool

@dataclass
class ProjectMetadata:
    project_id: str
    project_path: str
    target_framework: str
    sdk: str
    nuget_packages: List[PackageReference]
    project_references: List[str]
    output_type: str  # Exe, Library, etc.

@dataclass
class PackageReference:
    name: str
    version: str
    is_development: bool

@dataclass
class SemanticFrameworkItem:
    kind: str            # DependencyRegistration, ConfigurationBinding, EfMapping, etc.
    name: str
    qualified_name: str
    file_path: str
    start_line: int
    end_line: int
    code: str
    properties: Dict[str, str]
    source_symbol_id: str  # links to canonical C# symbol
```

### Semantic Framework Items

**Important boundary**: ASP.NET overlays (`aspnet_core`, `aspnet_framework`) already handle DI registration, middleware pipeline, minimal API endpoints, configuration binding, and controller/action detection. Primary C# analyzer only extracts **framework-agnostic** items.

| Kind | Pattern | Properties | Owner |
| --- | --- | --- | --- |
| ~~`DependencyRegistration`~~ | ~~`AddScoped<T>`, etc.~~ | ~~service_type, lifetime~~ | **aspnet_core overlay** (skip) |
| ~~`ConfigurationBinding`~~ | ~~`IOptions<T>`, `GetSection()`~~ | ~~config_path, section_name~~ | **aspnet_core overlay** (skip) |
| `EfEntityMapping` | `DbContext`, `DbSet<T>`, `[Table]`, `[Column]`, Fluent API | entity_type, table_name, key_properties | **Primary** |
| `GrpcService` | `GrpcServiceBase<T>`, `.proto` service definitions | service_name, methods, proto_file | **Primary** |
| `SignalRHub` | `Hub<T>`, `IHubContext<T>` | hub_name, methods, client_methods | **Primary** |
| `BackgroundService` | `BackgroundService`, `IHostedService` | service_type, execute_method | **Primary** |
| `AuthPolicy` | `[Authorize]`, `AuthorizationPolicy`, `AuthenticationScheme` | policy_name, schemes, requirements | **Primary** (generic, not HTTP-specific) |
| ~~`MiddlewareRegistration`~~ | ~~`UseMiddleware<T>`, `app.Use()`~~ | ~~middleware_type, order~~ | **aspnet_core overlay** (skip) |
| ~~`MinimalApiEndpoint`~~ | ~~`MapGet`, `MapPost`, etc.~~ | ~~route, http_method~~ | **aspnet_core overlay** (skip) |
| `LoggingTelemetry` | `ILogger<T>`, `ActivitySource`, `OpenTelemetry` | logger_category, activity_name | **Primary** |
| `NuGetDependency` | `<PackageReference>` in .csproj | package_name, version, is_development | **Primary** |

## Graph Schema Enhancements

### New Node Labels

```
# Member inventory (primary C#)
Property, Field, Event, Delegate, Parameter, GenericParameter

# Project metadata (primary C#)
Project, PackageReference

# Framework-agnostic items (primary C#)
EfEntityMapping, GrpcService, SignalRHub, BackgroundService,
AuthPolicy, LoggingTelemetry

# NOTE: DependencyRegistration, ConfigurationBinding, MiddlewareRegistration,
# MinimalApiEndpoint are handled by aspnet_core overlay, NOT primary C#
```

### New Relationship Types

```
# Member containment
HAS_PROPERTY, HAS_FIELD, HAS_EVENT, HAS_DELEGATE,
HAS_PARAMETER, HAS_GENERIC_PARAMETER, HAS_ATTRIBUTE

# Type relationships (resolved)
IMPLEMENTS_INTERFACE, EXTENDS_CLASS

# Project dependencies
DEPENDS_ON_PACKAGE, REFERENCES_PROJECT

# Framework-agnostic relationships
MAPS_ENTITY, EXPOSES_GRPC, EXPOSES_HUB, RUNS_BACKGROUND,
ENFORCES_POLICY, EMITS_LOG, TRACES_ACTIVITY
```

### Enhanced Existing Relationships

```
CALLS → add: resolved=true/false, is_async, is_virtual_dispatch
CONTAINS → add: accessibility, is_static
INHERITS → resolved base types with generic arguments
```

## Phases

1. [Phase 01 — Roslyn worker contract and proof of concept](phase-01-roslyn-worker.md)
2. [Phase 02 — Primary analyzer integration and fallback](phase-02-analyzer-integration.md)
3. [Phase 03 — Full member inventory](phase-03-member-inventory.md)
4. [Phase 04 — Semantic framework items](phase-04-framework-items.md)
5. [Phase 05 — Graph schema and MCP integration](phase-05-graph-mcp.md)
6. [Phase 06 — Testing, verification, and rollout](phase-06-verification.md)

Phases 01-02 là foundation. Phases 03-04 có thể song song. Phase 05 cần 03+04. Phase 06 là final gate.

## Target File Map

| Area | Planned files |
| --- | --- |
| Roslyn worker | `code-tiny/tools/csharp/roslyn_worker/{CSharpRoslynWorker.csproj, Program.cs, MemberExtractor.cs, SemanticFrameworkExtractor.cs, ProjectMetadataExtractor.cs}` |
| Python adapter | `code-tiny/tools/csharp/roslyn_adapter.py` (new, modeled on common/aspnet/roslyn_adapter.py) |
| Enhanced analyzer | `code-tiny/tools/csharp/csharp_analyzer.py` (modified) |
| Data models | `code-tiny/tools/csharp/models.py` (new, enhanced data classes) |
| Framework extractors | `code-tiny/tools/csharp/framework_items/{ef.py, grpc.py, signalr.py, auth.py, logging.py, nuget.py}` (DI/Config/Middleware/MinimalAPI stay in aspnet_core overlay) |
| Graph integration | `code-tiny/tools/graph/writer/csharp_writer.py` (enhanced) |
| MCP | Update `code-tiny/mcp/framework_registry.py` csharp profile |
| Tests | `tests/test_csharp_roslyn_*.py`, `tests/fixtures/csharp-roslyn-*/` |

## Verification Strategy

1. Roslyn worker build, protocol, workspace/syntax modes, timeout, malformed output
2. Fallback: Tree-sitter path still works when dotnet unavailable
3. Member inventory: properties, fields, events, delegates, generics, attributes, XML docs
4. Framework items: each kind has golden test with expected graph output, confidence scoring
5. Parse provenance: every node records backend (roslyn_workspace/roslyn_syntax/tree_sitter)
6. Graph: labels, directions, relationships, no orphans, provider-neutral queries
7. MCP: csharp profile discovers new labels/relationships
8. Regression: existing C# tests remain green
9. Performance: Roslyn syntax mode < 3x Tree-sitter, workspace mode < 10x on representative corpus
10. Cache: backend-aware cache keys prevent stale payloads
11. Doctor: `dev doctor` checks .NET SDK availability

## Success Criteria

- Roslyn worker builds and produces versioned deterministic evidence
- Primary analyzer emits full member inventory (properties, fields, events, delegates)
- Resolved inheritance/implementation chains with generic arguments
- All 11 semantic framework item kinds are extracted when patterns are present
- Tree-sitter fallback works when Roslyn unavailable (with provenance metadata)
- Parse provenance reaches graph/vector consumers
- Existing C# regression tests remain green
- ASP.NET overlay tests remain green (no ownership conflict)
- MCP csharp profile advertises new labels/relationships

## Risks and Mitigations

| Risk | Mitigation |
| --- | --- |
| Roslyn worker build fails on host without .NET SDK | Tree-sitter fallback, clear error message |
| Workspace mode fails for legacy projects | Syntax mode degrades gracefully, coverage_status=partial |
| New graph labels break existing queries | Additive only, existing labels unchanged, capability contract versioned |
| Framework item extraction has false positives | Pattern-gated with confidence scores, unresolved items marked explicitly |
| Performance regression vs Tree-sitter | Benchmark gate, configurable Roslyn timeout |
| ASP.NET overlay conflict | Primary owns `.cs`, overlays use SEMANTIC_OF, no ownership change |
| Parse cache invalidation | Version bump in cache key, schema version in payload |

## Delivery Command

```text
/hi-craft plans/260910-1400-csharp-roslyn-upgrade/plan.md
```
