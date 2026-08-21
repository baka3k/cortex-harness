# C++ semantic worker Phase 02 review fixes — 2026-08-21

## Context

Full hi-craft review of commit `422cefd` (Phase 02 semantic worker, plan
`./plans/260821-1144-cplus-semantic-call-graph/phase-02-clang-worker-and-usr-identity.md`)
scored 7/10 with four major findings. This entry closes them.

## Change

- `code-tiny/tools/cplus/semantic_worker.py:227` — `redact_relative` now checks
  containment explicitly; on POSIX `os.path.relpath` never raises for foreign
  paths, so external diagnostics files leaked as `../../../usr/include/...`.
- `code-tiny/tools/cplus/semantic_worker.py` — traversal now visits
  `CXX_CONSTRUCT_EXPR` and `classify_call` returns a new explicit
  `constructor_call` resolution class (added to
  `code-tiny/tools/common/call_evidence.py` `RESOLUTION_CLASSES`); also
  reordered the pure-virtual check that was unreachable behind
  `is_virtual_method()`.
- `code-tiny/tools/cplus/semantic_shadow.py` — replaced ad hoc expectation
  string prefix matching (one branch was tautological) with an explicit
  `_EXPECTATION_ALLOWED_CLASSES` table plus falsifiable predicates for macro
  origin and distinct overload USRs; unknown expectations fail closed.
- `code-tiny/tools/cplus/clang_worker.py` — `_error_response`/`_redact_error_text`
  strip absolute paths from exception text in both typed `invalid` responses,
  closing an OSError absolute-path leak.

## Impact

All Phase 02 safety claims (no external/absolute path leakage) are now actually
enforced and tested; constructor and operator-syntax callsites are captured
with explicit classes instead of silently missing. Risk: low (additive class,
shadow report only). 77 cplus tests pass, no behavior regressions.

## Decision

Constructor calls get their own resolution class rather than being forced into
`direct_resolved`, per the Phase 01 contract that construction evidence is not
a plain CALLS target. Shadow expectations use a closed table so the shadow
report can genuinely fail.

## References

- plan: ./plans/260821-1144-cplus-semantic-call-graph/phase-02-clang-worker-and-usr-identity.md
- commits: 422cefd (reviewed), 31fe2a5 (fixes)
- tests: tests/test_cplus_semantic_worker.py
