# Pro*C source-map contract and mask alignment fixes (Phase 05) — 2026-08-21

## Context
Plan `260821-1144-cplus-semantic-call-graph` Phase 05 required the Pro*C
original→generated source bridge: a versioned source-map contract,
reconciliation of Clang semantic observations to original `.pc`/`.pcc`
spans, generated-code filtering, bundle lifecycle states, and cache
invalidation coupling. Phases 01–04 had already delivered
`ProcSourceBundle`, `ProcArtifactManifest`, the protocol-2 worker, and the
evidence merge; the map/reconciliation layer and mask-fingerprint coupling
were missing.

## Change
- New `code-tiny/tools/cplus/proc_source_map.py`: mapping-quality states
  (`exact_span/exact_line/line_directive/inferred/missing/stale/invalid`),
  sidecar and `#line` providers behind one contract, typed span lookup
  (mapped/missing/conflict/stale/invalid), bundle state machine
  (`sql_only`…`failed`), strict fail-closed gate, and
  `reconcile_proc_semantic_callsites` (original identity + generated
  provenance, typed reject reasons, runtime/wrapper/unmapped filtering).
- `cplus_analyzer.py`: static `masking_fingerprint="proc-v1"` replaced by
  versioned `proc_masking_fingerprint(source_sha256)` in both
  `ParseContext` sites (parse + cache signature).
- `proc_manifest.py`: imports shared version constants from
  `proc_source_map` (no duplicate version strings).
- `proc_analyzer.py` scanner fixes (adversarial corpus exposed them):
  1. indented `EXEC SQL` double-emitted indentation → mask length grew;
  2. raw-string branch unreachable (`ch2 == "R"` contradiction);
  3. raw-string end marker `)"'+delim` instead of `)'+delim+'"`;
  4. multi-line `EXEC SQL` masks replaced newlines with spaces.
- Tests/fixtures: `tests/test_proc_source_map.py` (19 tests),
  `tests/fixtures/proc_source_map/`, and a deliberate
  `cplus_semantic_calls/baseline.json` update (proc.pc `commit_work()`
  moved unknown → lexical candidate after correct alignment).

## Impact
All consumers of masked Pro*C parsing: tree-sitter structure, enclosing
function joins, parse cache keys (old `.pc` entries invalidate once),
lexical call classification. Risk: low-medium — behavior changes are
invariant-restoring (length/newline preservation), all cplus/proc/semantic
test lanes green; full suite 51 failed / 902 passed vs 56/897 on clean
tree (remaining failures pre-existing in unrelated lanes).

## Decision
- Strict gate accepts only `exact_span` by default; `exact_line` promotion
  behind `ALLOW_EXACT_LINE_PROMOTION=False` until the reviewed corpus
  proves column reconstruction.
- Rejection precedence: generated runtime/wrapper class beats map-missing
  so generated traffic accounting is unambiguous.
- Scanner corrections kept minimal and invariant-focused per the Pro*C
  owner plan (`260804-1640`) coordination rule.
- Phase 06 (schema/journal publication of new staging rows) deferred by
  design.

## References
- plan: ./plans/260821-1144-cplus-semantic-call-graph/phase-05-proc-semantic-source-mapping.md
- report: ./plans/260821-1144-cplus-semantic-call-graph/phase-05-report.md
- commit: 15ffa2e
