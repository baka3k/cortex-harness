# Phase 07: Primary Analyzer Special-File Coverage

## Context

Most primary analyzers currently own source files and vectors, while project,
module, dependency, lock, toolchain, resource, and deployment files are either
handled incidentally or ignored. The complete target inventory is defined in
`research/parser-framework-special-files-matrix.md`.

## Requirements

- Audit all 22 primary analyzers against the special-file matrix.
- Add descriptor adapters without changing exclusive source ownership.
- Attach special-file facts to canonical modules and primary symbols/vectors.
- Record parse depth, provenance, generated/canonical status, confidence, and
  diagnostics.
- Avoid reading secret values or executing project/build scripts.

## Architecture

Extend the Phase 02 descriptor registry with per-ecosystem adapters. Primary
analyzers remain owners of source vectors; the topology overlay owns special-file
facts. An adapter can request primary-analyzer enrichment but cannot create a
second canonical symbol graph.

Implementation waves:

1. Module ecosystems: Go, Rust, Dart, Swift, C#/.NET, Java/Kotlin.
2. Package ecosystems: Python, JavaScript, TypeScript, PHP, Perl.
3. Toolchain/legacy ecosystems: C/C++, COBOL, Delphi, VB.NET, VB6, VBA,
   VBScript/Classic ASP.
4. Database ecosystems: SQL and PL/SQL.

## Related Files

- `code-tiny/tools/project_topology/registry.py`
- New adapters under `code-tiny/tools/project_topology/parsers/`
- All primary analyzer entrypoints listed in `code-tiny/README.md`
- `code-tiny/tools/sync/owner_manifest.py`
- `code-tiny/tools/sync/incremental_sync.py`
- `code-tiny/mcp/framework_registry.py`
- `code-tiny/scripts/setup_constraints.py`
- New fixtures under `tests/fixtures/parser-special-files/`
- New `tests/test_primary_special_file_coverage.py`

## Implementation Steps

1. Add a machine-readable coverage registry with parser, filename/glob, role,
   adapter, parse depth, secret/generated policy, and fixture ID.
2. Implement Go `go.mod/go.work`, Rust Cargo, Dart pub, SwiftPM/Xcode, and .NET
   project/workspace adapters.
3. Implement Python packaging/lock/tool config, JS/TS package/workspace/compiler
   config, PHP Composer/autoload, and Perl distribution metadata adapters.
4. Extend C/C++ build/dependency/toolchain coverage beyond compile-command
   bootstrap; add Delphi project/group/UI resources.
5. Add COBOL copy-path/job/build/dialect profiles without inventing a universal
   manifest.
6. Add VB.NET solution/project/config/resources, VB6 project/group/forms/COM
   references, VBA host/extraction metadata, and VBScript/Classic ASP host/include
   context.
7. Add SQL/PLSQL migration, orchestration, schema-project, and analytics metadata
   adapters.
8. Attach descriptors, targets, dependencies, resources, and diagnostics to
   canonical modules with stable IDs.
9. Add missing-file expectations only for proven project/framework types.
10. Add fixture-backed coverage tests for every registry entry and keep
    unimplemented entries at `parse_depth=unsupported`.

## Todo

- [ ] Every primary analyzer has a special-file coverage record.
- [ ] Required module/package descriptors have deep fixture-backed adapters.
- [ ] Legacy/no-standard-manifest languages use explicit profiles and evidence.
- [ ] Generated and secret-bearing files obey policy.
- [ ] Source ownership and vector strategies remain unchanged.

## Risks

- One filename can belong to multiple ecosystems. Resolve by module evidence and
  allow non-exclusive descriptor consumers.
- Reading Xcode, Office/VBA, MSBuild, or vendor COBOL formats can expand scope.
  Start with safe metadata extraction and structured diagnostics.
- Dependency lockfiles can be large. Stream or bound parsing and retain only
  architecture-relevant fields.
- Tool config is not application architecture by default. Store it as tooling
  context and keep architecture summaries filtered.

## Success Criteria

- The coverage registry has no unclassified primary analyzer.
- Each advertised deep adapter has positive, malformed, incremental, and deletion
  fixture tests.
- The same mixed repository produces stable modules/dependencies on full and
  incremental scans.
- No secret values or generated-file dominance appear in graph/MCP responses.

