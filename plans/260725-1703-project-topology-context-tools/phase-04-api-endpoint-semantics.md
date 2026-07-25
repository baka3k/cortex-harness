# Phase 04: Public API and Endpoint Semantics

## Context

The graph contains canonical symbols and several endpoint-specific overlays, but
language visibility is not normalized and endpoint labels vary by framework.
The requested inventory tools need truthful, comparable facts.

## Requirements

- Extract source-level visibility/export evidence during parsing.
- Attach symbols and endpoints to canonical modules.
- Normalize existing REST/controller/route facts without replacing them.
- Add protobuf service/RPC extraction for gRPC inventories.
- Keep inference opt-in and confidence/evidence explicit.

## Architecture

Add normalized symbol properties:

- `visibility`
- `is_public_api`
- `visibility_source`
- `export_evidence`
- `signature` where currently absent
- `module_id`

Public API classification is language-aware and runs at extraction/write time.
MCP may filter these facts but must not reinterpret source code.

Add an endpoint normalization adapter that maps specialized graph facts into an
`EndpointFact` response while preserving the original node ID/labels.

## Related Files

- `code-tiny/tools/java/java_analyzer.py`
- `code-tiny/tools/kotlin/kotlin_analyzer.py`
- `code-tiny/tools/android/android_kotlin_analyzer.py`
- `code-tiny/tools/android/android_java_analyzer.py`
- `code-tiny/tools/cplus/cplus_analyzer.py`
- Shared graph writers for symbol property propagation
- Existing web/Spring/Servlet/Struts/ASP.NET endpoint overlays
- New protobuf parser under `code-tiny/tools/project_topology/parsers/` or a
  focused `code-tiny/tools/protobuf/` package
- New `tests/test_public_api_semantics.py`
- New `tests/test_endpoint_normalization.py`
- New `tests/test_protobuf_endpoint_parser.py`

## Implementation Steps

1. Define language-specific public API rules and fixture expectations.
2. Extend Java parsing to retain modifiers and correctly handle public
   top-level/nested types, constructors, methods, fields, annotations, enums,
   records, and interface/annotation implicit visibility.
3. Extend Kotlin parsing to retain explicit/default visibility, internal/private
   declarations, constructors/properties/type aliases, companion/object members,
   and generated accessor policy.
4. Reuse the same visibility semantics in Android Java/Kotlin analyzers.
5. Extend C/C++ facts with explicit export/linkage evidence. Classify
   public-header declarations only as inferred and require opt-in at query time.
6. Attach canonical symbols to modules using normalized source roots and
   descriptor/source-set evidence.
7. Build endpoint normalization mappings for existing `ApiEndpoint`,
   `HttpEndpoint`, route/controller, Servlet/JSP, Struts, ASP.NET, and related
   facts. Normalize method/path/framework/handler/security fields without
   deleting specialized properties.
8. Parse `.proto` packages, messages referenced by services, service
   declarations, unary/client/server/bidirectional streaming RPC methods, and
   statically declared HTTP annotations.
9. Write `GrpcService`/`GrpcEndpoint` facts, handler/type links when resolvable,
   and module ownership.
10. Add deduplication tests where an endpoint has both generic and
    framework-specific labels.

## Todo

- [ ] Java/Kotlin visibility rules are fixture-backed.
- [ ] Android uses the same public API contract.
- [ ] C/C++ inferred APIs are excluded by default.
- [ ] Existing endpoint kinds normalize without duplication.
- [ ] Protobuf services/RPC methods produce gRPC endpoint facts.
- [ ] Symbols/endpoints link to canonical modules.

## Risks

- Kotlin default visibility is public but `internal` is module-scoped; module
  ownership must be established before final classification.
- Java synthetic/compiler-generated members are unavailable in source parsing;
  document source-level semantics.
- Framework overlays can create multiple nodes for one route. Prefer stable
  source node identity and response deduplication by node ID/protocol key.
- Protobuf imports and code-generation options can be dynamic or external.
  Resolve only local static imports and report unresolved types.

## Success Criteria

- Strict public API results include only language-defined public/exported facts.
- Every result explains classification evidence and confidence.
- Endpoint normalization returns REST/routes/controllers and gRPC with one
  stable response schema.
- Existing specialized traversal and framework tests remain green.

