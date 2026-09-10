# Phase 04 — Semantic Framework Items

## Goal
Extract **framework-agnostic** semantic patterns trong C# projects và emit chúng như first-class graph nodes.

## Important Boundary

**ASP.NET overlays already handle** (do NOT duplicate in primary):
- DI registration (AddScoped, AddTransient, AddSingleton) → `aspnet_core/resolver.py`
- Middleware pipeline (UseMiddleware, app.Use) → `aspnet_core/resolver.py`
- Minimal API endpoints (MapGet, MapPost) → `aspnet_core/resolver.py`
- Configuration binding (IOptions, GetSection) → `aspnet_core/resolver.py`
- Controller/Action/Route classification → `aspnet_core/resolver.py`
- HTTP attribute routing → `aspnet_core/resolver.py`

**Primary C# analyzer handles** (framework-agnostic):
- EF/ORM mappings
- gRPC services
- SignalR hubs
- Background services
- Auth/Authorization (generic, not HTTP-specific)
- Logging/Telemetry
- NuGet dependencies

## Scope

### Framework Item Categories

#### ~~1. Dependency Injection~~ → ASP.NET overlay (skip)
#### ~~2. Configuration Binding~~ → ASP.NET overlay (skip)

#### 3. Entity Framework / ORM (`ef.py`)

| Pattern | Detection |
| --- | --- |
| `DbContext` subclass | Base type check |
| `DbSet<T>` properties | Property type check |
| `[Table("name")]` | Attribute |
| `[Column("name")]` | Attribute |
| `[Key]`, `[PrimaryKey]` | Attribute |
| `[ForeignKey("...")]` | Attribute |
| `modelBuilder.Entity<T>()` | Fluent API invocation |
| `.HasMany()`, `.HasOne()`, `.BelongsTo()` | Fluent API chain |

Graph node: `EfEntityMapping`
Properties: `entity_type`, `table_name`, `key_properties`, `is_configured_via_fluent`
Relationships: `MAPS_ENTITY` → Type, `HAS_RELATIONSHIP` → EfEntityMapping

#### 4. gRPC Services (`grpc.py`)

| Pattern | Detection |
| --- | --- |
| `GrpcServiceBase<T>` subclass | Base type check |
| `.proto` file service definitions | File extension + syntax |
| `[Authorize]` on gRPC service | Attribute |

Graph node: `GrpcService`
Properties: `service_name`, `proto_file`, `methods`
Relationships: `EXPOSES_GRPC` → GrpcEndpoint

#### 5. SignalR Hubs (`signalr.py`)

| Pattern | Detection |
| --- | --- |
| `Hub<T>` or `Hub` subclass | Base type check |
| `IHubContext<T>` injection | Constructor parameter |
| `endpoints.MapHub<T>("/path")` | Registration pattern |

Graph node: `SignalRHub`
Properties: `hub_name`, `route`, `client_methods`
Relationships: `EXPOSES_HUB` → SignalRClient

#### 6. Background Services (`background.py`)

| Pattern | Detection |
| --- | --- |
| `BackgroundService` subclass | Base type check |
| `IHostedService` implementation | Interface check |
| `ExecuteAsync(CancellationToken)` | Override method |

Graph node: `BackgroundService`
Properties: `service_type`, `execute_method`
Relationships: `RUNS_BACKGROUND` → Type

#### 7. Authentication/Authorization (`auth.py`)

| Pattern | Detection |
| --- | --- |
| `[Authorize]` | Attribute on type/member |
| `[Authorize(Policy = "...")]` | Attribute with policy |
| `[AllowAnonymous]` | Attribute |
| `AuthorizationPolicy` builder | Builder pattern |
| `AuthenticationScheme` registration | DI pattern |

Graph node: `AuthPolicy`
Properties: `policy_name`, `schemes`, `requirements`, `scope` (type/method)
Relationships: `ENFORCES_POLICY` → Type/Function

#### ~~8. Middleware~~ → ASP.NET overlay (skip)
#### ~~9. Minimal API Endpoints~~ → ASP.NET overlay (skip)

#### 10. Logging/Telemetry (`logging.py`)

| Pattern | Detection |
| --- | --- |
| `ILogger<T>` injection | Constructor parameter |
| `ActivitySource` declaration | Field/property type |
| `[LoggerMessage]` | Attribute on method |
| `OpenTelemetry` registration | DI pattern |

Graph node: `LoggingTelemetry`
Properties: `logger_category`, `activity_name`, `instrumentation_type`
Relationships: `EMITS_LOG` → Type, `TRACES_ACTIVITY` → Type

#### 11. NuGet Dependencies (`nuget.py`)

| Pattern | Detection |
| --- | --- |
| `<PackageReference Include="..." Version="..." />` | .csproj XML parsing |
| `<PackageReference Include="..." Version="..."> <PrivateAssets>all</PrivateAssets>` | Dev dependency |

Graph node: `PackageReference`
Properties: `package_name`, `version`, `is_development`
Relationships: `DEPENDS_ON_PACKAGE` → Project

### Implementation Architecture

```
code-tiny/tools/csharp/framework_items/
├── __init__.py
├── base.py                  # Base extractor class, common patterns
├── ef.py                    # Entity Framework
├── grpc.py                  # gRPC services
├── signalr.py               # SignalR hubs
├── auth.py                  # Auth/Authz (generic, not HTTP-specific)
├── logging.py               # Logging/Telemetry
└── nuget.py                 # NuGet dependencies (XML parsing, not Roslyn)
```

Note: DI, Config, Middleware, Minimal API extractors are NOT created here — they live in `aspnet_core/resolver.py`.

Each extractor:
1. Receives Roslyn evidence (types, members, calls, attributes)
2. Pattern-matches against known framework signatures
3. Emits `SemanticFrameworkItem` records
4. Links back to canonical C# symbols via `source_symbol_id`

### Roslyn Worker Support

Worker needs to emit additional evidence for framework extraction:
- Full attribute argument values (not just names)
- Invocation target resolution (for DI registration calls)
- String literal arguments (for route patterns, config keys)
- Generic type arguments (for `AddScoped<TService, TImpl>`)

### Graph Integration

- Framework items written as separate node labels
- Linked to canonical C# symbols via `SEMANTIC_OF`
- Framework items are additive — do not compete with primary C# nodes
- Generation-scoped writes (same pattern as ASP.NET overlays)

### Implementation Steps

1. **T01**: Create `framework_items/` package with base class
2. **T02**: Implement each extractor (di, config, ef, grpc, signalr, auth, middleware, minimal_api, logging, nuget)
3. **T03**: Update Roslyn worker to emit framework-supporting evidence
4. **T04**: Integrate extractors into `csharp_analyzer.py` pipeline
5. **T05**: Update graph writer for new node labels and relationships
6. **T06**: Golden tests for each framework item kind
7. **T07**: Integration test with mixed framework patterns

## Acceptance Criteria

- All 11 framework item kinds are detected when patterns present
- False positive rate < 5% on representative corpus
- Each framework item links back to canonical C# symbol
- NuGet dependencies extracted from .csproj XML
- Graph queries can traverse framework items
- No conflict with ASP.NET overlay framework items
- Existing tests remain green
