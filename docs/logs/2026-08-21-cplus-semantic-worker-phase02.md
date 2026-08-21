# C/C++ semantic worker with USR identity and classified callsites (Phase 02) — 2026-08-21

## Context

Phase 01 established the call-evidence contract but the Clang backend was
optional and broken: `libclang` was not installed, the real worker test
failed, and the candidate parser emitted `callee_name` with `callee_id=None`.
Phase 02 of
[`plans/260821-1144-cplus-semantic-call-graph`](../plans/260821-1144-cplus-semantic-call-graph/plan.md)
turns Clang into a versioned, isolated semantic callsite-evidence provider
operating strictly in shadow mode.

## Change

- Pinned `libclang==18.1.1` (wheel bundles the native library) in
  `requirements.txt:16` and `code-tiny/requirements.txt:12`; typed readiness
  probe `probe_clang_runtime` in `code-tiny/tools/cplus/semantic_worker.py`
  (version pin + native load + parse round-trip; mismatch is a typed
  readiness failure, never a silent skip).
- New semantic worker protocol "2" (`request_schema=call_evidence`),
  JSON-in/JSON-out, separate from graph payloads: request carries root,
  contained path, sanitized compile args, fingerprints, limits; response
  carries classified callsites, coverage, redacted diagnostics/dependencies,
  and typed terminal outcome. Dispatch in
  `code-tiny/tools/cplus/clang_worker.py:_run_semantic_request`;
  `run_semantic_worker` wrapper in `code-tiny/tools/cplus/parse_recovery.py`
  keeps all disposable-worker guarantees (RSS/output caps, process-tree
  SIGKILL) and now prefers a worker's own typed JSON failure over a stderr
  tail.
- CIndex extraction emits caller/callee USR identities with linkage and
  TU-relative symbol ids, plus resolution classes: `direct_resolved`,
  `declared_virtual_target` (virtual/pure-virtual), `indirect_callsite`
  (callee is PARM/VAR/FIELD_DECL), `dependent_template_call` (template USR
  markers), `unresolved` (implicit C declarations: referenced location ==
  call location with empty extent), and macro-origin evidence via
  MACRO_INSTANTIATION range containment.
- Pro*C: `ProcBundleRequest` accepts only an allowlisted, hash-bound
  generated artifact with source-map reference and mapping policy; responses
  carry generated-code classes (`precompiler_runtime`/`precompiler_wrapper`/
  `unmapped_generated`/`original_application`) and a redacted precompiler
  fingerprint. Raw `.pc`/`.pcc` paths and credential-bearing options are
  rejected before parsing.
- Shadow-mode runner `code-tiny/tools/cplus/semantic_shadow.py` plus the
  committed comparison artifact
  `plans/260821-1144-cplus-semantic-call-graph/phase-02-shadow-report.json`
  (10/10 reviewed expectations matched, `published_calls: 0`).

## Impact

The real worker suite passes without mocks (`tests/test_cplus_clang_worker.py`,
`tests/test_cplus_semantic_worker.py`: 19 tests). No consumer-visible `CALLS`
is published — evidence is shadow-only until the Phase 04+ gates. Risk: low
(additive protocol; recovery worker behavior preserved; the 51 pre-existing
failures elsewhere in the suite are unrelated and predate this change).

## Decision

CIndex (libclang) selected as the production backend over a LibTooling
sidecar, recorded with corpus and operational evidence in
`plans/260821-1144-cplus-semantic-call-graph/phase-02-backend-selection.md`:
CIndex produced every required contract field accurately on the reviewed
corpus, so LibTooling's build/distribution/containment cost was not
justified. The protocol stays provider-neutral so the backend can be swapped
without consumer changes. Known accepted limitations (virtual/indirect
target recall, multi-configuration merge, full Pro*C mapping) are carried to
Phases 04/05.

## References

- plan: ./plans/260821-1144-cplus-semantic-call-graph/phase-02-clang-worker-and-usr-identity.md
- commit: 422cefd
- previous: ./docs/logs/2026-08-21-cplus-call-evidence-containment.md
