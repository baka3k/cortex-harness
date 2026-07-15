# Phase 04: Implement the ASP.NET Core Analyzer

## Context

Reconstruct hosting across Program/Startup styles, service registration, middleware order/branches, endpoint routing, MVC/Web API, Razor Pages, Minimal APIs, filters, binding/validation, views, and appsettings.

## Requirements

- Detect Core modules without activating on unrelated modern C#.
- Support Startup-based and top-level minimal hosting within the Phase 01 matrix.
- Preserve middleware/filter order, branches, terminal behavior, route metadata, and binding evidence.
- Extract controllers/actions, Razor Pages/handlers, Minimal APIs, DI, config, results, and views.
- Emit the same unified contract as the Framework analyzer.

## Architecture

```text
detector -> SDK/project/package/module inventory
Roslyn -> host, services, middleware, endpoints, symbols/calls
Razor -> pages/views/layouts/partials/tag helpers/components
JSON -> appsettings/environment configuration
resolver -> request, endpoint, DI, validation, result, view graph
pipeline -> deterministic result + dependency index
```

## Related Files

- `code-tiny/tools/aspnet_core/` (new)
- `code-tiny/tools/common/aspnet/`
- `code-tiny/tools/csharp/csharp_analyzer.py` (anchor contract only)
- `code-tiny/tools/spring/` (DI/controller/config overlay reference)
- `tests/fixtures/aspnet-core-application/` (new)

## Implementation Steps

1. Detect modules from Web SDK/framework references, targets, Program/Startup, appsettings, Razor artifacts, and ASP.NET Core namespaces/packages.
2. Discover `.cs`, project/solution metadata, `.cshtml`/`.razor`, `appsettings*.json`, launch/config metadata, and relevant view artifacts.
3. Extract host bootstrap, configuration providers, services, DI lifetimes/mappings, and injection evidence.
4. Reconstruct ordered `Use`/`Run`/`Map`/`MapWhen` middleware, branches, terminal behavior, auth/authz, exception/static/session/CORS, and mutation evidence.
5. Extract conventional/attribute/endpoint routes, controllers/actions, filters, binding, validation, results, redirects, and views.
6. Extract Razor Pages, `@page` routes, PageModel handlers, verbs/names, conventions, binding/validation, layouts, partials, tag helpers, components, and model declarations.
7. Extract Minimal API mappings/groups, methods/templates, handlers/lambdas, parameters, endpoint metadata, filters/policies, and results.
8. Parse appsettings/environment JSON with hierarchy, precedence provenance, consumer links, and redaction.
9. Resolve endpoints, ordered middleware/filter paths, DI consumers, validation, results, views/JSON/redirects, and config reads.
10. Build code/config/Razor/project dependencies and selected/changed/deleted closure.
11. Add CLI dry-run/JSON/diagnostics, shared flags, failure policy, cache, and no-service imports.
12. Add golden tests for Startup/minimal hosting, middleware branching, controllers, Razor Pages, Minimal APIs, DI, config, malformed inputs, dynamic endpoints, and determinism.

## Todo

- [ ] Detector and artifact inventory are reliable.
- [ ] Host, DI, and config are reconstructed.
- [ ] Middleware/routing/filter order is reconstructed.
- [ ] Controller, Razor Page, Minimal API, validation, result, and view semantics are reconstructed.
- [ ] Incremental dependencies/deletion are complete.
- [ ] CLI, cache, golden, failure, and deterministic tests pass.

## Risks

- Fluent extensions/custom wrappers can hide pipeline meaning.
- Minimal API composition may use non-constant dynamic values.
- Razor generation/view discovery varies by SDK version.

## Success Criteria

- The fixture traces endpoints through ordered middleware/routing/filter/binding/validation to handlers and results.
- DI/config dependencies are queryable with lifetime, provenance, redaction, and evidence.
- Dynamic/unsupported constructs remain explicit partial facts without invented links.

