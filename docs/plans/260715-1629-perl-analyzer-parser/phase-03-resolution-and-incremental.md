# Phase 03: Add Conservative Resolution and Incremental Analysis

## Context

The analyzer must support dependency discovery, navigation, and incremental parsing without violating the specification's non-goal of full semantic analysis. Resolution is therefore project-local, evidence-backed, deterministic, and allowed to remain unresolved.

## Requirements

- Build indexes for files/modules, package declarations, named subroutines, and imports.
- Normalize static `Foo::Bar` module names to candidate project paths without searching outside the project root or executing `@INC` logic.
- Resolve only unambiguous direct/qualified calls with stable source evidence.
- Preserve method calls, coderef calls, symbolic references, string `eval`, dynamic `require`, and ambiguous names as unresolved/ambiguous references.
- Build file/module dependency and reverse-dependency indexes for changed-file closure.
- Reparse changed files and affected reverse dependents; process deleted files through staged graph/vector cleanup.
- Version parse caches by content digest, relative path, analyzer/grammar versions, and semantic options.
- Ensure affected incremental output equals the corresponding portion of a clean full run.

## Architecture

```text
parsed records
  -> package/module/subroutine indexes
  -> conservative resolver
  -> resolved + unresolved references
  -> dependency/reverse-dependency index
  -> affected-file expansion
  -> validated staged result
```

Resolution precedence must be documented and deterministic. A recommended baseline is exact fully qualified symbol, current-package symbol, then a unique statically imported candidate. Ties remain ambiguous; filesystem order must never break them.

## Related Files

- `code-tiny/tools/perl/resolver.py`
- `code-tiny/tools/perl/pipeline.py`
- `code-tiny/tools/perl/perl_analyzer.py`
- shared helpers under `code-tiny/tools/common/` only when existing cache/manifest/cleanup APIs are reused without Perl-specific branching
- `tests/test_perl_resolution.py`
- `tests/test_perl_incremental.py`
- `tests/test_perl_cache.py`
- `tests/test_perl_determinism.py`

## Implementation Steps

1. Build canonical indexes and collision diagnostics for packages, modules, and subroutines.
2. Implement static module-name normalization for project-local `.pm` candidates while retaining raw dependency evidence.
3. Apply explicit call-resolution precedence and attach confidence, reason, and resolution status.
4. Emit namespaced diagnostics for missing modules, missing symbols, ambiguity, dynamic targets, and cycles.
5. Create dependency and reverse-dependency indexes from static imports plus evidence-backed resolved references.
6. Reuse shared manifest loading and cache-root helpers; normalize all manifest paths and reject paths outside the project root.
7. Expand changed paths to the minimum dependency closure required for equivalent results.
8. Stage deleted-file tombstones/cleanup work and apply it only after successful analysis and persistence preflight.
9. Add full-versus-incremental equivalence tests for change, rename, delete, cache hit, cache corruption, and grammar-version invalidation.

## Todo

- [ ] Resolution precedence is documented and tested.
- [ ] Ambiguous/dynamic calls never become `CALLS` edges.
- [ ] Dependency cycles are bounded and diagnosed.
- [ ] Changed/impacted/deleted manifests are root-contained and deterministic.
- [ ] Cache invalidation includes grammar and semantic-option versions.
- [ ] Incremental affected output matches full-run output.

## Risks

- Perl's runtime `@INC`, import hooks, AUTOLOAD, aliases, and symbol-table mutation cannot be reproduced statically.
- Over-expanding reverse dependencies can erase incremental performance gains.
- Under-expanding dependencies can leave stale resolved references after package/subroutine changes.

## Success Criteria

- Evidence-backed project-local references resolve consistently, and all other targets retain explicit uncertainty.
- A one-file change reparses only the necessary dependency closure.
- Deletes and renames do not leave stale facts after a successful run.
- Cache hits and incremental mode preserve the same normalized semantics as a clean full analysis.

