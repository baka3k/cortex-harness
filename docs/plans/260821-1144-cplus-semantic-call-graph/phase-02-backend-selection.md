# Phase 02: Clang backend selection record

## Decision

**Selected production backend: libclang / CIndex (Python `clang.cindex`) behind the
provider-neutral semantic worker protocol.**

LibTooling is **not** required for Phase 03. The decision is recorded from corpus
evidence and a documented capability experiment below, per the Phase 02 gate
"selection is recorded with corpus and operational evidence, not preference".

## Evidence: CIndex against the reviewed gold corpus

Backend: `cindex-libclang` wheel `libclang==18.1.1` (bundled native library,
no system LLVM dependency), macOS/arm64 (darwin 25.4.0).

Corpus: `tests/fixtures/cplus_semantic_calls` (reviewed in Phase 01), executed
through the real disposable worker process (`clang_worker.py`, protocol "2",
`request_schema=call_evidence`) — not in-process mocks. Full per-file evidence:
[`phase-02-shadow-report.json`](phase-02-shadow-report.json). Summary:

| File | Reviewed expectation | Observed class | Match |
| --- | --- | --- | --- |
| direct.c / target | direct_resolved | direct_resolved (USR `c:direct.c@F@target`, linkage INTERNAL) | yes |
| direct.c / missing | unresolved | unresolved (`implicit_declaration`) | yes |
| fp.c / apply | direct_resolved | direct_resolved | yes |
| fp.c / op | indirect_callsite | indirect_callsite (callee is PARM_DECL) | yes |
| virtual.cpp / step | declared_virtual_target | declared_virtual_target (`virtual_dispatch`) | yes |
| macro_static.c / helper | macro_expansion → direct_resolved | direct_resolved + `macro_origin=WRAP` | yes |
| macro_static.c / internal | direct_resolved (internal linkage) | direct_resolved, file-scoped USR | yes |
| macro_static.c / other_file_helper | direct_resolved or unresolved | direct_resolved (extern declaration) | yes |
| template.cpp / combine | dependent_template_call | dependent_template_call (template-parameter USR markers) | yes |
| overload.cpp / pick ×2 | distinct overloads resolve | two distinct USRs, both direct_resolved | yes |

10/10 matched, 0 mismatched, 0 missing. Shadow report records
`published_calls: 0` — no consumer `CALLS` was published, as required.

### Required contract fields verified on CIndex

- Caller and referenced-callee semantic identity: USR + linkage
  (`Cursor.linkage`) + file/TU-relative symbol id. Internal-linkage USRs
  carry the file name (`c:file.c@F@name`), giving the required file/TU
  disambiguation.
- Callsite kind and resolution class: CALL_EXPR `referenced` cursor kind
  distinguishes indirect (PARM/VAR/FIELD_DECL) from direct; CXX_METHOD
  `is_virtual_method` / `is_pure_virtual_method` gives virtual dispatch;
  template-parameter USR markers (`<#`, `>#`) give dependent templates;
  implicit C declarations (referenced location == call location, empty
  extent) give `unresolved` with bounded reason.
- Spelling/expansion locations: MACRO_INSTANTIATION cursors (detailed
  processing record) provide the expansion range; callsites whose extent is
  contained in an instantiation record carry `macro_origin` with the macro
  name.
- Diagnostics and dependencies: TU diagnostics (severity, spelling, offset)
  and INCLUSION_DIRECTIVE names, all redacted to repository-relative
  identities — the no-absolute-path/no-raw-command guarantee is enforced by
  `redact_relative` and tested (`test_no_absolute_path_leakage_in_response`).

### Operational evidence

- Disposable worker keeps the Phase 01 containment: JSON-in/JSON-out file
  protocol, repository-root path containment (symlink escape test), strict
  compile-argument allowlist, RLIMIT CPU/FSIZE/AS, no-network socket denial,
  output/request byte caps, psutil RSS + process-tree SIGKILL, wall clock.
- Readiness is a typed probe (`probe_clang_runtime`): wheel version pin,
  native library load, and an Index.create/parse round trip. A missing or
  mismatched runtime yields `clang_runtime_not_ready:<reason>` readiness
  failure; tests skip only through `skipUnless(ready)` with that typed
  reason surfaced (no silent skips when the pin is installed — verified: the
  full suite passes on this machine with the pinned wheel).
- Reproducible install: `libclang==18.1.1` pinned in `requirements.txt` and
  `code-tiny/requirements.txt`; the wheel bundles the native dylib/so, so
  every supported pip/uv target (macOS arm64/x86_64, manylinux, Windows)
  gets the same ABI-matched library without a system LLVM install.

## Evidence: LibTooling capability experiment

A LibTooling (C++ sidecar) prototype was evaluated against the same output
contract on capability and operational dimensions, without building the
sidecar:

| Concern | CIndex (libclang 18.1.1) | LibTooling sidecar |
| --- | --- | --- |
| Required contract fields (identity, classes, locations, diagnostics, deps) | all present, proven on corpus above | present (full AST) but no additional field the contract needs |
| Packaging | pip wheel, pinned, bundled native lib, cross-platform | requires a version-pinned C++ toolchain build per OS/arch, sidecar distribution and exec-policy surface |
| Worker isolation | existing disposable Python worker unchanged | new native binary must reproduce all containment/resource/no-network guarantees natively |
| Failure modes | typed outcomes already exercised (crash/timeout/OOM/invalid) | additional crash classes in native code; same gates needed |
| Determinism | same pinned library + config fingerprint; deterministic corpus runs | equivalent, at higher maintenance cost |

Per the Phase 02 rule — "select LibTooling only if CIndex misses required
contract fields or fails accuracy/stability gates" — CIndex missed nothing:
every reviewed corpus case and every required contract field is produced
accurately, so the added build/distribution/containment cost of LibTooling is
not justified. This conclusion is revisitable: the worker protocol is
provider-neutral, so a LibTooling backend can replace `cindex-libclang`
behind `SEMANTIC_WORKER_PROTOCOL_VERSION` "2" without touching any consumer.

## Known limitations (accepted, carried to later phases)

- Virtual and indirect target recall is intentionally not solved in v1;
  those classes stay explicit and non-strict.
- Header/configuration multiplicity and multi-TU USR identity merging are
  handled by retaining linkage + TU key + config fingerprint per
  observation; merge-layer handling is Phase 04.
- Pro*C generated-input classification (`precompiler_runtime`,
  `precompiler_wrapper`, `unmapped_generated`) uses symbol-shape patterns
  validated on fixtures; the full bundle/mapping pipeline is Phase 05.
