# Phase 06 — Testing, Verification, and Rollout

## Goal
Comprehensive testing, performance validation, and rollout verification.

## Scope

### Unit Tests

#### Roslyn Worker Tests
```
tests/test_csharp_roslyn_worker.py
├── test_worker_build          # dotnet build succeeds
├── test_protocol_version      # version match
├── test_workspace_mode        # .csproj loading
├── test_syntax_mode           # standalone .cs
├── test_timeout_enforcement   # workspace + file timeouts
├── test_malformed_input       # invalid JSON, missing fields
├── test_empty_file_list       # no files → empty result
├── test_encoding_edge_cases   # UTF-8 BOM, CP932
├── test_member_extraction     # all member types
├── test_semantic_resolution   # resolved types, calls
├── test_attribute_extraction  # attribute names, arguments
├── test_xml_doc_extraction    # summary, params, returns
└── test_project_metadata      # NuGet, references, TFM
```

#### Python Adapter Tests
```
tests/test_csharp_roslyn_adapter.py
├── test_adapter_build_lock     # thread safety
├── test_adapter_dll_resolution # runtime detection
├── test_adapter_manifest       # JSON manifest format
├── test_adapter_error_handling # worker failure
└── test_adapter_timeout        # subprocess timeout
```

#### Fallback Tests
```
tests/test_csharp_fallback.py
├── test_roslyn_first           # Roslyn available → used
├── test_tree_sitter_fallback   # dotnet missing → fallback
├── test_worker_build_failure   # build fails → fallback
├── test_per_file_fallback      # one file fails → mixed
├── test_disable_roslyn         # --disable-roslyn flag
└── test_provenance_metadata    # parse_meta correct
```

#### Member Inventory Tests
```
tests/test_csharp_member_inventory.py
├── test_properties             # auto, full, expression-bodied, indexer
├── test_fields                 # regular, const, readonly, volatile
├── test_events                 # field-like, custom
├── test_delegates              # with generics
├── test_generics               # type params, constraints
├── test_partial_classes        # merge in workspace mode
├── test_record_types           # positional, nominal
├── test_accessibility          # all modifiers
├── test_attributes             # resolved names
├── test_xml_docs               # parsed correctly
└── test_nested_types           # types within types
```

#### Framework Items Tests
```
tests/test_csharp_framework_items.py
├── test_di_registrations       # AddScoped/AddTransient/AddSingleton
├── test_configuration_binding  # IOptions, GetSection
├── test_ef_entity_mapping      # DbContext, DbSet, attributes
├── test_grpc_services          # GrpcServiceBase
├── test_signalr_hubs           # Hub<T>
├── test_background_services    # BackgroundService
├── test_auth_policies          # [Authorize], policies
├── test_middleware              # UseMiddleware<T>
├── test_minimal_api            # MapGet/MapPost
├── test_logging_telemetry      # ILogger<T>, ActivitySource
├── test_nuget_dependencies     # PackageReference
└── test_no_false_positives     # unrelated code not matched
```

### Integration Tests

```
tests/test_csharp_integration.py
├── test_full_pipeline          # scan → parse → graph → vector
├── test_incremental            # changed files + import expansion
├── test_graph_output           # all node types written
├── test_vector_output          # functions embedded
├── test_mcp_discovery          # framework items searchable
└── test_aspnet_no_conflict     # ASP.NET overlay still works
```

### Performance Benchmarks

| Metric | Target | Measurement |
| --- | --- | --- |
| Roslyn workspace mode | < 2x Tree-sitter wall time | Representative corpus (100 files) |
| Roslyn syntax mode | < 3x Tree-sitter wall time | Same corpus without project |
| Memory usage | < 2GB peak RSS | Large solution (1000 files) |
| Worker startup | < 10s | Cold start with build |
| Cache hit | < 50ms per file | Warm cache, no changes |

### Test Fixtures

```
tests/fixtures/csharp-roslyn-members/
├── Members.csproj
├── Classes.cs          # all member types
├── Generics.cs         # generic types/methods
├── Records.cs          # record types
├── PartialTypes.cs     # partial class parts
└── XmlDocs.cs          # XML documentation

tests/fixtures/csharp-roslyn-framework/
├── Framework.csproj
├── DiRegistrations.cs  # DI patterns
├── Configuration.cs    # IOptions patterns
├── EfContext.cs        # EF patterns
├── GrpcService.cs      # gRPC patterns
├── SignalRHub.cs       # SignalR patterns
├── BackgroundJob.cs    # Background service
├── AuthController.cs   # Auth patterns
├── Middleware.cs        # Middleware patterns
├── MinimalApi.cs       # Minimal API
└── appsettings.json    # Configuration file

tests/fixtures/csharp-roslyn-fallback/
├── Simple.cs           # works with Tree-sitter
└── (no .csproj)        # forces syntax mode
```

### Rollout Checklist

- [ ] Roslyn worker builds on CI (dotnet SDK available)
- [ ] All unit tests pass
- [ ] All integration tests pass
- [ ] Performance benchmarks within targets
- [ ] Parse cache version bumped
- [ ] Framework registry updated
- [ ] MCP capabilities advertised
- [ ] Full-text indexes updated
- [ ] ASP.NET overlay tests still green
- [ ] Existing C# regression tests still green
- [ ] Documentation updated (README, CLAUDE.md)
- [ ] Parse provenance reaches consumers

### Rollback Plan

If issues detected after rollout:
1. `--disable-roslyn` flag forces Tree-sitter path
2. No graph schema changes are destructive (additive only)
3. Parse cache can be cleared to force re-parse
4. Framework registry changes are backward compatible

## Acceptance Criteria

- All unit tests pass
- All integration tests pass
- Performance within targets
- No regression in existing tests
- ASP.NET overlay tests green
- Parse provenance correct for all backends
- Framework items discoverable via MCP
- Rollback path verified
