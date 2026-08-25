# Rust Relationship Endpoint Integrity — 2026-08-25

## Context

The `oh-my-pi` Rust full scan failed closed with `relationship_cardinality_mismatch`: the analyzer completed message extraction but emitted relationships whose declared endpoints could not all be reconciled, so the sync was marked dirty and graph publication did not proceed. Investigation confirmed the primary defect was `impl_item` ownership: method scopes were derived from a generic extracted name instead of the implemented type, producing invalid `DECLARES` owners. Correcting that exposed a second, previously masked defect: an `ALIASES` edge used the full target ID (for example, `brush_core::Error`) while type-use tokenization only materialized a shortened target such as `Error` (`code-tiny/tools/rust/rust_analyzer.py:570`).

## Change

The Rust analyzer now:

- Registers types through one replacement-aware path so a concrete definition can replace an earlier external placeholder without duplicating IDs (`code-tiny/tools/rust/rust_analyzer.py:309`).
- Reads the implemented type directly from the `impl_item` type field, unwraps generic/reference/pointer wrappers, and resolves its qualified owner (`code-tiny/tools/rust/rust_analyzer.py:354`). Impl traversal then scopes child methods under that owner and materializes an external owner when the type is not locally declared (`code-tiny/tools/rust/rust_analyzer.py:719`).
- Resolves `DECLARES` sources only to registered Type or Namespace endpoints, for both nested types and functions (`code-tiny/tools/rust/rust_analyzer.py:382`, `code-tiny/tools/rust/rust_analyzer.py:677`, `code-tiny/tools/rust/rust_analyzer.py:771`).
- Materializes the exact alias target ID before emitting `ALIASES`, closing the full-path versus tokenized-type gap (`code-tiny/tools/rust/rust_analyzer.py:796`).

Regression coverage verifies that trait and generic impl methods belong to their implemented Type rather than a Namespace, and that every explicit relationship has both endpoints materialized (`tests/test_rust_relationship_integrity.py:36`, `tests/test_rust_relationship_integrity.py:78`).

## Impact

**Level: medium.** The change affects Rust graph ownership and endpoint materialization, not source parsing or message extraction. It prevents fail-closed syncs caused by orphaned relationships and improves graph accuracy for impl methods and qualified type aliases. The main residual risk is broader Rust type syntax beyond the tested generic, trait-impl, nested-type, and qualified-alias forms.

## Decision

Endpoint closure is enforced at relationship construction instead of weakening the cardinality check. Parsing the explicit `impl_item` type field was chosen because it represents Rust ownership semantics directly; falling back to namespace ownership would publish a structurally valid but incorrect graph. Exact alias targets are materialized in addition to tokenized `USES_TYPE` targets because `ALIASES` must reference the same ID it emits. External placeholders remain replaceable so later concrete declarations preserve one stable endpoint ID.

Verification evidence:

- Re-analysis produced **24,973 relationships with 0 unresolved endpoints**.
- Relevant regression suites passed with **23 tests plus 13 subtests**.
- The `code-tiny` suites passed with **88 tests plus 24 subtests**.
- The whole repository test run still reported unrelated pre-existing baseline failures; no failure was attributed to these Rust analyzer changes.

## References

- Implementation: `code-tiny/tools/rust/rust_analyzer.py:309`
- Impl owner resolution: `code-tiny/tools/rust/rust_analyzer.py:354`
- Relationship endpoint selection: `code-tiny/tools/rust/rust_analyzer.py:382`
- Alias endpoint materialization: `code-tiny/tools/rust/rust_analyzer.py:814`
- Regression tests: `tests/test_rust_relationship_integrity.py:35`

