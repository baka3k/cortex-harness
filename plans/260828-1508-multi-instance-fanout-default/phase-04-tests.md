# Phase 04 — Tests, revert summary, and rollout

## Context

This phase reuses the dev-lifecycle tests from
`260828-1428-instance-isolated-mcp-locks` Phase 03 verbatim, replaces
the cross-instance gate tests with the new no-lease / fan-out
tests, and produces a revert summary that lists every file and
assertion that flips.

## Goals

- `tests/test_dev_pause_by_instance.py` is unchanged.
- `tests/test_mcp_lease_surface.py` is updated: the
  `CrossInstanceGateTests` class is removed; the
  `LeaseSurfaceTests` is rewritten to assert the fan-out default
  and the no-lease contract on siblings.
- `tests/test_mcp_sibling_no_lease.py` (new) pins the driver-level
  no-lease contract.
- `code-tiny/tests/mcp/cplus/test_cplus_mcp.py` is restored to
  its pre-`1428` shape for the discovery / additional_paths
  assertions; the new no-lease test is in the driver-level test
  file instead.
- A revert summary at
  `plans/260828-1508-multi-instance-fanout-default/reports/revert-summary.md`
  enumerates every file changed in plan `1428` and its
  corresponding change in this plan.

## Related files

- `tests/test_mcp_lease_surface.py`.
- `tests/test_dev_pause_by_instance.py` (read-only, carried over).
- `tests/test_mcp_sibling_no_lease.py` (new).
- `code-tiny/tests/mcp/cplus/test_cplus_mcp.py`.
- `tests/test_falkordb_driver_local.py` (carried over).
- `tests/test_dev_lifecycle_commands.py` (carried over).
- `tests/test_explore_graph_falkor_compat.py` (carried over).
- `docs/development-rules.md` (note: "MCP fan-out default; primary
  lease exclusive; siblings no lease; pause-by-instance").

## Implementation steps

1. Update `tests/test_mcp_lease_surface.py`:
   - Replace `test_alpha_driver_owns_only_alpha_lease` with
     `test_alpha_driver_includes_every_sibling`:
     - `_list_databases` (or directly the driver) returns
       graphs from both `alpha` and `beta`.
   - Replace `test_ingest_of_beta_succeeds_while_mcp_alpha_holds_alpha`:
     - Same body (this test still holds; the exclusive lease is
       on alpha, not beta, so ingest of beta never conflicted).
   - Remove `test_ingest_of_alpha_fails_when_mcp_alpha_holds_it`:
     - Replace with `test_ingest_of_alpha_still_conflicts`
       using a different `owner_id` (the primary lease
       semantically still blocks).
   - Remove `test_legacy_escape_hatch_returns_every_sibling`:
     the env flag is gone.
   - Remove `CrossInstanceGateTests` entirely.
2. Add `tests/test_mcp_sibling_no_lease.py`:
   - Use `mock.patch.object(StorageLease, "acquire")` to count
     calls during driver construction.
   - Construct a driver with one primary path and one sibling
     path; assert `acquire` is called exactly once (for the
     primary) and the sibling client is opened.
   - Construct a driver with no `additional_paths`; assert
     `acquire` is called exactly once.
   - Construct a driver with three siblings; assert `acquire` is
     still called exactly once.
3. Revert the cplus-mcp test changes from plan `1428`:
   - Restore `test_graph_driver_receives_all_discovered_instance_files`
     to its original expectation
     (`additional_paths == [primary_path, sibling_path]`).
   - Restore `test_discovery_honors_relocated_data_home` to
     its original `discovered == [first, second]` expectation.
   - Remove the new tests added in `1428`
     (`test_discovery_siblings_*`).
4. Update `docs/development-rules.md` (one short paragraph) to
   document:
   - MCP fan-out is the default for unscoped queries.
   - The primary `data.rdb` lease is exclusive and blocks other
     writers.
   - Sibling `data.rdb` files have no application lease; the
     reader's falkordblite process co-exists with the sibling's
     writer.
   - `dev sync <owner> --instance <I>` pauses only the MCP for
     instance `<I>`; other instances keep serving.
5. Write the revert summary at
   `plans/260828-1508-multi-instance-fanout-default/reports/revert-summary.md`:
   - For each file changed in `1428` (commit `1762772`), describe
     the corresponding change in this plan.
   - The summary is a single file (not a daily dump) and is
     referenced from this plan's `References` block.

## Risks

- The `test_mcp_lease_surface.py` rewrite must be reviewed for any
  test that relied on the cross-instance gate; the only such
  class is `CrossInstanceGateTests` which is removed entirely.
- The driver-level no-lease test depends on the exact
  `StorageLease.acquire` call shape; if a refactor moves that
  call into a helper, the test must be updated. Phase 01's
  review covers this.
- The revert summary must list every file from `1428` to avoid
  leaving a stale env-flag reference in a non-obvious place.
  Mitigation: the summary is generated from `git show 1762772
  --stat` plus a follow-up `git grep` for the removed symbols.

## Success criteria

- All tests in the affected suites pass:
  `test_mcp_lease_surface.py`, `test_mcp_sibling_no_lease.py`,
  `test_dev_pause_by_instance.py`, `test_falkordb_driver_local.py`,
  `test_cplus_mcp.py`, `test_explore_graph_falkor_compat.py`,
  `test_dev_lifecycle_commands.py`.
- The revert summary at
  `plans/260828-1508-multi-instance-fanout-default/reports/revert-summary.md`
  lists every file and every env flag that flips.
- The dev lifecycle scoping (pause-by-instance) is preserved and
  `test_dev_pause_by_instance.py` passes unchanged.
- No env-flag references to `CORTEX_MCP_SCOPE_LEASES` or
  `CROSS_INSTANCE_QUERY` remain in the source tree.
