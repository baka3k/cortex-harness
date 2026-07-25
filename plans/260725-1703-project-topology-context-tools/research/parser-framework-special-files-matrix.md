---
type: research
date: 2026-07-25
---

# Research: Parser and Framework Special-File Matrix

## Summary

This matrix extends the project-topology plan across every registered primary
analyzer and framework overlay. It identifies files that carry architecture,
module, dependency, runtime, routing, security, persistence, UI, code-generation,
or deployment meaning beyond ordinary source symbols.

“Special file” does not mean universally mandatory. Some ecosystems have a
required package descriptor (`Cargo.toml`, `pubspec.yaml`, `.csproj`), while
others have no language-level project file (C/C++, Python, COBOL, SQL). The
implementation must classify each file role as:

- `identity`: defines a package/project/module;
- `topology`: declares modules, targets, workspaces, or project references;
- `dependency`: declares or locks dependencies;
- `configuration`: runtime/build/tool configuration;
- `framework`: proves framework use or defines framework behavior;
- `interface`: API/schema/IDL or exported surface;
- `resource`: UI, localization, templates, assets, or platform metadata;
- `deployment`: hosting/container/server/cloud metadata;
- `generated`: generated state that may inform resolution but is not canonical;
- `secret-bearing`: parse keys/structure only and redact values.

## Verified Registry Baseline

The indexed `tests/test_common_analyzer_registry.py` confirms 22 primary
analyzers and 12 framework overlays. `owner_manifest.py` currently assigns most
primary ownership by source extension. Structured searches also confirmed:

- Android owns Gradle and Android-context XML.
- Flutter detection reads `pubspec.yaml`.
- TypeScript project detection reads `package.json`.
- C/C++ uses `CMakeLists.txt` and `Makefile` to bootstrap compile commands.
- ASP.NET overlays inspect `.csproj`, `web.config`, and appsettings artifacts.
- VB routing recognizes `.vbp`, `.vbproj`, `.sln`, `.vbs`, `.wsf`, `.hta`, and
  Classic ASP.
- Spring/MyBatis/Servlet/Struts detectors already inspect selected build and XML
  descriptors.
- Exact graph searches found no current code references for `Cargo.toml`,
  `go.mod`, `composer.json`, or `tsconfig.json`; these are priority gaps, not
  claimed current capabilities.

## Primary Analyzer Matrix

### Android

| Category | Do-not-miss files |
| --- | --- |
| Identity/topology | `AndroidManifest.xml`, `settings.gradle(.kts)`, `build.gradle(.kts)`, `gradle.properties`, `gradle/libs.versions.toml` |
| Module/dependency | Gradle project dependencies, dynamic-feature declarations, version catalogs, source sets, included builds |
| Runtime/security | `network_security_config.xml`, backup/data-extraction rules, `proguard-rules.pro`, `consumer-rules.pro` |
| Resources/interfaces | `res/values/*.xml`, layouts, navigation, menus, preferences, file paths, XML configs, AIDL, `.proto` |
| Target facts | app/library/dynamic-feature/test module, variants/source sets, permissions/components/deep links, resources, internal/external dependencies |

### COBOL

| Category | Do-not-miss files |
| --- | --- |
| Source/interface | `.cbl`, `.cob`, `.cpy`, `.copy`, copybook directories and search-path configuration |
| Runtime/job flow | JCL `.jcl`, procedure libraries, CICS maps/BMS `.bms`, DB2/SQL precompiler inputs where present |
| Build/dialect | `Makefile`, Ant/Maven/Gradle wrappers used by modernization projects, compiler option/directive files, dialect/source-format configuration |
| Target facts | program/copybook ownership, copy resolution, job-step/program invocation, CICS/DB2 interfaces, dialect and unresolved include evidence |
| Note | COBOL has no universal project manifest; handlers must be vendor/profile adapters rather than one invented standard |

### C and C++

| Category | Do-not-miss files |
| --- | --- |
| Canonical analysis | `compile_commands.json` |
| Identity/topology | `CMakeLists.txt`, `Makefile`/`makefile`, `meson.build`, `configure.ac`, `.vcxproj`, `.sln`, module maps |
| Dependency | `conanfile.py/txt`, `vcpkg.json`, `vcpkg-configuration.json`, pkg-config `.pc`, lockfiles |
| Platform/resources | `.rc`, `.rc2`, `.def`, manifests, resource headers, precompiled-header configuration |
| Target facts | targets/libraries/executables, include paths, compile definitions/options, link dependencies, exported symbols, generated sources |

### C#

| Category | Do-not-miss files |
| --- | --- |
| Identity/topology | `.sln`, `.slnx`, `.csproj`, `Directory.Build.props`, `Directory.Build.targets` |
| SDK/dependency | `global.json`, `Directory.Packages.props`, `NuGet.Config`, `packages.config`, `packages.lock.json` |
| Runtime | `appsettings*.json`, `launchSettings.json`, `app.config`, `web.config` |
| Interfaces/resources | `.proto`, OpenAPI files, Razor `.cshtml`, Blazor `.razor`, `.resx`, EF migration metadata |
| Target facts | solution/project references, target frameworks, package refs, analyzers/generators, runtime profiles, endpoints, resources |

### Dart

| Category | Do-not-miss files |
| --- | --- |
| Identity/dependency | `pubspec.yaml`, `pubspec.lock` |
| Analysis/build | `analysis_options.yaml`, `build.yaml`, `dart_test.yaml` |
| Generated resolution | `.dart_tool/package_config.json` (resolution evidence only) |
| Interfaces/resources | `.proto`, generated-part relationships, assets declared in `pubspec.yaml` |
| Target facts | package/workspace identity, SDK constraints, dependencies/dev-dependencies, exports/parts, builders, lints |

### Delphi/Object Pascal

| Category | Do-not-miss files |
| --- | --- |
| Identity/topology | `.dpr`, `.dproj`, `.groupproj`, `.dpk` |
| Source/interface | `.pas`, `.inc`, package/unit uses clauses |
| UI/resources | `.dfm`, `.fmx`, `.res`, `.rc` |
| Build/tooling | `.cfg`, `.dof`, platform/configuration properties in project files |
| Target facts | projects/groups/packages, units/forms, package requirements, platform targets, resources, event-handler bindings |

### Go

| Category | Do-not-miss files |
| --- | --- |
| Identity/dependency | `go.mod`, `go.sum`, `go.work`, `go.work.sum`, `vendor/modules.txt` |
| Build/config | build tags in source, `Makefile`, `tools.go`, code-generation directives |
| Interfaces/resources | `.proto`, OpenAPI files, `//go:embed` targets |
| Target facts | module/workspace/package topology, replace/exclude/retract directives, dependencies, build constraints, embedded resources |

### Java

| Category | Do-not-miss files |
| --- | --- |
| Identity/topology | `pom.xml`, `settings.gradle(.kts)`, `build.gradle(.kts)`, `module-info.java`, `MANIFEST.MF` |
| Dependency/build | `gradle.properties`, `gradle/libs.versions.toml`, Maven parent/BOM/plugin/profile data, wrapper properties |
| Interfaces | `.proto`, OpenAPI files, annotation processors/service provider files under `META-INF/services/` |
| Runtime/framework | application configs are delegated to framework overlays but attached to the owning Java module |
| Target facts | Maven/Gradle/JPMS modules, coordinates, source sets, dependencies, services, public APIs, generated-code provenance |

### JavaScript

| Category | Do-not-miss files |
| --- | --- |
| Identity/dependency | `package.json`, npm/yarn/pnpm/bun lockfiles, workspace declarations |
| Module/build | `jsconfig.json`, Babel config, Webpack/Rollup/Vite/Parcel configs |
| Runtime | `.env*` names/keys only, runtime config modules, `nodemon.json`, process-manager configs |
| Interfaces/routes | OpenAPI/GraphQL schemas, route modules, serverless deployment descriptors |
| Target facts | package/workspace modules, ESM/CommonJS mode, exports/imports maps, scripts, dependencies, aliases, entrypoints |

### Kotlin

| Category | Do-not-miss files |
| --- | --- |
| Identity/topology | `settings.gradle(.kts)`, `build.gradle(.kts)`, `pom.xml` when used |
| Dependency/build | `gradle.properties`, `gradle/libs.versions.toml`, Kotlin Multiplatform targets/source sets |
| Interfaces | `.proto`, Kotlin serialization schemas/config, service provider metadata |
| Platform | Android files delegate to Android analysis; JVM/Native/JS target metadata remains Kotlin module context |
| Target facts | JVM/KMP modules, target/source-set graph, expect/actual links, dependencies, public/internal API boundaries |

### Perl 5

| Category | Do-not-miss files |
| --- | --- |
| Identity/build | `Makefile.PL`, `Build.PL`, `dist.ini` |
| Dependency/metadata | `cpanfile`, `cpanfile.snapshot`, `META.json`, `META.yml`, `MYMETA.*` |
| Distribution | `MANIFEST`, `MANIFEST.SKIP`, `Changes`, `LICENSE` |
| Source/tests | `.pm`, `.pl`, `.t`, local library paths |
| Target facts | distribution/module identity, prerequisites by phase, provides map, scripts, test topology, generated vs canonical metadata |

### PHP

| Category | Do-not-miss files |
| --- | --- |
| Identity/dependency | `composer.json`, `composer.lock`, `installed.json` as generated evidence |
| Autoload/module | PSR-0/PSR-4/classmap/files autoload rules |
| Tooling | `phpunit.xml(.dist)`, `phpstan.neon`, `psalm.xml`, PHP-CS-Fixer/Pint configs |
| Runtime/framework | `.env.example` and key names only; framework config/routes delegated to overlays |
| Target facts | Composer packages, autoload namespaces, scripts/plugins, dependencies, runtime/tool profiles |

### PL/SQL

| Category | Do-not-miss files |
| --- | --- |
| Source/interface | package specs/bodies, procedures, functions, triggers, types, synonyms, grants |
| Orchestration | SQL*Plus install/upgrade scripts and `@`/`@@` include chains |
| Migration | Liquibase changelogs, Flyway naming/config, vendor deployment manifests |
| Runtime | connection/profile files are secret-bearing; store references/keys only |
| Target facts | schema/module ownership, deployment order, package public APIs, DB dependencies, grants, migration lineage |

### Python

| Category | Do-not-miss files |
| --- | --- |
| Identity/build | `pyproject.toml`, `setup.py`, `setup.cfg` |
| Dependency/lock | `requirements*.txt`, `Pipfile`, `Pipfile.lock`, `poetry.lock`, `uv.lock`, `pdm.lock` |
| Workspace/tooling | `tox.ini`, `pytest.ini`, `mypy.ini`, `.python-version`, Ruff/coverage config |
| Framework/runtime | `manage.py`, Django settings/URLs, Alembic config, `.env*` key names only |
| Target facts | packages/distributions, dependency groups/extras, entry points, workspace members, framework evidence, public exports via `__all__` |

### Rust

| Category | Do-not-miss files |
| --- | --- |
| Identity/topology | `Cargo.toml`, workspace members/excludes, virtual manifests |
| Dependency/lock | `Cargo.lock`, `[patch]`, `[replace]`, target-specific dependencies |
| Toolchain/build | `rust-toolchain.toml`, `.cargo/config.toml`, `build.rs` |
| Interfaces/resources | features, crate types, examples/benches/tests, FFI headers/config |
| Target facts | workspace/crate/target graph, features, build dependencies, target triples, exported/public items, build-script provenance |

### SQL

| Category | Do-not-miss files |
| --- | --- |
| Schema/source | DDL/DML scripts, views, procedures where dialect permits, seed/reference data |
| Migration | Flyway migrations/config, Liquibase changelogs/properties, dbmate/Goose/Knex/Prisma migration layouts |
| Analytics | `dbt_project.yml`, `packages.yml`, `profiles.yml` references, model `schema.yml` |
| Orchestration | include/source directives and ordered install scripts |
| Target facts | dialect, schemas/objects, read/write/reference edges, migration order/checksums, dbt model/test/source lineage |

### Swift

| Category | Do-not-miss files |
| --- | --- |
| Identity/dependency | `Package.swift`, `Package.resolved`, `Podfile`, `Podfile.lock`, `Cartfile` |
| Xcode topology | `.xcodeproj/project.pbxproj`, `.xcworkspace`, `.xcconfig` |
| Platform/runtime | `Info.plist`, entitlements, privacy manifest, bridging headers |
| UI/resources | storyboards, XIBs, asset catalogs, localization, Core Data models |
| Target facts | packages/products/targets, Xcode targets/configurations, dependencies, capabilities, entrypoints, UI/resource links |

### TypeScript/TSX

| Category | Do-not-miss files |
| --- | --- |
| Identity/dependency | `package.json`, lockfiles, workspace declarations |
| Compiler/topology | `tsconfig.json`, extended configs, project references, path aliases |
| Build/framework | Vite/Webpack/Rollup/Next/Nest/Angular configs, React Native/Expo app config |
| Interfaces | `.d.ts`, OpenAPI/GraphQL/protobuf schemas, generated clients |
| Target facts | package/workspace/module graph, TS project references, aliases, frontend/backend role, routes/controllers, API calls |

### VB.NET

| Category | Do-not-miss files |
| --- | --- |
| Identity/topology | `.sln`, `.slnx`, `.vbproj`, `Directory.Build.props/targets` |
| Dependency | `packages.config`, `packages.lock.json`, `Directory.Packages.props`, `NuGet.Config` |
| Runtime | `app.config`, `web.config`, `appsettings*.json`, `My Project/Application.myapp`, settings/resources |
| UI/resources | `.resx`, WinForms/WPF designer/XAML files |
| Target facts | solution/project references, target frameworks, root namespaces, startup objects, resources/settings, public APIs |

### Visual Basic 6

| Category | Do-not-miss files |
| --- | --- |
| Identity/topology | `.vbp`, `.vbg`, `.vbw` |
| Source/UI | `.bas`, `.cls`, `.frm`, `.frx`, `.ctl`, `.ctx`, `.dsr`, `.dsx` |
| Resources/dependency | `.res`, project references/components/object declarations |
| Target facts | projects/groups, forms/controls/events, COM references, startup object, binary compatibility, resources |

### VBA

| Category | Do-not-miss files |
| --- | --- |
| Exported source | `.bas`, `.cls`, `.frm`, `.frx` |
| Host containers | macro-enabled Office files and `vbaProject.bin` when extraction support is available |
| Project metadata | VBA project streams, references, host document/module type |
| Target facts | host application/document, modules/forms, references, event entrypoints, public macros, extraction diagnostics |

### VBScript and Classic ASP

| Category | Do-not-miss files |
| --- | --- |
| Source/host | `.vbs`, `.wsf`, `.hta`, `.asp`, `global.asa` |
| Deployment/runtime | IIS `web.config`, script host/job metadata, include directives |
| Interfaces | COM object creation, request/session/application usage, ASP routes/includes |
| Target facts | host type, include graph, page/handler entrypoints, COM dependencies, global lifecycle events, server config |

## Framework Overlay Matrix

The live registry includes the seven overlays listed by the user plus five
additional overlays. All twelve must participate in the same special-file and
MCP capability contract.

### Spring

- `pom.xml`, Gradle/settings/version catalogs.
- `application*.properties|yml|yaml|json`, `bootstrap*` where used.
- XML bean/application-context files.
- `META-INF/spring.factories`,
  `META-INF/spring/org.springframework.boot.autoconfigure.AutoConfiguration.imports`,
  service loader metadata.
- Logging, messaging, cache, security, JPA/Flyway/Liquibase configuration.
- Query targets: beans/configurations, controllers/endpoints, security rules,
  messaging, scheduled/async work, transactions, repositories/entities, profiles.

### Servlet/JSP

- `WEB-INF/web.xml`, `META-INF/web-fragment.xml`.
- JSP/JSPX/JSPF, tag files, TLD descriptors.
- Servlet container/vendor descriptors such as context and deployment XML when
  present.
- Resource bundles, welcome/error pages, filters/listeners/security constraints.
- Query targets: servlet/filter/listener lifecycle, URL mappings, JSP/tag
  dependencies, security, forwards/includes, active generation.

### MyBatis

- `mybatis-config.xml`, mapper XML files, mapper interfaces/annotations.
- `mybatis-generator-config.xml`, properties referenced by config/mappers.
- Spring Boot `mybatis.*`/`mybatis-plus.*` configuration.
- Query targets: namespace/interface binding, statements/fragments/result maps,
  dynamic SQL, table read/write, cache/type-handler/plugin/data-source context.

### Struts

- `struts.xml`, included Struts XML, `struts.properties`, `web.xml`.
- `validators.xml`, `*-validation.xml`, conversion properties.
- JSP/Freemarker/Velocity result templates and message bundles.
- Query targets: packages/namespaces/actions, interceptors/stacks, results,
  exception mappings, validation/conversion, redirects/chains.

### Flutter

- `pubspec.yaml`, `pubspec.lock`, `analysis_options.yaml`, `build.yaml`.
- `lib/main.dart`, router/navigation configuration, generated registrations.
- `l10n.yaml`, ARB localization, assets/fonts declared in pubspec.
- Android/iOS/macOS/web/Windows/Linux host project descriptors.
- Query targets: app/package/modules, screens/routes/navigation, widgets/state,
  platform channels, assets/localization, plugins.

### ASP.NET Core

- `.sln/.csproj`, `Directory.Build.*`, `global.json`, package configuration.
- `Program.cs`, `Startup.cs`, minimal API mappings, controllers.
- `appsettings*.json`, `launchSettings.json`, deployment `web.config`.
- Razor/Blazor files, `.proto`, OpenAPI, EF migrations, user-secret IDs
  (identifier only).
- Query targets: middleware/DI/options/config profiles, endpoints/controllers,
  authorization, gRPC, hosted services, EF/repositories, Razor/Blazor UI.

### ASP.NET Framework

- `.sln/.csproj`, `packages.config`, `web.config` plus transforms.
- `Global.asax`, `RouteConfig`, `WebApiConfig`, `BundleConfig`.
- `.aspx/.ascx/.master` plus code-behind/designer, `.asmx`, `.svc`.
- WCF/serviceModel, membership/authentication/authorization, EDMX.
- Query targets: application lifecycle, routes/controllers/pages/controls,
  handlers/modules, WCF/ASMX services, config transforms, data access.

### FastAPI and Django

- Packaging/dependency files from Python.
- FastAPI application factories, router modules, dependency providers, OpenAPI
  customization.
- Django `manage.py`, settings modules, `urls.py`, `wsgi.py`, `asgi.py`,
  `apps.py`, migrations, templates, static config.
- Alembic/Django migration metadata and Pydantic/settings configuration.
- Query targets: routes, dependencies/middleware, settings/profiles, models,
  migrations, templates, ASGI/WSGI entrypoints.

### Express.js

- `package.json`, lock/workspace files, JS/TS compiler/build configs.
- app/server entrypoints, router modules, middleware, template/static config.
- OpenAPI/GraphQL schemas and deployment/serverless descriptors.
- Query targets: routers/routes, middleware ordering, handlers/services, API
  calls, error handling, security/session configuration.

### Laravel

- `composer.json/lock`, `artisan`, `bootstrap/app.php`.
- `config/*.php`, `routes/*.php`, service providers.
- `.env.example` and environment key names only.
- database migrations/seeders/factories, Blade templates, queues/events.
- Query targets: routes/controllers/middleware, service container/providers,
  Eloquent models/migrations, policies/guards, jobs/events/views.

### Database SQL

- Flyway/Liquibase/dbt and other migration/project files listed in the SQL
  primary matrix.
- Query targets: schema objects, migrations, lineage, read/write dependencies,
  sources/tests, deployment order and drift evidence.

### Database PL/SQL

- Package specs/bodies, SQL*Plus orchestration, grants/synonyms/triggers, migration
  descriptors.
- Query targets: public package API, schema dependencies, callers, deployment
  ordering, security grants, table/view/procedure lineage.

## Cross-Cutting Graph Contract

Each recognized special file should produce or enrich:

- `ProjectModule`
- `ProjectDescriptor` or specialized compatible label
- `FrameworkInstance`
- `ConfigurationProfile`
- `Dependency`
- `BuildTarget`
- `ApiEndpoint`/`GrpcEndpoint`/schema interface facts
- `Resource`
- `DeploymentDescriptor`
- `AnalysisDiagnostic`

Core relationships:

- `ProjectModule-[:HAS_DESCRIPTOR]->ProjectDescriptor`
- `ProjectModule-[:USES_FRAMEWORK]->FrameworkInstance`
- `ProjectModule-[:DEPENDS_ON]->ProjectModule|Dependency`
- `ProjectModule-[:HAS_CONFIG]->ConfigurationProfile`
- `ProjectModule-[:DECLARES_TARGET]->BuildTarget`
- `ProjectModule-[:EXPOSES_API]->symbol|endpoint`
- `ProjectDescriptor-[:CONFIGURES|GENERATES|LOCKS|IMPORTS]->fact`
- framework-specific relationships remain authoritative and are not flattened
  away.

## MCP Query Additions

In addition to the four tools already planned, add:

### `get_project_special_files`

Returns special files by project/module, role, parser/overlay owner, parse depth,
framework evidence, status, diagnostics, and safe summary. It must distinguish
canonical, generated, missing-expected, and secret-bearing files.

### `get_framework_context`

Returns detected framework instances with evidence, versions when statically
known, owning modules, entrypoints, config profiles, routes/endpoints, security,
persistence, messaging/jobs, UI/templates/resources, dependencies, diagnostics,
and ingestion provenance.

Extend existing tools:

- `get_project_modules`: include descriptors, build targets, workspaces, and
  dependency manifests.
- `get_public_apis`: apply language-specific export rules across all primaries.
- `get_endpoints`: normalize framework, gRPC, service, page, and route facts.
- `get_module_architecture_summary`: include special-file coverage and framework
  context counts/samples.

## Capability Rules

- A recognized filename alone is detection evidence, not deep support.
- `parse_depth` is `identity`, `topology`, `dependency`, `semantic`, or
  `unsupported`.
- MCP reports missing expected files only when framework/module evidence makes
  the expectation valid.
- Secret-bearing files never expose values.
- Generated files are lower-priority resolution evidence and cannot overwrite
  canonical source descriptors.
- Every advertised parser/framework capability requires a fixture-backed
  assertion in the acceptance matrix.

