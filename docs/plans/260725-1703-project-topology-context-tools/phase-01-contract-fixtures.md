# Phase 01: Contract, Fixtures, and Compatibility Baseline

## Context

Existing analyzers use different labels, IDs, properties, and writer paths.
Adding aggregate MCP tools before defining a canonical module/API/endpoint
contract would encode accidental inconsistencies into a public interface.

## Requirements

- Freeze module, descriptor, public API, endpoint, diagnostic, and provenance
  schemas before implementation.
- Preserve existing Android/framework labels and stable IDs.
- Define project scoping, path normalization, pagination, ordering, and error
  behavior for all four tools.
- Create representative fixtures before parser changes.
- Establish the regression and provider-compatibility baseline.

## Architecture

Define typed intermediate records under the new topology package:

- `ModuleFact`
- `DescriptorFact`
- `DependencyFact`
- `PublicApiFact`
- `EndpointFact`
- `AnalysisDiagnostic`

Records are pure extraction outputs. Provider-specific writers and MCP response
formatters consume them but do not redefine their meaning.

Stable module ID:

```text
project-module:{normalized_project_scope}:{normalized_module_path}
```

The normalization helper must reuse current project-scope/path conventions and
must distinguish root (`.`) from absent input.

## Related Files

- New `code-tiny/tools/project_topology/models.py`
- New `code-tiny/tools/project_topology/contracts.py`
- Existing ID/project-scope helpers under `code-tiny/tools/common/` and
  `code-tiny/tools/sync/`
- `code-tiny/mcp/framework_registry.py`
- `code-tiny/mcp/tool_metadata.py`
- New fixtures under `tests/fixtures/project-topology/`
- New `tests/test_project_topology_contract.py`

## Implementation Steps

1. Inventory current Android, framework, database, and generic graph labels,
   relationship types, IDs, scope fields, and deletion ownership.
2. Write a compatibility table mapping current facts to the canonical contract.
3. Define normalized module kinds, descriptor types, dependency scopes,
   endpoint protocols, visibility values, confidence levels, and diagnostic
   codes.
4. Define strict JSON-like response schemas for the four MCP tools, including
   pagination and `capability_diagnostics`.
5. Build a mixed fixture:
   - Android app, library, and dynamic feature using Groovy and Kotlin DSL;
   - Java/Kotlin symbols with every visibility;
   - Maven parent/child modules;
   - MyBatis config plus mapper XML;
   - Ant project;
   - CMake subdirectories/targets and a Make target;
   - REST/controller routes and a protobuf service;
   - malformed and dynamically computed descriptor examples.
6. Add contract tests for stable IDs, deterministic sorting, path
   normalization, deduplication, and diagnostics.
7. Record the focused existing test baseline and any environment-dependent
   exclusions before product changes.

## Todo

- [ ] Canonical fact models and enumerations are reviewed.
- [ ] Existing labels/IDs have an explicit compatibility mapping.
- [ ] Four MCP request/response contracts are frozen.
- [ ] Mixed fixture and malformed-descriptor fixture exist.
- [ ] Baseline tests and exclusions are recorded.

## Risks

- A new module ID can duplicate existing `GradleModule` nodes. The compatibility
  table must decide reuse before any writer code is added.
- Fixed enums can reject future build systems. Keep serialized kinds versioned
  and allow `unknown` plus evidence.
- Fixture-only designs can miss monorepo path behavior. Include nested modules,
  alternate `projectDir`, and module-scoped scan roots.

## Success Criteria

- Contract tests run without graph services or embedding models.
- Identical fixtures produce identical IDs and ordering on macOS, Linux, and
  Windows path forms.
- No existing label or public MCP tool is renamed or removed.
- Each ambiguity produces a documented confidence/diagnostic rule.

