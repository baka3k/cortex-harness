# Phase 01: Freeze Contracts and Prove Parser Capabilities

## Context

The source specs define technologies and runtime models but leave implementation-critical choices open: exact artifact detection, Roslyn workspace behavior, Razor/Web Forms capabilities, stable identities, graph direction, incremental invalidation, budgets, and unsupported-feature policy. Freeze these before building extractors.

## Requirements

- Preserve `csharp` as primary `.cs` owner and classify both new tools as overlays.
- Define module-level Core/Framework detection and ambiguity behavior.
- Validate Roslyn C# syntax/semantic APIs, `MSBuildWorkspace`, SDK requirements, and legacy reference-assembly behavior.
- Validate Razor parsing for `.cshtml`/`.razor` and bounded markup parsing for `.aspx`/`.ascx`/`.master`/ASMX/ASHX.
- Freeze the worker protocol, normalized result, graph vocabulary, diagnostics, coverage, stable IDs, and failure policy.
- Separate MVP, partial, deferred, and non-goal behavior for each supported technology.

## Decisions to Record

1. Worker targets and pinned Roslyn/MSBuild/Razor package versions.
2. Workspace selection for `.sln`, `.csproj`, loose files, and multiple target frameworks.
3. Semantic modes (`auto`, `on`, `off`) and exact exit/coverage behavior.
4. Compiler evidence for projects, documents, types, members, attributes, invocations, constants, inheritance, and diagnostics.
5. Razor/legacy markup capability matrix and malformed/generated-code behavior.
6. Detection evidence, confidence, module boundaries, mixed solutions, and strong deleted candidates.
7. Unified node/edge fields, directions, C# anchor coordinates, ordering, redaction, and resolution states.
8. Resource budgets, cache fingerprints, operational timeouts, and deterministic truncation.

## Related Files

- Source specifications listed in `plan.md`
- `code-tiny/docs/Tool_Template.md`
- `code-tiny/docs/guide_tool_integrate.md`
- `code-tiny/tools/csharp/csharp_analyzer.py`
- `code-tiny/tools/vb/vb_roslyn_adapter.py`
- `code-tiny/tools/vb/roslyn_worker/`
- `code-tiny/tools/servlet_jsp/`
- `code-tiny/tools/spring/`

## Implementation Steps

1. Build a requirement-to-evidence matrix for every source-spec technology, node, relationship, migration mapping, error behavior, and acceptance gate.
2. Create minimal probe inputs for SDK-style Core, legacy Framework, loose C#, Razor, and Web Forms markup.
3. Prototype Roslyn workspace/syntax loading without graph writes; capture supported hosts, missing workloads/reference assemblies, timing, and deterministic behavior.
4. Probe Razor and legacy markup parsers; choose safe APIs and document gaps.
5. Define the worker request/response schema and protocol version, including per-document and process failures.
6. Define immutable facts, relationships, diagnostics, capabilities, dependencies, coverage counters, and serialization ordering.
7. Define stable IDs and canonical C# anchor mapping using relative paths and qualified compiler symbols.
8. Freeze detector/artifact inventories and resolution matrices for routes, handlers, config, views, DI, lifecycle events, and pipeline order.
9. Record every unsupported or host-dependent construct with its diagnostic and coverage outcome.

## Todo

- [ ] Requirement/evidence matrix is complete.
- [ ] Parser probes pass or have documented partial policies.
- [ ] Protocol and normalized model versions are frozen.
- [ ] Detection, artifacts, identities, graph direction, redaction, and coverage are frozen.
- [ ] MVP and deferred capability lists are explicit.

## Risks

- Parser packages may require unavailable runtimes/workloads.
- Old-style projects may parse syntactically but fail semantic compilation.
- Compiler symbol display formats can vary across targets/package versions.

## Success Criteria

- An implementer can build either analyzer without inventing protocol, identity, artifact, graph, or fallback behavior.
- Probes demonstrate compiler-backed Core analysis and an honest legacy workspace or syntax-only partial mode.
- The contract prevents duplicate C# ownership and defines anchors for every compiler-backed fact.

