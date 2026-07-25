# Phase 08: Framework Overlay Deep Context

## Context

Framework understanding is the user's priority. Existing overlays already emit
valuable graph facts, but their config, module, runtime, route, security,
persistence, UI, and deployment coverage is uneven. The live registry contains
12 overlays, including five not listed in the user-provided table.

## Requirements

- Audit all 12 overlays against the framework special-file matrix.
- Preserve framework-specific semantic labels and relationships.
- Attach each framework instance and special file to canonical modules.
- Normalize common context dimensions without flattening framework semantics.
- Make capability depth and missing evidence truthful.

## Architecture

Introduce a shared `FrameworkInstance` compatibility layer with:

- framework/version evidence;
- owning module and prerequisite parser;
- entrypoints and bootstrap/lifecycle facts;
- config profiles and safe configuration keys;
- endpoints/routes/pages/services;
- security/authentication/authorization;
- persistence/migrations;
- messaging/jobs/events;
- UI/templates/resources;
- deployment/runtime descriptors;
- diagnostics and ingestion provenance.

Specialized overlay nodes remain the source of truth. `FrameworkInstance` links
to them and supports aggregate MCP queries.

## Related Files

- `code-tiny/tools/spring/`
- `code-tiny/tools/servlet_jsp/`
- `code-tiny/tools/mybatis/`
- `code-tiny/tools/struts/`
- `code-tiny/tools/flutter/`
- `code-tiny/tools/aspnet_core/`
- `code-tiny/tools/aspnet_framework/`
- `code-tiny/tools/web_framework/`
- `code-tiny/tools/database_schema/`
- `code-tiny/tools/graph/writer/`
- `code-tiny/tools/sync/incremental_sync.py`
- `code-tiny/mcp/framework_registry.py`
- New `tests/test_framework_special_file_coverage.py`
- Existing framework fixture, graph, MCP, and incremental tests

## Implementation Steps

1. Add a framework coverage registry for all 12 overlays with expected evidence,
   special files, parse depth, semantic dimensions, and fixture IDs.
2. Deepen Spring config/autoconfiguration/profile/security/messaging/persistence
   descriptor coverage and module attachment.
3. Deepen Servlet/JSP descriptors, tag libraries, container/vendor config,
   mappings, lifecycle, security, and view dependencies.
4. Deepen MyBatis main/generator config, mapper/interface binding, dynamic SQL,
   plugin/type-handler/cache/data-source context, and module attachment.
5. Deepen Struts config includes/properties/validation/conversion/templates,
   action/interceptor/result flows, and module attachment.
6. Deepen Flutter pub/host-platform/assets/localization/routes/platform-channel
   context.
7. Deepen ASP.NET Core and Framework build/runtime/config/UI/service/security/
   persistence coverage while sharing .NET project metadata.
8. Separate FastAPI and Django facts within the existing combined overlay;
   capture their distinct bootstrap, settings, routes, middleware, migrations,
   and deployment entrypoints.
9. Deepen Express and Laravel package/bootstrap/config/route/middleware/
   persistence/UI/job context.
10. Deepen SQL and PL/SQL overlay migration/schema/lineage/deployment context.
11. Link specialized facts to `FrameworkInstance` and `ProjectModule` using
    stable IDs without duplicating canonical symbols/endpoints.
12. Add active-generation, deletion, malformed-config, secret-redaction, and
    mixed-framework fixtures.

## Todo

- [ ] All 12 overlays have fixture-backed coverage records.
- [ ] Framework instances attach to canonical modules.
- [ ] Common context dimensions are queryable.
- [ ] Specialized graph semantics remain intact.
- [ ] Missing expected files and partial depth are reported honestly.

## Risks

- A project can use several frameworks in one module. Framework detection and
  context must remain non-exclusive.
- Framework versions can be indirect through BOMs/catalogs/locks. Report the
  resolution source and avoid guessed versions.
- Environment-specific config often contains secrets. Store key names, profiles,
  sources, and redacted summaries only.
- Combined overlays such as FastAPI/Django can blur semantics. Preserve exact
  framework identity in every fact.

## Success Criteria

- Every registered overlay returns a coherent framework context from its fixture.
- Mixed modules expose multiple framework instances without duplicate canonical
  source facts.
- Config, endpoints, security, persistence, messaging/jobs, UI/resources, and
  deployment sections report supported/partial/unavailable independently.
- Existing specialized traversal and active-generation behavior remains green.

