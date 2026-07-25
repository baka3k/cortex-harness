# Phase 02: Descriptor Parsers and Canonical Project Topology

## Context

Build descriptors are currently consumed opportunistically by Android/framework
detectors or build bootstrap helpers. There is no shared parser registry,
canonical module record, or graph of internal module dependencies.

## Requirements

- Parse Gradle settings/build files, Maven POM, Ant, CMake, and Make with bounded
  static analysis.
- Reuse existing MyBatis analysis and link its facts/configs to modules.
- Resolve declared modules and internal dependencies across mixed build systems.
- Keep descriptor analysis non-exclusive and incremental-safe.
- Persist through the provider-neutral graph writer contract.

## Architecture

Create `code-tiny/tools/project_topology/`:

```text
models.py
contracts.py
registry.py
detector.py
parsers/
  gradle.py
  maven.py
  ant.py
  cmake.py
  make.py
  manifest.py
resolver.py
pipeline.py
project_topology_analyzer.py
```

Each parser returns facts plus structured diagnostics. `resolver.py` combines
descriptor evidence into canonical modules and resolves internal references.
It never executes a build tool or arbitrary descriptor code.

The writer uses stable IDs and additive labels:

- canonical `ProjectModule`;
- optional compatibility/specialized labels such as `GradleModule`;
- `BuildDescriptor`;
- internal/external dependency facts and relationships.

## Related Files

- New `code-tiny/tools/project_topology/`
- New `code-tiny/tools/graph/writer/project_topology_writer.py`
- `code-tiny/tools/graph/writer/__init__.py`
- Existing project-scope and normalized-path helpers
- `code-tiny/tools/mybatis/pipeline.py`
- `code-tiny/tools/mybatis/models.py`
- `code-tiny/scripts/setup_constraints.py`
- New `tests/test_project_descriptor_parsers.py`
- New `tests/test_project_topology_resolver.py`
- New `tests/test_project_topology_writer.py`

## Implementation Steps

1. Implement the descriptor registry with file-name/extension matching, parser
   versions, declared capability depth, and size limits.
2. Implement Gradle settings parsing for `rootProject.name`, `include`,
   `projectDir`, included builds, and version catalog declarations.
3. Implement Gradle build parsing for plugins, module kind, coordinates,
   namespaces/application IDs, source sets, project dependencies, external
   dependencies, and dynamic features.
4. Implement namespace-safe Maven parsing for parent, coordinates, packaging,
   child modules, properties, dependencies/scopes, dependency management,
   relevant plugins, and profiles. Resolve local `${property}` values only when
   statically available.
5. Implement Ant parsing for projects, imports, targets, and target dependency
   edges.
6. Implement bounded CMake parsing for project/subdirectory/target/link/source
   statements, preserving unresolved generator expressions as diagnostics.
7. Implement conservative Make parsing for includes, literal targets,
   prerequisites, and selected variables. Do not execute shell/functions.
8. Add detection-only manifest adapters for other common ecosystems, clearly
   reporting `topology_depth=identity` until deep handlers exist.
9. Resolve one canonical module per normalized module path, merging compatible
   descriptor evidence and reporting conflicts.
10. Resolve internal dependencies by declared module path/coordinate/target,
    retain unresolved references as external or diagnostic facts, and detect
    cycles without rejecting them.
11. Attach existing MyBatis config/mapper facts and technology evidence to their
    owning module without duplicating MyBatis parsing.
12. Implement recording-driver writes, provider-neutral constraints/indexes,
    scoped cleanup, and exact-row/edge tests.

## Todo

- [ ] Requested descriptor handlers emit typed facts and diagnostics.
- [ ] Mixed-build modules resolve deterministically.
- [ ] Internal/external dependency edges are distinguishable.
- [ ] MyBatis facts attach to owning modules.
- [ ] Writer is additive, scoped, and provider-neutral.
- [ ] Cycles and unresolved dynamic build logic are reported.

## Risks

- Gradle Groovy/Kotlin DSL is executable. Static parsing must be explicitly
  best-effort and never claim resolved values for dynamic expressions.
- Maven inheritance/profile resolution can become a build system. Limit the
  first release to local static parents/properties and record unresolved values.
- Make is highly dynamic. Treat shell/eval/function-generated targets as
  unsupported diagnostics rather than guesses.
- One directory can contain multiple descriptors. Merge evidence at the module
  level and retain every descriptor rather than creating duplicate modules.

## Success Criteria

- Pure-parser tests cover valid, malformed, namespaced, nested, and dynamic
  descriptors.
- The mixed fixture yields the expected module set, module kinds, and dependency
  graph with stable IDs.
- Recording-driver tests prove exact project/module/descriptor/dependency
  scoping and cleanup.
- Re-running the same facts is idempotent.

