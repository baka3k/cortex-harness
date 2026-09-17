# Phase 05 — Graph Schema and MCP Integration

## Goal
Update graph schema, framework registry, and MCP capabilities để expose new C# semantic data.

## Scope

### Graph Schema Updates

#### New Node Labels (additive)
```
# Member inventory
Property, Field, Event, Delegate, Parameter, GenericParameter

# Project metadata
Project, PackageReference

# Framework items
DependencyRegistration, ConfigurationBinding, EfEntityMapping,
GrpcService, SignalRHub, BackgroundService, AuthPolicy,
MiddlewareRegistration, MinimalApiEndpoint, LoggingTelemetry
```

#### New Relationship Types (additive)
```
# Member containment
HAS_PROPERTY, HAS_FIELD, HAS_EVENT, HAS_DELEGATE,
HAS_PARAMETER, HAS_GENERIC_PARAMETER

# Type relationships (resolved)
EXTENDS_CLASS, IMPLEMENTS_INTERFACE

# Project dependencies
DEPENDS_ON_PACKAGE, REFERENCES_PROJECT

# Framework relationships
REGISTERS_SERVICE, BINDS_CONFIGURATION, MAPS_ENTITY,
EXPOSES_GRPC, EXPOSES_HUB, RUNS_BACKGROUND,
ENFORCES_POLICY, USES_MIDDLEWARE, HANDLES_ENDPOINT,
EMITS_LOG, TRACES_ACTIVITY
```

#### Enhanced Properties on Existing Nodes
```
# Function nodes
return_type, is_async, is_static, is_virtual, is_override,
is_abstract, is_extension_method, accessibility, attributes, xml_doc

# Type nodes
base_types, implemented_interfaces, type_parameters,
is_abstract, is_sealed, is_static, is_partial, is_record,
accessibility, attributes, xml_doc

# All nodes
parse_backend (roslyn_workspace | roslyn_syntax | tree_sitter)
```

### Framework Registry Update

Update `code-tiny/mcp/framework_registry.py` csharp profile:

```python
"csharp": FrameworkQueryConfig(
    name="csharp",
    aliases=frozenset({"csharp", "c#", "cs", "dotnet", ".net"}),
    labels=CSHARP_LABELS,  # new expanded label set
    relationships=CSHARP_RELATIONSHIPS,  # new expanded relationship set
    searchable_properties=CSHARP_SEARCHABLE_PROPERTIES,
    support_level="full",
    support={
        "symbols": "full",
        "calls": "full",  # upgraded from partial (resolved calls)
        "endpoints": "partial",  # Minimal API endpoints
        "database": "partial",  # EF entity mappings
    },
    features=FRAMEWORK_FEATURES | frozenset({
        "endpoint_queries",
        "framework_context_queries",
    }),
    generation_scoped=False,
),
```

New label/relationship sets:
```python
CSHARP_LABELS = GENERIC_LABELS | frozenset({
    # Members
    "Property", "Field", "Event", "Delegate", "Parameter", "GenericParameter",
    # Project
    "PackageReference",
    # Framework items
    "DependencyRegistration", "ConfigurationBinding", "EfEntityMapping",
    "GrpcService", "SignalRHub", "BackgroundService", "AuthPolicy",
    "MiddlewareRegistration", "MinimalApiEndpoint", "LoggingTelemetry",
    # Already in GENERIC_LABELS
    "HttpEndpoint", "Route", "Controller", "Service", "Repository",
    "Middleware", "Database",
})

CSHARP_RELATIONSHIPS = tuple(dict.fromkeys((
    *GENERIC_RELATIONSHIPS,
    # Member containment
    "HAS_PROPERTY", "HAS_FIELD", "HAS_EVENT", "HAS_DELEGATE",
    "HAS_PARAMETER", "HAS_GENERIC_PARAMETER",
    # Type relationships
    "EXTENDS_CLASS", "IMPLEMENTS_INTERFACE",
    # Project
    "DEPENDS_ON_PACKAGE", "REFERENCES_PROJECT",
    # Framework
    "REGISTERS_SERVICE", "BINDS_CONFIGURATION", "MAPS_ENTITY",
    "EXPOSES_GRPC", "EXPOSES_HUB", "RUNS_BACKGROUND",
    "ENFORCES_POLICY", "USES_MIDDLEWARE", "HANDLES_ENDPOINT",
    "EMITS_LOG", "TRACES_ACTIVITY",
    # Semantic bridge
    "SEMANTIC_OF",
)))

CSHARP_SEARCHABLE_PROPERTIES = tuple(dict.fromkeys((
    *GENERIC_SEARCHABLE_PROPERTIES,
    "return_type", "type_name", "delegate_type",
    "accessibility", "is_async", "is_static",
    "service_type", "implementation_type", "lifetime",
    "config_path", "section_name",
    "entity_type", "table_name",
    "route", "http_method",
    "policy_name", "middleware_type",
    "package_name", "version",
)))
```

### MCP Query Profiles

Add C#-specific query profiles:

```python
default_query_profiles = {
    "get_di_registrations": ("REGISTERS_SERVICE",),
    "get_ef_mappings": ("MAPS_ENTITY",),
    "get_api_endpoints": ("HANDLES_ENDPOINT", "EXPOSES_GRPC", "EXPOSES_HUB"),
    "get_middleware_pipeline": ("USES_MIDDLEWARE", "NEXT_MIDDLEWARE"),
    "get_auth_policies": ("ENFORCES_POLICY",),
    "get_dependencies": ("DEPENDS_ON_PACKAGE", "REFERENCES_PROJECT"),
    "get_type_hierarchy": ("EXTENDS_CLASS", "IMPLEMENTS_INTERFACE"),
    "get_member_inventory": ("HAS_PROPERTY", "HAS_FIELD", "HAS_EVENT", "HAS_DELEGATE"),
}
```

### Graph Writer Updates

Update `LanguageCodeWriter` to handle new node types:
- `write_properties()`: Property nodes with type, accessibility, modifiers
- `write_fields()`: Field nodes with type, const/readonly
- `write_events()`: Event nodes with delegate type
- `write_delegates()`: Delegate nodes with signature
- `write_framework_items()`: Generic framework item writer

Or extend `write_all()` with additional parameters for new node types.

### Full-Text Index Updates

Update `code-tiny/scripts/setup_constraints.py`:
- Add new labels to full-text indexes
- Add new searchable properties to indexes
- Ensure framework item nodes are discoverable via text search

### Semantic Graph Expansion

Update `code-tiny/mcp/semantic_graph_expansion.py`:
- Add new relationships to default traversal set for C# backend
- Support framework-aware path finding
- Handle `SEMANTIC_OF` bridges to canonical C# symbols

### Implementation Steps

1. **T01**: Define CSHARP_LABELS, CSHARP_RELATIONSHIPS, CSHARP_SEARCHABLE_PROPERTIES
2. **T02**: Update csharp profile in framework_registry.py
3. **T03**: Update graph writer for new node types
4. **T04**: Update setup_constraints.py for full-text indexes
5. **T05**: Update semantic_graph_expansion.py for new relationships
6. **T06**: Add C#-specific MCP query profiles
7. **T07**: Tests for graph schema, MCP routing, query profiles

## Acceptance Criteria

- C# profile in framework_registry advertises all new labels/relationships
- Graph writer creates nodes for all new types
- Full-text indexes include new labels and properties
- MCP search discovers framework items
- Semantic graph expansion traverses new relationships
- Query profiles return expected results
- Backward compatible: existing queries still work
- Capability contract version bumped
