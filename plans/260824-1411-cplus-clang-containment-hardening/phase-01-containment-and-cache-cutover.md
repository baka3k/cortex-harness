# Phase 01: Containment and cache cutover

## Goal

Make Tree-sitter structural ownership an executable invariant before any further
Clang work. Remove both known whole-payload replacement paths and prevent an old
LIBCLANG cache entry from bypassing the new rule.

## Current evidence

- `cplus_analyzer._load_or_parse_payload` enables an in-process Clang fallback
  only when `parse_quality_policy == "off"`, then replaces the Tree-sitter
  payload when Clang diagnostic count is lower than Tree-sitter `ERROR` count.
- `_load_or_parse_payload` can read a recovered LIBCLANG payload cache before
  producing the Tree-sitter baseline.
- `parse_recovery.recover_payload_candidates` still returns selected Clang
  payloads for `repair`, including when the compile context is `free-mode`.
- `candidate_is_strictly_better` correctly avoids comparing cross-provider
  diagnostics, but it still authorizes cross-provider structural replacement.

## Files and symbols

- `code-tiny/tools/cplus/cplus_analyzer.py`
  - `_CLANG_FALLBACK_BASE_THRESHOLD`, `_effective_fallback_threshold`
  - `_load_or_parse_payload`
  - `build_call_graph/raw_payload_for`
  - `_PARSE_CACHE_VERSION`, `parse_args`
- `code-tiny/tools/cplus/parse_recovery.py`
  - `_candidate_quality`, `recover_payload_candidates`, protocol-1 result path
- `code-tiny/tools/common/parse_quality.py`
  - `RECOVERY_POLICY_VERSION`, `candidate_is_strictly_better`
- `code-tiny/tools/cplus/clang_worker.py`
  - legacy protocol-1 request branch; preserve semantic protocol 2
- `tests/test_cplus_parse_recovery.py`
- `tests/test_parse_quality_contract.py`
- `tests/test_incremental_sync_parse_quality.py`
- `tests/test_cplus_graph_runtime.py`
- `code-tiny/tools/Readme.md`

## Implementation steps

1. Freeze the provider/plane rule in the common quality contract: structural
   candidate selection is same-backend only. A LIBCLANG candidate may produce a
   diagnostic/shadow result, never a selected structural payload.
2. Remove the optional `_clang_parser` import, threshold constants, diagnostic
   comparison branch, and `allow_inprocess_clang_fallback` parameter from the
   normal analyzer path.
3. Change `raw_payload_for` so all parse-quality modes source structural
   collections from Tree-sitter. `off` suppresses quality reporting/recovery;
   it must not activate a hidden parser fallback.
4. Stop `repair` from placing any LIBCLANG payload into
   `repair_selected_payloads`. Keep same-backend alternate C/C++ grammar and
   Pro*C-owned masking/recovery behavior intact.
5. Retire protocol 1 from analyzer/recovery/publication. A separately invoked
   fixture diagnostic may keep its decoder, but its typed result is always
   `diagnostic_only` and every cache, validator, selector, writer, and provider
   boundary must reject it. Protocol 2 remains semantic-only.
6. Remove recovered-LIBCLANG cache preference and writes. Bump parse-cache and
   recovery-policy versions so old whole-payload entries cannot be reused.
7. Preserve terminal recovery accounting with an explicit
   `cross_backend_structure_forbidden` outcome rather than silently reporting
   an improvement or failure.
8. Inventory and version every affected surface: parse/recovery cache,
   protocol, evidence/merge, semantic cache, coverage, graph rows, HTTP query
   cache, and provider generation. A legacy provenance/version at any surface
   is a cache miss or quarantine, never silently upgraded.
9. End Phase 1 at the parser/cache boundary: write a durable incompatibility
   marker for provider generations containing unknown or legacy Clang structural
   provenance and refuse to serve/reuse them as current. Do not rebuild, delete,
   or pointer-flip provider storage here. Phase 4, under its runtime-owner
   blockers, must consume the marker and build/validate/activate a clean inactive
   Tree-sitter generation.
10. Update CLI help and operator docs with the new `off`, `report`, and
    `repair` meanings and the cache/generation migration behavior.

## Cutover matrix

| Surface | Old state handling | New invariant |
| --- | --- | --- |
| Parse/recovery cache | Miss/quarantine LIBCLANG winners | Tree-sitter structure only |
| Protocol 1 | Decoder fixture only | Cannot enter selection or publication |
| Graph/vector rows | Mark incompatible and block current serving | Phase 4 rebuilds a clean inactive generation |
| Coverage/query cache | Invalidate old keys | Project/generation/policy scoped |
| Journal/generation | Never replay incompatible entries | Atomic validated cutover |

## Tests

- Inject a zero-diagnostic Clang candidate with fewer functions/types/relations
  and prove Tree-sitter remains selected under all three policies.
- Seed a legacy LIBCLANG cache entry and prove the upgraded analyzer ignores it,
  reparses Tree-sitter, and never rewrites it as a structural winner.
- Prove same-backend Tree-sitter grammar retry can still win.
- Prove `off` does not import/call Clang and does not force compile-database
  bootstrap; `report` and `repair` retain their documented bounded behavior.
- Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_parse_quality_contract.py \
  tests/test_cplus_parse_recovery.py \
  tests/test_incremental_sync_parse_quality.py \
  tests/test_cplus_graph_runtime.py
```

## Acceptance criteria

- Repository search finds no normal-path diagnostic-count replacement and no
  LIBCLANG structural-cache preference.
- All three policies return Tree-sitter provenance for structural collections.
- Cross-backend candidate selection fails closed even if a future caller tries
  to reintroduce it.
- Cache/policy version changes are covered by a migration regression test.
- Phase 1 performs no provider activation. Legacy/unprovable generations are
  durably marked incompatible and fail closed until Phase 4 performs the clean
  generation rebuild/cutover.
- Phase 1 can ship alone as the immediate safe containment state.

## Rollback

Rollback restores the prior parser/cache policy only if it is Tree-sitter-only.
It never clears a provider incompatibility marker or restores either Clang
structural replacement path; temporarily disable semantic work instead.

## Todo

- [ ] Freeze the same-backend structural-selection invariant.
- [ ] Remove legacy fallback and parse-quality `off` coupling.
- [ ] Retire whole-payload `repair` selection.
- [ ] Invalidate legacy LIBCLANG structure caches.
- [ ] Update focused tests and CLI documentation.
