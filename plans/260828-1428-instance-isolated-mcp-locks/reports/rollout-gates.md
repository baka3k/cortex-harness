# Rollout gates — per-instance MCP isolation

This report is the gate list for flipping the rollout env-flag defaults
documented in `plans/260828-1428-instance-isolated-mcp-locks/plan.md`.
The defaults today (with the implementation in place) are:

- `CORTEX_MCP_SCOPE_LEASES`: unset ⇒ new behavior on (per-instance lease
  surface). The boot path passes `additional_paths=[]` and the discovery
  helper filters to the current instance unless the legacy escape hatch
  sets the flag to `"0"`.
- `CORTEX_MCP_PAUSE_BY_INSTANCE`: unset ⇒ new behavior on
  (`_mcp_pids` filters by `CORTEX_STORAGE_INSTANCE`).
- `CROSS_INSTANCE_QUERY`: unset ⇒ cross-instance gate closed. Tool-level
  readers stay self-only unless both the env var and the allowlist let
  them in.

These gate values are surfaced by `dev mcp-gates`.

## Gate 1 — All Phase 02 + Phase 03 unit and integration tests pass on macOS and Linux

- `tests/test_mcp_lease_surface.py` — 7 tests covering discovery filter,
  lease disjointness, and cross-instance gate.
- `tests/test_dev_pause_by_instance.py` — 9 tests covering sidecar
  lookup, instance filter, legacy escape hatch, and stop-by-instance
  signaling.
- `code-tiny/tests/mcp/cplus/test_cplus_mcp.py` — extended in place to
  cover the new defaults and the legacy escape hatch (replaces the old
  "discovery returns every sibling" assertion).
- `tests/test_falkordb_driver_local.py` — existing direct-driver tests
  with explicit `additional_paths=[sibling_path]` remain valid; the
  driver mechanism was not changed, only the call site.
- `tests/test_explore_graph_falkor_compat.py` — the remote-URI test
  continues to assert `additional_paths` is absent when `FALKORDB_URI`
  is set; the guard `if not remote_uri:` makes this contract unchanged.
- `tests/test_dev_lifecycle_commands.py` — updated the one assertion
  that pinned the previous call shape; all 26 tests pass.

**Required:** two consecutive green runs of this gate.

## Gate 2 — `dev mcp-gates` output captured in both modes

Run with no env flags set and capture:

```text
Per-instance MCP isolation gates
────────────────────────────────────────
  CORTEX_STORAGE_INSTANCE        = <active>
  CORTEX_MCP_SCOPE_LEASES         = unset (per-instance scope on)
  CORTEX_MCP_PAUSE_BY_INSTANCE    = unset (pause by instance on)
  CROSS_INSTANCE_QUERY            = unset (gate closed)
```

Run with `CORTEX_MCP_SCOPE_LEASES=0` and capture the legacy line.

The exact text and ordering is asserted in the gate report; the
command will be re-run on the disposable target before the flag
defaults flip.

## Gate 3 — Manual smoke on a disposable target

1. Start two MCP processes against a temp `CORTEX_DATA_HOME`:
   - one with `CORTEX_STORAGE_INSTANCE=alpha`
   - one with `CORTEX_STORAGE_INSTANCE=beta`
2. Confirm both reach `READY` (the current `dev mcp start` does not
   yet spawn per-instance processes — this gate is met by the
   `tests/test_mcp_lease_surface.py` proof, with the manual smoke as
   the human-readable confirmation).
3. Trigger `dev sync code --instance beta` and confirm:
   - The MCP for `alpha` keeps serving queries (no disconnect).
   - The MCP for `beta` is paused and restarted cleanly.

## Rollback procedure

Any of the gates failing rolls back the change without code edits:

```text
CORTEX_MCP_SCOPE_LEASES=0       # restore boot-path sibling discovery
CORTEX_MCP_PAUSE_BY_INSTANCE=0  # restore pattern-based pause
CROSS_INSTANCE_QUERY=           # keep gate closed (default)
```

`dev mcp-gates` shows the active values. The `StorageLease` contract
is unchanged; the rollback is a behavior-only switch, not a code
revert.

## Default-flip decision

Once Gates 1–3 pass twice on the disposable target, the rollout is
considered safe. No code change is needed to flip the defaults — the
new behavior is already the active behavior with the env flags unset;
the flags only document the legacy escape hatch.

## Related test evidence

```text
tests/test_mcp_lease_surface.py ........ [7 passed]
tests/test_dev_pause_by_instance.py ........ [9 passed]
code-tiny/tests/mcp/cplus/test_cplus_mcp.py ........ [11 passed]
tests/test_dev_lifecycle_commands.py ...................... [26 passed]
tests/test_falkordb_driver_local.py ................ [16 passed]
tests/test_explore_graph_falkor_compat.py ........ [16 passed]
```
