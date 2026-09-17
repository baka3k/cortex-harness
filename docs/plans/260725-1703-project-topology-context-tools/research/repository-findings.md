---
type: research
date: 2026-07-25
---

# Research: Existing Parser, Topology, and MCP Architecture

## Summary

The repository already has substantial Android and framework semantics, but the
facts are fragmented by analyzer and are not organized around a canonical
cross-language module model. The safest extension point is a non-exclusive
topology overlay plus aggregate MCP services. Replacing primary parsers or
duplicating framework facts would conflict with completed orchestration and MCP
capability work.

## Context Search Results

- `mind_mcp` was called first but could not query project context because its
  configured `documents` collection does not exist.
- `graph_mcp` discovery (`list_mcp_functions`, `list_parsers`) succeeded.
  The initial broad architecture queries returned no matches, but the later
  analyzer/framework audit found indexed registry, detector, parser, fixture,
  and capability evidence. Graph expansion timed out on one broad query, so
  findings rely on returned indexed nodes rather than an assumed complete
  relationship traversal.
- Serena was then used for symbol overviews, targeted bodies, references, and
  pattern searches. Native `rg` was used only after those structured layers to
  enumerate exact test/doc filenames.

## Findings

### Existing Android graph support is partial

- `code-tiny/tools/android/android_common.py`
  - `AndroidManifestDef`
  - `AndroidComponentDef`
  - `AndroidResourceDef`
  - `GradleModuleDef`
  - `GradleDependencyDef`
  - `_parse_android_manifest`
- The manifest parser handles Activity, activity-alias, Service, Receiver, and
  Provider plus selected attributes and nested intent-filter values.
- `code-tiny/tools/android/android_kotlin_analyzer.py`
  - `_scan_android_gradle_files` only selects `build.gradle` and
    `build.gradle.kts`.
  - `_parse_gradle_file` detects only Android application/library plugins,
    namespace/application ID, and simple external coordinate calls.
  - `_collect_android_resources` only promotes layout/navigation/menu files and
    IDs.
- The analyzer writes `AndroidManifest`, `AndroidResource`, `GradleModule`, and
  `GradleDependency` nodes plus `USES_RESOURCE`, `DEPENDS_ON`, and
  `DECLARES_COMPONENT` relationships.
- `android_java_analyzer.py` reuses the shared manifest parser but has a
  separate write path, creating a duplicate-behavior risk that tests must cover.

### Primary symbol models lack visibility

- `code-tiny/tools/java/java_analyzer.py::{ClassDef,FunctionDef}`
- `code-tiny/tools/kotlin/kotlin_analyzer.py::{ClassDef,FunctionDef}`
- `code-tiny/tools/android/android_kotlin_analyzer.py::{ClassDef,FunctionDef}`

These dataclasses retain kind, names, file location, code, comments, and
summaries, but no normalized visibility or exported/public flag. A reliable
`get_public_apis` tool therefore requires ingestion changes; a query-only
heuristic would overclaim.

### Framework/config analysis exists but is not module-centric

- `code-tiny/tools/mybatis/` already includes detector, mapper XML analyzer,
  mapper interface analyzer, dynamic SQL, config parsing, resolver, writer, and
  Spring bridge logic.
- Spring, Servlet/JSP, MyBatis, Struts, web-framework, and database analyzers are
  registered as overlays in
  `code-tiny/tools/sync/incremental_sync.py::FRAMEWORK_ANALYZERS`.
- `_select_parser_for_path()` remains an exclusive primary-owner selector.
  Android claims Gradle and Android-context XML; framework overlays consume the
  global change set separately.
- CMake and Make are currently used by
  `code-tiny/tools/cplus/bootstrap_compile_commands.py` to generate compile
  commands. They are not persisted as topology facts.
- Framework detectors notice `pom.xml`, Gradle/settings files, and sometimes
  Ant `build.xml`, but that evidence is local to detection.

### Unified MCP has the right routing foundation

- `code-tiny/mcp/unified_mcp.py` owns parser-aware dispatch, provider capability
  context, bridge queries, endpoint call-chain queries, and tool registration.
- `code-tiny/mcp/framework_registry.py::FrameworkQueryConfig` centralizes labels,
  relationships, searchable properties, feature flags, and dimensional support.
- `code-tiny/mcp/tool_metadata.py::build_catalog()` filters shared tool metadata
  for backend registration.
- Direct aggregate graph queries can reuse the provider-neutral
  `GraphDriverFactory` path already used by `_run_bridge_query`, but should be
  placed in a focused service rather than further enlarging `unified_mcp.py`.
- Searches found no implementations named `get_project_modules`,
  `get_public_apis`, `get_endpoints`, or
  `get_module_architecture_summary`.

### Cross-plan constraints are real

- `plans/neo4j-to-falkordb-migration/plan.md` is still `in_progress` and owns
  provider runtime, schema, and direct Unified MCP query compatibility.
- Completed framework integration established non-exclusive overlays and warns
  against adding config/framework analyzers to primary ownership.
- Completed incremental reliability work established scan-root and repository
  topology. Its term “module topology” concerns Git/source scopes, not semantic
  build modules; the new model must reuse those normalized paths.
- Completed MCP hardening/alignment plans established capability gates,
  provider-neutral catalog wording, and parser-profile/framework-filter
  separation.

### Test gap

The root test suite contains framework overlay, framework MCP, provider,
incremental, and MCP acceptance coverage. Exact test-file enumeration did not
find a focused Android manifest/resource/topology suite. New coverage should use
fixture extraction and recording drivers before any expensive live sync.

### Expanded registry and special-file gap

- Indexed `tests/test_common_analyzer_registry.py` confirms 22 primary analyzers
  and 12 overlays, including FastAPI/Django, Express, Laravel, database SQL, and
  database PL/SQL beyond the seven overlays in the user-provided table.
- Primary ownership is still source-extension-centric for most languages.
- Structured exact searches confirmed selected special-file handling
  (`pubspec.yaml`, `package.json`, `.csproj`, `web.config`,
  `AndroidManifest.xml`, `pom.xml`, `CMakeLists.txt`, `Makefile`, `struts.xml`,
  `web.xml`) and no current indexed code references for `Cargo.toml`, `go.mod`,
  `composer.json`, or `tsconfig.json`.
- The full target inventory and capability rules are documented in
  `parser-framework-special-files-matrix.md`.

## Recommendations

1. Add one canonical topology overlay and descriptor registry.
2. Preserve existing Android labels and stable identities while adding
   `ProjectModule` semantics.
3. Extract language visibility during parsing; do not infer public APIs solely
   in MCP queries.
4. Normalize heterogeneous endpoint facts at query time while retaining
   specialized labels.
5. Add protobuf service/RPC extraction to satisfy gRPC inventory requirements.
6. Keep topology writes recording-driver testable and provider-neutral.
7. Use project-scoped, deterministic, paginated aggregate query services.
8. Treat malformed/static-analysis limitations as structured diagnostics.
9. Add a machine-readable special-file/framework coverage registry and make MCP
   support claims depend on fixture-backed parse depth.
10. Add `get_project_special_files` and `get_framework_context` while extending
    the original four context tools across every registered analyzer/overlay.

## Unresolved Questions

- Compiled ABI inspection is explicitly excluded; confirm later if binary
  compatibility reports are required.
- Common non-requested ecosystem manifests may be detected for tech-stack
  summaries, but deep dependency support should wait for separate fixture-backed
  handlers.
- Live Neo4j parity remains dependent on the active provider migration.
