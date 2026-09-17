# Phase 02: Isolated Clang worker and semantic identity

## Context

The repository contains an isolated Clang recovery worker, but `libclang` is not
installed by the standard requirements, the real worker test currently fails,
and the parser emits a callee name while leaving `callee_id=None`. This phase
turns Clang from an optional whole-payload recovery parser into a versioned
semantic callsite evidence provider operating in shadow mode.

## Requirements

- Package and health-check one pinned Clang backend on every supported target.
- Preserve the existing disposable worker, argument filtering, containment,
  timeout, RSS/output, no-network, and no-store-handle guarantees.
- Emit caller and referenced-callee semantic identities rather than names only.
- Preserve linkage, TU/configuration, spelling/expansion, diagnostics, and
  resolution kind.
- Do not publish consumer-visible `CALLS` during this phase.
- Compare libclang/CIndex and LibTooling against the same output contract and
  select one production backend with evidence.
- Accept mapped Pro*C generated or validated virtual C/C++ only through an
  explicit source-bundle request; never treat raw `.pc`/`.pcc` as ordinary C.
- Return generated span, original mapping reference, generated-code class, and
  redacted precompiler fingerprint without exposing raw commands or secrets.

## Architecture

Use a JSON-in/JSON-out semantic worker protocol separate from graph payload
construction. One request identifies a source path, normalized validated compile
arguments, root, source/context fingerprints, limits, and requested schema.
One response contains classified callsite evidence, coverage, diagnostics,
dependencies, resource usage, and a typed terminal outcome.

For Pro*C, the request also carries the immutable `ProcSourceBundle` ID,
allowlisted generated artifact, source-map ID/hash, original file identity, C
or C++ mode, and mapping policy. The worker reads only the generated/validated
semantic input. Reconciliation to original source remains outside the worker so
the same protocol supports different map providers and prevents Clang from
becoming the authority for SQL or source ownership.

The CIndex implementation should use referenced declarations and USRs directly.
Internal-linkage identities add file/TU disambiguation. Virtual, indirect, and
dependent calls are classified rather than forced into a direct target.

## Related files

- `requirements.txt`
- `code-tiny/requirements.txt`
- `code-tiny/tools/cplus/clang_parser.py`
- `code-tiny/tools/cplus/clang_worker.py`
- `code-tiny/tools/cplus/parse_recovery.py`
- worker protocol/semantic contract from Phase 01
- `tests/test_cplus_clang_worker.py`
- `tests/test_cplus_parse_recovery.py`
- new semantic-worker contract tests
- [Pro*C component map](pro-c-component-map.md)

## Implementation steps

1. Define supported OS/architecture, Clang version, native library/resource-dir,
   Python binding or sidecar binary, and startup health contract.
2. Pin and install the selected CIndex dependencies in a reproducible fixture;
   make an unavailable or mismatched runtime a typed readiness failure.
3. Extend the worker request/response version without weakening current
   sanitization, containment, and resource caps.
4. Extract caller semantic identity and referenced callee USR/linkage data for
   direct calls; retain source name/display fields only as presentation data.
5. Classify constructors, methods, overloaded operators, virtual calls,
   function pointers, dependent templates, macros, and unresolved expressions.
6. Capture spelling and expansion locations where supported and retain the
   evidence needed for deterministic callsite identity.
7. Emit Clang dependency/diagnostic/context evidence without absolute-path or
   raw-command leakage in normal artifacts.
8. Run the same gold cases through CIndex and a minimal LibTooling prototype or
   documented capability experiment. Select LibTooling only if CIndex misses
   required contract fields or fails accuracy/stability gates; retain the same
   external worker protocol either way.
9. Run semantic extraction in shadow mode and produce a comparison artifact;
   never replace Tree-sitter structure or publish calls yet.
10. Add worker fixtures for mapped Pro*C C and C++ output, precompiler-generated
    wrappers/runtime calls, original application calls, line directives,
    missing/stale maps, and a raw `.pc` rejection case.
11. Prove request validation contains generated artifacts, strips or rejects
    credential-bearing precompiler options, and returns only normalized relative
    source identities plus redacted fingerprints.

## Todo

- [x] Pin and package the Clang runtime and readiness probe.
- [x] Version the semantic worker protocol.
- [x] Emit caller/callee semantic identities and resolution classes.
- [x] Add macro, virtual, indirect, template, linkage, and failure fixtures.
- [x] Compare CIndex and LibTooling against the contract.
- [x] Select and document the production backend.
- [x] Make the real worker suite pass without mocks or silent skips.
- [x] Produce a shadow semantic-evidence report.
- [x] Support `ProcSourceBundle` worker requests without raw Pro*C parsing.
- [x] Emit generated spans/classes and source-map references for reconciliation.
- [x] Pass Pro*C artifact containment, secret redaction, and raw-source rejection
  tests.

## Risks

- Binding and native library versions may import successfully but be ABI
  incompatible.
- Incorrect compile arguments can produce plausible but incomplete semantics.
- USR alone can merge file-local or configuration-specific entities incorrectly.
- Macro expansion and source locations differ between interfaces.

Mitigate with version/resource-dir health checks, toolchain fingerprints,
linkage/TU disambiguation, diagnostic/coverage gates, and exact gold assertions.

## Success criteria

- Real workers start and pass on every supported platform profile.
- Direct-call evidence contains semantic caller/callee identities and never
  relies on later name/scope/arity promotion.
- Non-direct cases retain their explicit resolution class.
- Worker crash, timeout, OOM, malformed response, unsafe argument, external
  path, or runtime mismatch cannot publish evidence or fail the coverage scan.
- The CIndex/LibTooling selection is recorded with corpus and operational
  evidence, not preference.
- Pro*C worker output is bound to one original/generated/map/configuration
  bundle, and wrapper/runtime/unmapped calls cannot be confused with original
  application callsites.
