# C/C++/Pro*C CALLS containment via versioned call-evidence contract — 2026-08-21

## Context
The C/C++ graph path resolved Tree-sitter `call_expression` candidates with
name/scope/file/arity heuristics and published them as strict `CALLS` edges,
so the graph asserted semantic certainty it never had. Phase 01 of
`plans/260821-1144-cplus-semantic-call-graph/` makes the graph honest before
any Clang semantic coverage is added (Phase 02+).

## Change
- New canonical contract `code-tiny/tools/common/call_evidence.py`: resolution
  classes (`direct_resolved` … `unresolved`), approved semantic providers
  (only `clang_worker`; never `tree_sitter`), strong-evidence predicate,
  run-independent callsite identity, `SemanticCoverageRecord`, and the
  `ProcSourceBundle` identity with repository-contained generated-artifact
  references.
- `code-tiny/tools/cplus/cplus_analyzer.py`: heuristic-resolved calls now
  publish site-level `POSSIBLE_CALLS` with `resolution_class=lexical_candidate`;
  unresolved calls stay `UNKNOWN_CALL` with `resolution_class=unresolved`.
  The strict `buf_calls` lane is reserved for the Phase 02 semantic worker.
  `_call_site_id` delegates to the canonical formula (identical UUIDv5 output,
  so site identities are unchanged).
- Enforcement at two boundaries: `payload_validation.validate_cplus_payload`
  migrates legacy rows to `lexical_candidate`, demotes incomplete or
  unaccepted `direct_resolved` claims (recording `demoted_from`), and
  quarantines unknown classes; `LanguageCodeWriter.write_calls_with_site`
  re-asserts via `enforce_strong_call_row` (marker-based so delphi/android
  lanes are unaffected).
- Journaled weak-edge path: `write_possible_calls_with_site` +
  `possible_calls:site` journal operation with a dedicated replay compiler, so
  journal replay can never materialize weak evidence as `CALLS`.
- Fail-closed cache migration: `PAYLOAD_SCHEMA_VERSION` 1.0→1.1 and the cplus
  parse-cache schema bumped.
- Reviewed corpus `tests/fixtures/cplus_semantic_calls/` (7 cohorts: direct,
  overload, virtual, function pointers, macro/internal-linkage, template,
  Pro*C) with `expected.json` and a committed `baseline.json`; tests in
  `tests/test_cplus_call_evidence.py`.

## Impact
All C/C++/Pro*C consumers see `POSSIBLE_CALLS`/`UNKNOWN_CALL` instead of
`CALLS` from the Tree-sitter plane — deliberate containment, not a parser
regression. Risk: medium (graph shape change for C++ lanes; strict-`CALLS`
queries return nothing until Phase 02 publishes semantic evidence). Other
language analyzers are unaffected. Rollback: revert to heuristic `CALLS` from
this commit.

## Decision
Downgrade-first per the plan's conditional decision: conservative containment
before semantic coverage, with the writer guard kept marker-based (opt-in)
rather than universal because delphi/android still write contract-less
site calls; their lanes migrate in later phases. Reviewer-driven fixes: the
first cut wrote possible-calls through an unjournaled custom query that would
have aborted journal-required ingest, and validation only demoted
unaccepted-callee claims — both corrected before commit.

## References
- plan: ./plans/260821-1144-cplus-semantic-call-graph/phase-01-edge-contract-and-containment.md
- commit: 73e3349
