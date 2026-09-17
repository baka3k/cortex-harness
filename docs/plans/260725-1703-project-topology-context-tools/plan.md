---
title: "Project Topology, Parser Coverage, and Context MCP Tools"
status: completed
created: 2026-07-25
mode: hi-plan --full
scope: all registered code-tiny primary analyzers and framework overlays, special-file coverage, project topology graph, public API and endpoint semantics, unified MCP context tools
blockedBy: [neo4j-to-falkordb-migration]
relatedPlans: [260713-1638-framework-parser-integration, 260718-2159-incremental-scan-reliability, 260719-0100-mcp-query-capability-hardening, 260719-2150-parser-mcp-runtime-alignment]
reviewed: 2026-07-25
completed: 2026-07-25
---

# Project Topology, Parser Coverage, and Context MCP Tools

## Overview

Extend `code-tiny` from language- and framework-centric analysis into a
project-structure intelligence layer. The target is a provider-neutral graph
that can answer four common context questions without forcing an AI client to
reconstruct architecture from low-level symbol searches:

1. What modules exist, what kind are they, and how do they depend on each other?
2. What is the source-level public API surface of each module?
3. What REST, route, controller, and gRPC endpoints are exposed?
4. What is the concise architecture summary of a module or project?

The expanded scope covers every live registry entry: **22 primary analyzers and
12 framework overlays**. Beyond source syntax, the analyzer layer must
understand the special files that define packages, modules, dependencies, build
targets, runtime profiles, routes, security, persistence, UI/resources, code
generation, and deployment. The complete do-not-miss inventory is in
[`research/parser-framework-special-files-matrix.md`](research/parser-framework-special-files-matrix.md).

This is an extension of existing behavior, not a replacement. Android already
emits partial manifest, component, resource, and Gradle facts. Spring,
Servlet/JSP, MyBatis, Struts, web-framework, and database overlays already emit
specialized semantic facts. Unified MCP already provides parser-aware graph
queries and provider capability gates. The plan fills the missing shared
topology contract, deepens the incomplete parsers, and adds stable aggregate
query tools.

## Verified Baseline

| Area | Existing behavior | Confirmed gap |
| --- | --- | --- |
| Android manifests | `android_common._parse_android_manifest()` extracts package, Activity, alias, Service, Receiver, Provider, selected component attributes, and intent-filter actions/categories/data | No application/library classification, declared/used permissions, features, queries, instrumentation, application metadata, or first-class intent-filter facts |
| Android resources | `android_kotlin_analyzer._collect_android_resources()` creates layout/navigation/menu resources and IDs | No strings/plurals/arrays/styles/colors/dimens/config XML, layout hierarchy, resource-file facts, or general reference graph |
| Gradle | Android analyzer discovers `build.gradle`/`.kts`, detects app/library plugins, namespace/application ID, and external coordinates | No `settings.gradle(.kts)`, included builds, version catalogs, dynamic-feature/KMP/JVM module kinds, `project(...)` dependencies, or module-to-module edges |
| Java/Kotlin symbols | Canonical Class/Function graph facts exist | Core dataclasses do not retain normalized visibility/export metadata, so strict public API queries cannot be truthful |
| Maven/Ant/CMake/Make | Framework detectors notice some build files; C++ bootstrap uses CMake/Make to obtain compile commands | No canonical descriptor facts, module nodes, target topology, or cross-build dependency model |
| MyBatis | Dedicated detector, mapper/config parser, resolver, writer, and Spring bridge exist | MyBatis facts are not consistently attached to canonical project modules or surfaced in an architecture summary |
| Endpoints | Multiple overlays emit `ApiEndpoint`, `HttpEndpoint`, route/controller, Servlet/JSP, Struts, and persistence facts | No normalized inventory tool; no `.proto` service/RPC extraction; clients must know label-specific queries |
| MCP | Unified MCP has parser profiles, provider schema inspection, endpoint call-chain tools, and a shared catalog | None of the four requested aggregate context tools exist |
| Tests | Strong framework/MCP/incremental tests exist | No focused Android topology/resource fixture suite or multi-build topology acceptance fixture was found |
| Registry completeness | `tests/test_common_analyzer_registry.py` verifies 22 primaries and 12 overlays plus vector strategies and MCP profiles | No executable matrix covers special files, framework context dimensions, parse depth, and MCP evidence |

Detailed evidence is recorded in
[`research/repository-findings.md`](research/repository-findings.md).

## Scope Challenge Decisions

### 1. Should config files become new primary parsers?

**Decision:** No. Add a non-exclusive `project_topology` overlay that consumes
build/config descriptors after primary language parsing. This preserves the
current one-primary-owner model and allows the same Gradle/XML file to feed
Android, Spring, MyBatis, and topology analysis.

### 2. What counts as a public API?

**Decision:** Use source-level declarations, not compiled ABI inspection.

- Java: explicit `public` types and members; interface members are public by
  language rule; protected/package-private/private declarations are excluded.
- Kotlin: default or explicit `public`; `internal`, `protected`, and `private`
  are excluded from the strict surface.
- C/C++: explicit export/linkage evidence is authoritative; header-location
  heuristics are returned only when `include_inferred=true`.
- Other analyzers: use normalized visibility/export facts when present. Do not
  silently classify every top-level symbol as public.

Every result carries `visibility`, `evidence`, and `confidence`.

### 3. How broad is “other popular config” support?

**Decision:** Phases 01-06 deeply cover the requested Android/JVM/native set.
Phases 07-10 extend special-file and framework-context coverage across every
registered primary analyzer and overlay. Registry entries may begin at
`parse_depth=identity` or `unsupported`; dependency/topology/semantic depth is
advertised only after a fixture-backed handler and graph/MCP assertion exist.

### 4. Should existing Android graph labels be replaced?

**Decision:** No destructive migration. Introduce canonical `ProjectModule` and
descriptor semantics additively. Existing `GradleModule`, `AndroidManifest`,
`AndroidComponent`, and `AndroidResource` labels/IDs remain queryable. Where a
Gradle module maps one-to-one to a project module, use one stable node with
compatible labels rather than duplicate nodes.

## Target Architecture

### Analysis flow

```text
primary language analyzers
  -> framework overlays
  -> project_topology overlay
       -> descriptor registry
       -> module resolver
       -> dependency resolver
       -> canonical topology writer
  -> provider-neutral graph
  -> unified MCP aggregate services
```

The topology overlay receives the normalized full/incremental manifest. It uses
descriptor allowlists and cached module evidence; it does not recursively parse
arbitrary data files.

Primary analyzers remain source/vector owners. Special files are non-exclusive
inputs to topology and framework analysis, so one package, project, runtime, or
deployment descriptor can enrich several context dimensions without changing
canonical source ownership.

### Canonical graph contract

Primary nodes:

- `ProjectModule`: stable module identity, path, kind, languages, frameworks,
  build systems, source roots, and confidence.
- `BuildDescriptor`: normalized descriptor metadata with `descriptor_type`,
  path, parser version, and diagnostics.
- Existing compatibility/specialized labels: `GradleModule`,
  `AndroidManifest`, `AndroidComponent`, `AndroidResource`, MyBatis/Spring/
  Servlet/Struts facts, and canonical symbols.
- `GrpcService` and `GrpcEndpoint` for protobuf services and RPC methods.

Primary relationships:

- `Project-[:CONTAINS]->ProjectModule`
- `ProjectModule-[:HAS_DESCRIPTOR]->BuildDescriptor`
- `ProjectModule-[:CONTAINS]->File|Class|Interface|Function`
- `ProjectModule-[:DEPENDS_ON {scope, configuration, source}]->ProjectModule`
- `ProjectModule-[:DEPENDS_ON]->external dependency fact`
- `ProjectModule-[:EXPOSES_API]->Class|Interface|Function`
- `ProjectModule-[:EXPOSES_ENDPOINT]->ApiEndpoint|HttpEndpoint|GrpcEndpoint|Route`
- Existing framework and Android relationships remain authoritative for
  specialized traversal.

Stable IDs must be based on normalized `project_id + module_path` and must not
depend on the absolute checkout path. Ambiguous descriptor evidence produces a
diagnostic and lower confidence rather than a guessed module kind.

### Module classification precedence

1. Explicit build plugin/packaging: Android application/library/dynamic feature,
   Maven packaging, CMake target type, Gradle Java/Kotlin/JVM plugin.
2. Android manifest plus Gradle context.
3. Parent settings/POM/module declaration.
4. Build descriptor presence with `unknown` subtype.

One directory may have multiple build descriptors but one canonical module
identity. Nested declared modules become separate nodes.

### Parser coverage contract

| Descriptor | Required extraction |
| --- | --- |
| `AndroidManifest.xml` | manifest/application attributes, app/library evidence, components, exported state, permissions, features, queries, instrumentation, metadata, intent filters and deep links |
| Android `res/**/*.xml` | values resources, layout/view hierarchy, navigation/menu/config facts, IDs, and resource references with qualifiers |
| `settings.gradle(.kts)` | root name, `include`, `projectDir`, included builds, plugin/dependency management catalogs |
| `build.gradle(.kts)` | plugins, Android/JVM module kind, namespace/application ID, source sets, project/external dependencies, dynamic features |
| `pom.xml` | coordinates, packaging, parent, modules, dependency management, dependencies/scopes, plugins/profiles relevant to topology |
| MyBatis XML | reuse existing mapper/config semantic parser and attach results/config evidence to modules |
| `build.xml` | Ant project, imports, targets, target dependencies, source/output hints |
| `CMakeLists.txt` | project, add_subdirectory, target declarations, target linkage, include/source directories |
| `Makefile` | includes, declared targets, target prerequisites, variables needed for local dependency resolution; mark dynamic/shell-generated constructs unresolved |
| `.proto` | package, services, RPC methods, streaming flags, request/response types, HTTP transcoding options when statically present |

This first-wave table is supplemented by the full 22-primary/12-overlay matrix
in `research/parser-framework-special-files-matrix.md`. Every additional handler
must declare file role, parse depth, canonical/generated status, redaction
policy, owning module/framework, diagnostics, and fixture-backed capability
evidence.

### Unified MCP contracts

All tools use project scoping, deterministic ordering, bounded result sizes,
provider capability diagnostics, and parser/profile routing.

#### `get_project_modules`

Inputs: `project_id`, optional `module_id`/`module_path`, `include_dependencies`,
`db`, `parser_type`, pagination controls.

Returns module identity/type, paths, languages/frameworks/build systems,
descriptors, internal/external dependencies, confidence, diagnostics, and totals.

#### `get_public_apis`

Inputs: `project_id`, optional `module_id`, symbol kinds, language,
`include_inferred=false`, `db`, `parser_type`, pagination controls.

Returns public/exported symbol identity, kind, signature, visibility, source
location, owning module, evidence, confidence, and totals.

#### `get_endpoints`

Inputs: `project_id`, optional `module_id`, protocol, framework, HTTP method,
path/name filter, `db`, `parser_type`, pagination controls.

Returns a normalized record with `protocol`, method/path or service/RPC name,
framework, handler, owning module, source location, security metadata when
available, evidence, and original node kind.

#### `get_module_architecture_summary`

Inputs: exactly one project/module scope (or an explicit `all_modules=true`),
detail level, item limits, `db`, and `parser_type`.

Returns module identity, technology stack, primary descriptors, internal and
external dependencies, public API and endpoint counts plus bounded samples,
Android/framework/persistence facts, diagnostics, and ingestion provenance.
It aggregates the graph and never performs a hidden filesystem rescan.

#### `get_project_special_files`

Inputs: project/module scope, file role, parser/overlay, framework, parse depth,
status, generated/canonical filter, `db`, `parser_type`, and pagination.

Returns decisive project files with role, owner, module, framework evidence,
parse depth, safe summary, provenance, freshness, redaction state, diagnostics,
and missing-expected status when strong project evidence makes that expectation
valid.

#### `get_framework_context`

Inputs: project/module scope, framework filter, requested context dimensions,
detail limits, `db`, `parser_type`, and pagination.

Returns framework instances with detection/version evidence, entrypoints,
configuration profiles, endpoints/routes/pages/services, security,
persistence/migrations, messaging/jobs/events, UI/templates/resources,
deployment descriptors, dependencies, diagnostics, and ingestion provenance.

## Cross-Plan Dependencies

### `neo4j-to-falkordb-migration`

This plan changes the same provider/query boundary, schema setup, and Unified MCP
surface. Pure extraction, fixture, and recording-driver work may proceed, but
live provider parity and final schema/MCP acceptance are blocked until the
provider-neutral contract is stable. The upstream plan must list this plan in
its `blocks` metadata.

### Completed plans reused

- `260713-1638-framework-parser-integration`: reuse non-exclusive framework
  overlay orchestration and do not alter primary ownership.
- `260718-2159-incremental-scan-reliability`: reuse normalized scan roots,
  changed/deleted manifests, repository scopes, and lock/state behavior.
- `260719-0100-mcp-query-capability-hardening`: reuse parser capability profiles,
  provider schema gates, and fixture-backed MCP acceptance.
- `260719-2150-parser-mcp-runtime-alignment`: preserve the separation between
  parser profile and exact framework filter and retain provider-neutral catalog
  language.

## Phases

1. [Phase 01 - Contract, fixtures, and compatibility baseline](phase-01-contract-fixtures.md)
2. [Phase 02 - Descriptor parsers and canonical project topology](phase-02-project-topology.md)
3. [Phase 03 - Deep Android XML and Gradle semantics](phase-03-android-xml-gradle.md)
4. [Phase 04 - Public API and endpoint semantics](phase-04-api-endpoint-semantics.md)
5. [Phase 05 - Unified MCP context tools](phase-05-mcp-context-tools.md)
6. [Phase 06 - Incremental integration, provider parity, and hardening](phase-06-orchestration-hardening.md)
7. [Phase 07 - Primary analyzer special-file coverage](phase-07-primary-analyzer-special-files.md)
8. [Phase 08 - Framework overlay deep context](phase-08-framework-overlay-context.md)
9. [Phase 09 - MCP special-file and framework context queries](phase-09-mcp-special-files-framework.md)
10. [Phase 10 - Full parser and framework coverage acceptance](phase-10-coverage-acceptance.md)

## Expected File Areas

- New `code-tiny/tools/project_topology/`
- Per-ecosystem descriptor adapters for all registered primary analyzers
- `code-tiny/tools/android/android_common.py`
- `code-tiny/tools/android/android_kotlin_analyzer.py`
- `code-tiny/tools/android/android_java_analyzer.py`
- `code-tiny/tools/java/java_analyzer.py`
- `code-tiny/tools/kotlin/kotlin_analyzer.py`
- `code-tiny/tools/cplus/cplus_analyzer.py`
- Existing MyBatis parser/pipeline integration points
- All registered framework overlay packages under `code-tiny/tools/`
- `code-tiny/tools/graph/writer/`
- `code-tiny/tools/sync/incremental_sync.py`
- `code-tiny/tools/sync/owner_manifest.py` only if descriptor ownership metadata
  needs an additive non-primary category
- `code-tiny/mcp/services/`
- `code-tiny/mcp/framework_registry.py`
- `code-tiny/mcp/tool_metadata.py`
- `code-tiny/mcp/unified_mcp.py`
- `code-tiny/scripts/setup_constraints.py`
- `tests/fixtures/` and focused topology/Android/MCP test modules
- `code-tiny/README.md`, `code-tiny/mcp/README.md`, and
  `docs/MCP_CAPABILITY_ACCEPTANCE_MATRIX.md`

## Scope Boundaries

Included:

- requested descriptor families and Android XML depth;
- identity, topology, dependency, configuration, interface, resource, generated,
  secret-bearing, and deployment special-file coverage for all registered
  primary analyzers;
- deep context coverage for all 12 registered framework overlays;
- source-level public API classification;
- REST/route/controller normalization and protobuf gRPC services;
- module topology, dependencies, technology summaries, four original aggregate
  tools, plus special-file and framework-context tools;
- full and incremental orchestration, deletion safety, diagnostics, tests, and
  provider-neutral schema/query behavior.

Excluded:

- compiled JAR/AAR/DEX/native ABI inspection;
- executing Gradle, Maven, Ant, CMake, Make, or arbitrary build scripts merely
  to discover topology;
- resolving dynamically computed build logic beyond static evidence;
- unregistered ecosystems and arbitrary tool configs without architecture value;
- replacing existing framework analyzers or creating one MCP server per parser;
- returning raw secret values from configuration files.

## Success Criteria

- A mixed fixture containing an Android app, Android library, dynamic feature,
  Maven/JVM module, MyBatis mapper/config, and CMake/Make native module produces
  deterministic canonical module nodes and correct internal dependency edges.
- Android fixtures expose components, permissions, intent filters/deep links,
  resource values, layout hierarchy, navigation, and resource references without
  duplicate facts across Java/Kotlin Android analyzer paths.
- Java/Kotlin public APIs follow explicit language visibility rules. Inferred
  C/C++ APIs are excluded by default and clearly marked when requested.
- REST/framework endpoints and protobuf RPC methods are normalized through one
  query contract without erasing their specialized source facts.
- The four requested MCP tools are discoverable through `list_mcp_functions`,
  enforce project scope, paginate deterministically, report capability/schema
  gaps honestly, and return fixture-derived results.
- Incremental add/change/delete/rename cases update only affected topology facts
  and never delete canonical language or unrelated framework data.
- Recording-driver tests pass for exact rows/edges. Live FalkorDB acceptance
  passes; live Neo4j parity passes when the blocking migration makes that
  environment available, otherwise the exclusion remains explicit.
- Existing analyzer, framework overlay, incremental sync, provider, MCP routing,
  and acceptance tests remain green.
- Malformed, oversized, or unsupported descriptors produce bounded diagnostics
  and do not abort unrelated module analysis.
- Documentation and the MCP capability matrix state actual fixture-backed
  support rather than advertising unverified parser depth.
- Every registered primary analyzer and overlay has a machine-readable acceptance
  row, and every advertised deep capability points to a fixture and exact
  graph/MCP assertion.
- `get_project_special_files` and `get_framework_context` are discoverable,
  scoped, bounded, redaction-safe, capability-aware, and fixture-backed.

## Review and Validation

- [Red-team report](reports/red-team.md): **GO with documented gates**.
- [Validation report](reports/validation-report.md): **PASS for implementation
  planning**.
- The parser/framework expansion was validated as an additive continuation; its
  coverage matrix and Phases 07-10 preserve the same identity, ownership,
  redaction, capability, and provider gates.
- The Phase 01 identity/ownership compatibility map and the active provider
  migration interface are hard implementation gates.

## Delivery Command

After approval, execute the plan with:

```text
/hi-craft plans/260725-1703-project-topology-context-tools/plan.md
```
