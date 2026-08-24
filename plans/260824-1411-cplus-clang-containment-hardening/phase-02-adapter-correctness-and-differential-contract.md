# Phase 02: Adapter correctness and differential contract

## Goal

Fix real cursor-to-evidence loss and replace misleading total-row comparison
with a label/identity/plane differential. Do not make the legacy Clang adapter
a second owner of repository structure.

## Current evidence

- `_find_enclosing_func` caches sorted extents by `id(func_extents)` while the
  same list keeps growing. Calls in later functions can therefore be dropped.
- The seven checked-in C/C++ fixtures produce 13 Tree-sitter calls versus 9
  legacy-Clang calls. Known losses include `run -> apply` in `fp.c` and both
  calls in `macro_static.c::entry`.
- `cursor.is_definition()` excludes prototypes and pure-virtual declarations;
  `qualified-name/arity@file` collapses same-arity overloads.
- Tree-sitter emits 34 structural relations on this fixture set while the
  legacy adapter emits none. Some count differences are expected lexical facts,
  so raw totals cannot decide correctness.

## Files and symbols

- `code-tiny/tools/cplus/clang_parser.py`
  - module/`parse_and_extract` contract
  - `_find_enclosing_func`, `func_extents`, pending `CALL_EXPR` handling
- `code-tiny/tools/cplus/cplus_analyzer.py`
  - `_symbol_id`, structural `Function` extraction and call resolution
- `code-tiny/tools/cplus/semantic_worker.py`
  - `_function_symbol_id`, `extract_semantic_callsite_evidence`
- `code-tiny/tools/cplus/semantic_shadow.py`
  - `run_shadow_comparison`
- `tests/fixtures/cplus_semantic_calls/`
- `tests/test_cplus_clang_worker.py`
- `tests/test_cplus_semantic_worker.py`
- new `tests/test_cplus_clang_parser.py`
- new `tests/test_cplus_clang_differential.py`

## Implementation steps

1. Remove function-global mutable cache state. Collect function extents/USRs
   first, freeze a parse-local sorted index, then resolve pending calls in a
   second pass. Apply the same two-pass endpoint map to protocol-2 extraction.
2. Make enclosing-function lookup deterministic for nested constructs and safe
   across repeated/concurrent parses; no object-id cache or list-length race.
3. Introduce structural identity schema `cplus-function-v2`: qualified name +
   normalized syntactic parameter types + cv/ref/noexcept qualifiers + template
   arity + linkage discriminator. Add repository-relative file only for
   internal linkage and a normalized-span suffix only for anonymous/unparseable
   declarations. Name+arity is forbidden. Store a versioned legacy alias for
   diagnostics, never as a unique join key.
4. Re-key Tree-sitter functions, structural endpoints, callsites, Pro*C host
   joins, caches, journals, and provider rows in one clean-generation migration.
   Preserve declaration/definition coalescing tests and explicitly quarantine
   ambiguous legacy rows; never mutate old IDs in place.
5. Preserve Clang USR as observation identity. Join it to exactly one accepted
   signature-v2 `Function` by normalized declaration coordinates/signature and
   project/config provenance. Zero or multiple matches remain dangling and can
   never create a strict edge.
6. Treat prototype and pure-virtual cursors as semantic references/inventory,
   not as permission to replace Tree-sitter declaration nodes. An absent local
   target becomes unresolved/dangling evidence unless it can join an accepted
   structural identity.
7. Retain conservative classifications: pure virtual is
   `declared_virtual_target`, a function-pointer invocation is
   `indirect_callsite`, and a dependent template call is not direct.
8. Produce a deterministic differential artifact with four stages per file:
   raw Tree-sitter payload, raw Clang cursor/evidence inventory, validated
   payload, and expected persisted identities. Compare a canonical projection:
   label, signature-v2 ID, normalized span, relation type/endpoints, and stable
   properties. Exclude ordering, elapsed time, timestamps, and run IDs.
9. Bind every differential identity and endpoint to project + generation; a
   fixture with identical paths/symbols in two projects must prove isolation.
10. Classify every delta as `expected_plane_difference`, `adapter_loss`,
   `identity_collision`, `validation_rejection`, or `unexpected_persistence`.
11. Assert the key invariant separately: within identity schema v2, enabling
   Clang cannot remove or mutate any Tree-sitter structural identity/relation.

## Regression matrix

| Cohort | Required result |
| --- | --- |
| Two or more functions | Calls in first and later functions all retain caller identity |
| Prototype/external declaration | Tree-sitter declaration retained; unresolved or joined semantic target is explicit |
| Pure virtual | Structural declaration retained; call classified virtual, never unconditional direct |
| Same-arity overload | Distinct callee USRs/observations; no identity collapse |
| Template | Dependent call remains weak until concrete resolution |
| Function pointer | Invocation remains indirect; enclosing call to `apply` is retained |
| Macro/static linkage | Expansion and file-local identity preserved |
| Missing/generated header | Partial/failed coverage with stable reason, no invented target |

## Tests

```bash
.venv/bin/python -m pytest -q \
  tests/test_cplus_clang_parser.py \
  tests/test_cplus_clang_differential.py \
  tests/test_cplus_clang_worker.py \
  tests/test_cplus_semantic_worker.py
```

The real libclang tests must use the pinned runtime and fail visibly when it is
unavailable; pure helper tests may run without it. No mock may prove cursor
coverage or overload identity.

## Acceptance criteria

- The later-function regression fails on the current mutable cache and passes
  with a parse-local two-pass index.
- Every reviewed fixture delta is classified; unexplained disappearance is a
  failure even if Clang reports zero diagnostics.
- Protocol 2 emits distinct overload USRs, and every strict endpoint joins one
  project/generation-scoped signature-v2 `Function` without ambiguity.
- The canonical Tree-sitter structural projection is identical with the
  diagnostic/semantic lane enabled.

## Todo

- [ ] Replace mutable extent caching with two-pass local indexing.
- [ ] Migrate structural Function IDs and semantic endpoint mapping to v2.
- [ ] Add the adversarial differential fixture matrix.
- [ ] Emit deterministic per-plane delta artifacts.
- [ ] Prove exact Tree-sitter structural invariance.
