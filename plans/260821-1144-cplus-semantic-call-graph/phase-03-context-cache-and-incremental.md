# Phase 03: Compile context, cache identity, and incremental semantic scheduling

## Context

Clang semantics are valid only for a specific translation unit, command,
headers, generated inputs, target, sysroot, resource directory, and toolchain.
The current compile database is useful for recovery, but full semantic analysis
needs explicit coverage, multiple-configuration handling, dependency-aware
invalidation, and a bounded scheduler suitable for repositories with millions
of lines and repeated headers.

## Requirements

- Treat compile databases as validated data and never execute their commands.
- Index contexts once with bounded file/entry/token/path limits.
- Preserve multiple legitimate configurations without silently merging them.
- Derive header contexts from actual includer/dependency evidence with visible
  confidence and bounded variants.
- Include every semantic input in cache identity and incremental invalidation.
- Use the owning gateway/job system for admission; do not add another global
  scheduler.
- Keep workers bounded by CPU, memory, queue bytes, wall time, and circuit
  breakers while leaving capacity for MCP/control work.
- Model Pro*C compiler mode, redacted precompiler identity/options, generated
  artifact, `EXEC SQL INCLUDE` dependencies, mask/source-map policy, and all
  original/generated hashes as semantic inputs.
- Invalidate the complete Pro*C replacement set when original source,
  generated output, map, include/header, context, toolchain, or policy changes.

## Architecture

Create a normalized compile-context registry keyed by project, source/TU,
configuration fingerprint, and policy. Store normalized arguments, working
directory relative to the approved root, target/sysroot/resource identities,
toolchain version, source/dependency fingerprints, eligibility, and rejection
reason.

Semantic cache identity includes:

```text
source + dependency closure + normalized compile context + target/toolchain
+ semantic worker/schema/policy + Pro*C source-map version when applicable
```

Tree-sitter scan results seed candidate TUs and weak evidence. The semantic lane
queues only eligible contexts. Incremental scheduling consumes changed files,
Clang dependency manifests, compile-context changes, policy/version changes,
and explicit source-map changes; lexical include closure remains a conservative
fallback when semantic dependencies are unavailable.

For Pro*C, context selection is two-layered: the approved precompiler/artifact
manifest proves how one original `.pc`/`.pcc` became a generated C/C++ input,
then the normalized compiler context proves how Clang interprets that generated
translation unit. A missing or stale layer leaves SQL and masked Tree-sitter
coverage intact but makes semantic publication ineligible.

## Related files

- `code-tiny/tools/cplus/bootstrap_compile_commands.py`
- `code-tiny/tools/cplus/parse_recovery.py`
- `code-tiny/tools/cplus/clang_worker.py`
- `code-tiny/tools/cplus/cplus_analyzer.py`
- `code-tiny/tools/common/analyzer_cache.py`
- `code-tiny/tools/common/git_diff.py`
- `code-tiny/tools/sync/incremental_sync.py`
- StoreGateway/job components owned by the concurrency plan
- compile-context, cache, incremental, and benchmark tests
- `code-tiny/tools/cplus/proc_analyzer.py`
- `code-tiny/tools/sync/owner_manifest.py`
- [Pro*C component map](pro-c-component-map.md)

## Implementation steps

1. Reuse the current bounded loader/sanitizer and document gaps for semantic
   contexts, multiple variants, target/sysroot, generated headers, and working
   directories.
2. Add the normalized context registry and stable coverage/rejection reasons.
3. Determine approved configuration-selection policy: all declared variants up
   to a cap, explicit project profiles, and no implicit arbitrary winner.
4. Ingest Clang dependency evidence and build reverse invalidation indexes.
5. Extend cache signatures to every semantic and mapping input; invalidate old
   entries on worker/schema/policy upgrades.
6. Integrate eligible semantic work with the bounded CPU preparation lane and
   stable job/run identity from the concurrency owner.
7. Add backpressure, per-TU limits, rolling non-yield/circuit-breaker metrics,
   cancellation checkpoints, and queue/status visibility.
8. Prove exact incremental behavior for source, header, compile flags, target,
   Clang version, dependency, and policy changes.
9. Measure cold/warm cache, header fan-out, duplicate variant cost, peak RSS,
   throughput, queue growth, and changed-TU p50/p95 before broader scheduling.
10. Define a redacted Pro*C artifact/context manifest with original and
    generated hashes, language mode, precompiler/tool fingerprint, normalized
    approved option fingerprint, include/macro context, map identity, and
    eligibility/rejection reason; never persist credentials or raw commands.
11. Add Pro*C dependency edges for C/C++ includes, resolved `EXEC SQL INCLUDE`,
    generated headers, and Clang manifests with bounded external-path policy.
12. Test exact invalidation for original edits, SQL-only edits, generated/map
    replacement, context/mode/tool version changes, included files, deleted or
    renamed `.pc`/`.pcc`, and a transition from semantic-complete to SQL-only.

## Todo

- [x] Implement normalized compile-context registry and coverage reasons.
- [x] Define bounded multi-configuration and header-context policies.
- [x] Add dependency manifests and reverse invalidation index.
- [x] Complete semantic cache fingerprints and compatibility handling.
- [x] Integrate bounded job admission, status, cancellation, and backpressure (bounded semantic lane; StoreGateway lane reuse deferred to concurrency owner).
- [x] Add exact incremental invalidation tests.
- [x] Publish cold/warm context/cache/scheduler baseline (baseline report builder implemented and contract-tested; representative-data run pending pilot).
- [x] Add the Pro*C compiler/precompiler/artifact context manifest.
- [x] Add Pro*C bundle/cache fingerprints and dependency indexes.
- [x] Prove exact Pro*C invalidation, cleanup, and downgrade behavior.

## Risks

- Synthetic contexts can raise parse yield while resolving the wrong conditional
  branch; they cannot certify semantic `CALLS` without an explicit weaker
  context class.
- One header change may invalidate a large fraction of TUs.
- Multiple configurations can multiply graph, cache, and query size.
- Scheduling based only on lexical call candidates can miss macro/generated
  semantic work.

Mitigate by distinguishing faithful from synthetic/inherited context, preserving
configuration identity, measuring fan-out, bounding variants, and failing closed
on semantic publication.

## Success criteria

- Every semantic observation resolves to one validated context fingerprint and
  coverage state.
- No compile command, response file, plugin, or repository build hook executes.
- Repeating unchanged analysis hits cache; every changed semantic input causes
  exact affected invalidation.
- Worker and queue budgets are enforced and visible under stress.
- The baseline report quantifies coverage, failure reasons, configuration
  multiplicity, cache behavior, latency, RSS, and fan-out on representative data.
- Pro*C cache and coverage distinguish original, masked, generated, map,
  compiler, precompiler, include, worker, schema, and policy inputs; no stale
  strong evidence survives any reviewed change.
