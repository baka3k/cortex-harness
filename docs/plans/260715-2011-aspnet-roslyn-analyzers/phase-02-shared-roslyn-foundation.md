# Phase 02: Build Shared Roslyn and Semantic Foundations

## Context

Both analyzers require the same process isolation, compiler evidence, stable identities, diagnostics, safe configuration handling, and serialization. Implement these once under `tools/common/aspnet/`, leaving framework rules in their owning packages.

## Requirements

- The analysis core runs without graph/vector services or credentials.
- Worker execution is bounded, root-contained, versioned, deterministic, and safe for malformed projects.
- Workspace and syntax-only capability are explicit.
- Shared models serialize byte-stably across runs and checkout roots.
- XML/JSON parsing denies external entities/network access and redacts secrets before output.
- No analyzed code, target, template, generator, build script, or application is executed.

## Architecture

```text
Python adapter
  -> validates root/project/files/options
  -> builds or locates a compatible worker
  -> executes a bounded manifest request
  -> validates protocol/schema/path containment
  -> returns compiler evidence + capabilities + diagnostics

Shared contracts
  -> normalized facts/relationships/dependencies
  -> stable identities and canonical JSON
  -> safe JSON/XML parsing and redaction
```

## Related Files

- `code-tiny/tools/common/aspnet/` (new)
- `code-tiny/tools/common/aspnet/roslyn_worker/` (new)
- `code-tiny/tools/vb/vb_roslyn_adapter.py` (reference; extract generic code only if safely justified)
- `code-tiny/tools/graph/writer/`
- Existing VB Roslyn pipeline tests (operational reference)

## Implementation Steps

1. Add the smallest shared package with protocol/model versions and lazy imports.
2. Implement immutable spans, capabilities, diagnostics, facts, relationships, dependency indexes, and results.
3. Implement root containment, relative path normalization, stable semantic/C# anchor IDs, deterministic dedupe/sort, and canonical JSON.
4. Implement bounded safe JSON/XML helpers and centralized secret redaction.
5. Create the C# worker with pinned packages and the frozen protocol.
6. Implement deterministic solution/project/workspace selection, target metadata, semantic/syntax modes, compiler extraction, and per-document isolation.
7. Implement the Python adapter with argument arrays, temporary manifests, build locking/cache, runtime selection, bounded timeouts, and schema/stderr validation.
8. Add protocol, containment, malformed output, crash, timeout, missing SDK, syntax-only, workspace, multi-project, cache, and determinism tests.
9. Prove the package imports and analyzes probe inputs without graph/vector dependencies.

## Todo

- [ ] Shared contracts and IDs are implemented.
- [ ] Safe parsing and redaction are implemented.
- [ ] Worker and adapter satisfy the protocol.
- [ ] Workspace/syntax capability is observable and tested.
- [ ] Failure, containment, cache, and determinism tests pass.

## Risks

- First-use worker builds can be slow or unavailable offline.
- Multi-target projects can duplicate compiler evidence.
- Roslyn package upgrades can change symbol formatting.

## Success Criteria

- Both analyzers consume one deterministic compiler-evidence API without importing each other.
- Identical inputs produce identical normalized evidence and stable IDs across roots.
- Missing workspace capability creates bounded partial results, never false resolved semantics.

