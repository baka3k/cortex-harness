# Phase 06 — Cross-tree acceptance tests + LINK_CONTRACT.md

## Goal

Prove the isolation promises end-to-end on a real fixture project, and
write the contract doc that future plans will cite.

## Tasks

1. `tests/test_link_acceptance.py` — cross-tree e2e on a small fixture
   project (tiny code repo + 2–3 docs, one mentioning real identifiers):
   - `dev sync code` → `dev sync doc` → `dev sync link` → boot 3 servers →
     `find_code_for_doc` returns the expected implementation symbol;
     `find_doc_for_code` reverses it.
   - Drift scenario: rename the symbol → re-run `dev sync code` +
     `dev sync link` → old link `stale`, new link `active`.
   - Isolation scenario: with link-tiny disabled/absent, `dev sync code`,
     `dev sync doc`, and both existing servers behave as before (the
     `test_old_sync_untouched` promise at acceptance level).
2. `docs/LINK_CONTRACT.md` — data contract (record schema, identity rules,
   provenance values, stale semantics), isolation contract (dependency
   direction, read/write split, manifest untouched), tool contracts, and
   the Phase-3 gate (coverage thresholds that justify fuzzy matching).
3. Add one row to `docs/MCP_CAPABILITY_ACCEPTANCE_MATRIX.md` for the three
   new tools (mirror existing row format).
4. Coverage telemetry review checkpoint: run `dev sync link` on this repo
   itself (`cortext` project) and record coverage numbers in the plan's
   `reports/` — the Phase-3 go/no-go input.

## Files

- New: `tests/test_link_acceptance.py`, `docs/LINK_CONTRACT.md`
- Existing files touched: `docs/MCP_CAPABILITY_ACCEPTANCE_MATRIX.md`
  (additive row).

## Boundaries (do NOT)

- No production code changes in this phase — acceptance failures route
  back to their phase, not patched here.
- Do not enable link_mcp in the default `dev start all`.

## Test scope

Mapped acceptance criteria + size: **M** (~4–6 large-grained test functions,
each covering a scenario; counts within feature ceiling together with
phase totals ≈ 43 — justification: acceptance scenarios are the deliverable
of this phase and reuse fixture setup).

| Behavior (map) | Test |
| --- | --- |
| Full pipeline e2e produces correct links through the MCP surface (typical) | `test_acceptance_pipeline_e2e` |
| Drift → stale + re-link round trip (data-integrity at acceptance level) | `test_acceptance_drift` |
| Isolation: absence of link-tiny leaves old flows untouched (plan core promise) | `test_acceptance_isolation` |
| Acceptance-matrix row verifiable against live servers (docs↔reality) | covered by e2e assertions |
