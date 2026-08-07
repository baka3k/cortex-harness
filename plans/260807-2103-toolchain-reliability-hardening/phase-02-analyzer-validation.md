# Phase 02: Analyzer validation and quarantine boundary

## Context

File-level parse quality does not guarantee valid extracted records. The
incident included function identities containing line breaks, preprocessor
fragments, and comment text even when the file tier was `clean` or `recovered`.
Those records reached graph construction because the evidence policy is
produced but not enforced.

## Requirements

- Validate records before graph/vector mutation.
- Keep shared invariants language-neutral and allow analyzer-specific adapters.
- Reject control characters and structurally impossible identities without
  rejecting legitimate language forms such as C++ operators or Unicode names.
- Enforce quality/evidence policy for nodes, calls, and relationships.
- Suppress all dependent effects of quarantined records.
- Preserve exact accounting and bounded diagnostics.
- Do not make one malformed record fail a full scan unless configured policy or
  thresholds require terminal failure.

## Architecture

Introduce a validated payload envelope containing accepted nodes, relations,
calls, vectors, and quarantine records. Build an accepted identity registry by
label/project/source. Validate relations against that registry before any
storage call.

Validation stages:

1. required keys and JSON-safe types;
2. normalized relative path and source ownership;
3. source-span and line/byte consistency;
4. lexical plausibility and forbidden controls;
5. deterministic identity derivation and duplicate merge policy;
6. label/relationship allowlist and required endpoint semantics;
7. parser-quality/evidence-policy enforcement;
8. artifact privacy, item, and byte limits.

For C/C++/Pro*C, reason codes should distinguish malformed declarator capture,
preprocessor leakage, comment leakage, damaged scope, conflicting duplicate,
invalid span, missing owner, quarantined file quality, and unresolved reference.

## Related Files

- `code-tiny/tools/common/parse_quality.py`
- New payload validation/identity registry module under `tools/common/`
- `code-tiny/tools/cplus/cplus_analyzer.py`
- `code-tiny/tools/cplus/parse_recovery.py`
- `code-tiny/tools/cplus/proc_analyzer.py`
- analyzer cache schema/version and incremental artifact integration
- graph/vector payload adapters

## Implementation Steps

1. Finalize the envelope and validator interfaces from Phase 01.
2. Implement shared schema, path, span, accounting, identity, and referential
   validators with deterministic reason codes.
3. Add C/C++/Pro*C symbol plausibility validation based on parser node kind and
   source span, not a single global name regex.
4. Enforce `strong_relations_allowed` and per-record evidence policy before
   calls/containment/inheritance reach the writer.
5. Normalize duplicates with explicit keep/merge/conflict behavior; conflicts
   quarantine rather than silently choosing a row.
6. Generate run-scoped quarantine JSON with counts, bounded samples, source
   relative path, reason, provenance, and dependent-effect counts.
7. Version cache identity so pre-validation cache payloads are revalidated.
8. Add adapters in risk order after the C/C++ canary: direct graph writers,
   framework overlays, Android/Java, TypeScript, shell/legacy analyzers, and
   topology.

## Todo

- [ ] Shared envelope/accounting validator passes property-based tests.
- [ ] C/C++/Pro*C malformed symbol fixture is quarantined before mutation.
- [ ] Dependent relations, calls, and vectors cannot reference quarantined IDs.
- [ ] Evidence policy has an enforced consumer and coverage test.
- [ ] Cache compatibility cannot bypass new validation.
- [ ] Quarantine artifacts are bounded, private, and deterministic.
- [ ] Analyzer adapter inventory reports migrated versus uncertified tools.

## Risks

- False-positive quarantine reduces semantic yield. Use parser-kind/source-span
  validation, reviewed fixtures, thresholds, and before/after reports.
- Duplicate normalization can change final properties. Declare per-label merge
  policy and test ordering/idempotency.
- Some analyzers lack spans/provenance. Support explicit capability levels but
  never waive identity and referential accounting.

## Success Criteria

- The motivating malformed functions cannot reach graph or vector writers.
- `discovered = accepted + quarantined + rejected` for every analyzer payload.
- All required relations reference accepted identities before storage begins.
- A bad record yields a quarantine result, not a traceback or partial mutation.
- C/C++/Pro*C semantic-yield changes are reviewed before enforcement rollout.
