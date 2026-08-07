# Phase 03: Classification, provenance, and cache identity

## Context

Recovered Tree-sitter payloads currently enter later extraction and graph stages
without a stable trust tier. Parse cache identity does not fully encode grammar,
compile context, fallback backend, encoding decision, or recovery policy.

## Requirements

- Classify every C/C++ payload into one deterministic quality tier.
- Attach backend, language, compile context, policy, and selection provenance.
- Preserve partial evidence while identifying strong relations that require a
  trusted parse.
- Invalidate only cache entries whose source or parse context changed.
- Cache terminal non-improvement so unchanged bad files are not retried forever.

## Architecture

Add an analyzer-independent context fingerprint to `analyzer_cache.py` and store
the Phase 01 quality record beside the payload. C/C++ extraction supplies semantic
yield after the initial AST walk, then finalizes the tier before caching. Full
diagnostics remain in the artifact; payloads carry only compact provenance.

## Related files

- `code-tiny/tools/common/analyzer_cache.py`
- `code-tiny/tools/common/parse_quality.py`
- `code-tiny/tools/cplus/cplus_analyzer.py`
- `code-tiny/tools/cplus/clang_parser.py`
- `tests/test_parse_quality_contract.py`
- `tests/test_cplus_parse_recovery.py` (new)
- `tests/test_cplus_graph_runtime.py`

## Implementation steps

1. Define a canonical parse-context fingerprint covering source hash, selected
   grammar/version, backend/version, normalized compile flags, encoding, masking,
   and recovery-policy version.
2. Extend cache read/write validation without breaking unrelated analyzer keys.
3. Finalize quality tier after semantic-yield collection and add compact
   provenance to the file payload.
4. Mark relation categories as weak evidence or strong structural evidence for
   Phase 05 publication policy; do not change graph writes yet.
5. Persist candidate winner/non-improvement outcome using the same context key.
6. Add targeted invalidation tests for compile database, grammar, encoding, and
   policy changes.

## Todo

- [x] Add canonical context fingerprints and schema migration behavior.
- [x] Attach compact quality/provenance to C/C++ payloads.
- [x] Define strong versus weak extracted evidence categories.
- [x] Cache selected and terminal non-improvement outcomes.
- [x] Verify unrelated analyzer caches remain compatible.

## Risks

Over-broad invalidation defeats incremental performance; under-broad invalidation
preserves stale ASTs. Test one changed fingerprint dimension at a time.

## Success criteria

Every C/C++ payload is traceable to a quality/backend/context fingerprint;
unchanged cache hits are stable; changing any parse-context dimension invalidates
only the affected entry; non-improving candidates are not repeated.
