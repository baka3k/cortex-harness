# Phase 03: Faithful-context dual-plane orchestration

## Goal

Connect the already implemented context registry, semantic cache, bounded
protocol-2 worker, and evidence merge to the real analyzer/sync path. Clang is
useful without a successful whole-project build only where one TU/configuration
has faithful frontend inputs.

Runtime wiring starts only after
`260807-0929-mcp-ingest-query-concurrency` exposes the single admitted job/
generation owner. Component contracts and fixtures may be prepared earlier,
but no second scheduler or provisional publisher is allowed.

## Runtime contract

```text
source inventory
  -> Tree-sitter structural payload (always)
  -> lexical call evidence (always weak)
validated compile context
  -> faithful? -> bounded Clang protocol 2 -> semantic observations
  -> otherwise -> explicit coverage reason, zero strict observations
both evidence streams
  -> deterministic merge, never structural replacement
```

Whole-project link, test, or package failure does not make an otherwise faithful
TU ineligible. Missing/stale flags, target, macros, headers, generated inputs,
or source/dependency fingerprints do.

## Files and symbols

- `code-tiny/tools/cplus/cplus_analyzer.py`
  - `build_call_graph`, `raw_payload_for`, callsite buffer/write path, `parse_args`
- `code-tiny/tools/sync/incremental_sync.py`
  - `_build_analyzer_cmd`, C/C++ policy forwarding and run artifacts
- `cortex_harness/storage/gateway.py`, `generation.py`, `admission.py`
  - sole job admission, physical-target ownership, and generation lifecycle
- `cortex_harness/dev.py`
  - sync-code option forwarding/status surface
- `code-tiny/tools/cplus/semantic_context.py`
  - `CoverageState`, `RegisteredContext.eligible`, `ContextRegistry`
  - `SemanticCache`, `BoundedSemanticScheduler`, `build_baseline_report`
- `code-tiny/tools/cplus/parse_recovery.py`
  - `load_compile_database`, `run_semantic_worker`
- `code-tiny/tools/cplus/semantic_worker.py`
  - `validate_semantic_request`, `build_semantic_response`
- `code-tiny/tools/cplus/evidence_merge.py`
  - `merge_call_evidence`, coverage frontier
- `tests/test_cplus_semantic_context.py`
- `tests/test_cplus_semantic_worker.py`
- new `tests/test_cplus_dual_plane_integration.py`

## Implementation steps

1. Add one explicit C/C++ semantic mode surface: `containment` (default),
   `sparse`, or `comprehensive`. `parse-quality` controls only Tree-sitter
   reporting/same-backend retry; no cross-product combination can enable Clang
   structure. Sparse/comprehensive alone may request protocol 2.
2. Make incremental sync inside the admitted `StoreGateway` job the sole
   orchestration owner. Its bounded worker pool emits artifacts to the job;
   workers never own queues, provider clients, publication, or pointer flips.
3. Load an existing bounded/sanitized compile database as a TU -> configuration
   multimap, not first-command-wins. Canonicalize real working directory,
   compiler family, target/sysroot/toolchain roots, language/dialect, macros,
   includes, forced inputs, and generated/dependency hashes; deterministically
   deduplicate variants and reject unsupported semantic-changing flags. Do not
   execute builds or synthesize missing generated inputs.
4. Separate three axes in the registry: `context_fidelity` = faithful,
   inherited, synthetic, missing; `admission` = accepted/rejected plus reasons;
   `execution_coverage` = not_analyzed, complete, partial, failed, truncated,
   cancelled. Strict eligibility is exactly faithful + accepted + complete +
   matching provenance. `safe_to_parse` is not evidence of faithful build
   context.
5. Treat fidelity as a parent-side attestation derived from a trusted compile
   database origin, freshness, contained dependency graph, and approved roots.
   Serialized requests/responses and worker-supplied fidelity fields are
   untrusted; omitted, self-asserted, forged, or stale attestations fail closed.
6. Before enqueueing, persist an immutable `SemanticScopeManifest` containing
   expected `(project, generation, revision, policy, TU, configuration)` keys.
   Sparse budgets and configuration caps stay visible as `not_analyzed` or
   `variant_cap_exceeded`; they make the scope partial, never silently smaller.
7. Maintain a header/dependency reverse index. A changed shared/generated
   header invalidates every affected variant subject to a visible fan-out
   budget; any deferred affected key remains pending and makes coverage partial.
8. Run protocol 2 under OS-enforced isolation: dedicated low-privilege process
   or container, read-only repository and approved toolchain roots, no network,
   private empty temp, cleared `PYTHONPATH`/loader variables, resource/time/output
   limits, verified interpreter/worker/native-library digests, no-follow path
   containment, and an explicit external-header manifest. Sandbox unavailable
   means noncoverage, not an in-process fallback.
9. Extend request/response provenance with the manifest key, attestation,
   source/dependency/generated hashes, and worker/backend versions. The parent
   independently recomputes and verifies them; Clang diagnostics may reduce
   coverage but zero diagnostics never establishes fidelity/completeness.
10. Harden the semantic cache under a trusted per-project root: no-follow and
    permission checks, locks, atomic rename plus fsync, corruption/orphan
    recovery, and keys including project/root, immutable content horizon
    (revision or dirty-snapshot digest), attestation, configuration, policy,
    worker, and dependencies. Exclude ephemeral publication `generation_id` so
    unchanged content can reuse observations; rebind only after fresh validation
    to the new scope manifest/physical generation. External caches require a
    content digest/authenticator.
11. Feed lexical and eligible semantic observations to deterministic merge,
    preserving per-configuration contradictions. Redact/harden `-D` values,
    paths, diagnostics, errors, cache artifacts, and reports using structured
    secret tainting rather than string-only best effort.
12. Export the scope manifest plus context/admission/coverage/queue accounting
    and stable reason IDs in the sync summary.

## Failure-state expectations

| State | Structural output | Semantic output | Strict publication |
| --- | --- | --- | --- |
| `faithful` + complete | Tree-sitter unchanged | Classified Clang observations | Eligible for later gates |
| `faithful` + partial/failed | Tree-sitter unchanged | Weak/partial or none | Forbidden |
| `inherited`/`synthetic` | Tree-sitter unchanged | Optional shadow evidence | Forbidden |
| `missing`/`rejected` | Tree-sitter unchanged | None | Forbidden |
| worker timeout/crash/OOM | Tree-sitter unchanged | Typed failure | Forbidden |

## Tests

- Build-free fixture with faithful existing command succeeds semantically even
  though no link/build step runs.
- Missing include, generated header, target flag, or context produces explicit
  noncoverage and zero strict edges while retaining every Tree-sitter identity.
- A forged non-empty fingerprint with non-faithful state is rejected.
- Forged/stale/omitted fidelity, symlink escape, malicious include/plugin flags,
  loader-variable injection, cache tampering, and cross-project cache reuse are
  rejected; lack of sandbox yields typed noncoverage.
- Context/source/dependency/config/policy mutations invalidate only expected
  semantic cache entries.
- Two compile variants for one TU remain distinct; header fan-out and variant
  caps leave explicit pending/not-analyzed manifest keys.
- Queue overload, timeout, cancellation, crash, and truncated output cannot
  mutate the structural baseline or claim complete coverage.
- Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_cplus_semantic_context.py \
  tests/test_cplus_semantic_worker.py \
  tests/test_cplus_dual_plane_integration.py \
  tests/test_incremental_sync_parse_quality.py
```

## Acceptance criteria

- Normal analyzer/sync code, not only benchmark/shadow tests, invokes protocol
  2 through the bounded context-aware lane.
- Actual manifest keys equal expected keys and each has exactly one current
  coverage record; no runtime traversal is allowed to define completeness.
- Only faithful, complete, provenance-matched worker results reach the strong
  evidence candidate set.
- Structural payload counts, IDs, ranges, and relations match containment mode
  exactly for the same frozen horizon.

## Todo

- [ ] Add and forward the semantic mode contract.
- [ ] Wire registry/cache/scheduler/worker into the normal path.
- [ ] Normalize all context and worker failure states.
- [ ] Emit complete per-frontier coverage artifacts.
- [ ] Prove build-free faithful-TU behavior and exact invalidation.
