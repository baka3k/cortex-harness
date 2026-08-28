# Phase 04 — Regression tests, end-to-end isolation proof, and rollout gates

## Context

Phases 02 and 03 each landed their own focused tests. Phase 04 stitches
them into one end-to-end isolation proof on temporary instance paths and
defines the rollout gates that flip the env-flag defaults.

## Goals

- One integration test that boots two MCP processes against temporary
  instance paths, asserts both reach `READY`, and proves:
  - `dev sync B` succeeds while MCP A is running.
  - MCP A continues serving a query after a `dev sync B` pause.
  - Both MCPs' `StorageLease` surfaces are disjoint (verified through a
    driver-level introspection hook).
- Rollout gates defined: the env-flag defaults flip only after the suite
  passes twice consecutively and a `dev doctor` report is captured.
- A revert runbook committed under `plans/.../reports/`.

## Related files

- `tests/test_mcp_lease_surface.py` (new).
- `tests/test_dev_pause_by_instance.py` (Phase 03).
- `plans/260828-1428-instance-isolated-mcp-locks/reports/rollout-gates.md`
  (new).
- `docs/development-rules.md` (gate values, runbook link).

## Implementation steps

1. Add `tests/test_mcp_lease_surface.py`:
   - Use `tempfile.TemporaryDirectory` for `CORTEX_DATA_HOME` and create
     two instances `alpha/` and `beta/` with empty `data.rdb`.
   - Spawn two MCP backend subprocesses with
     `CORTEX_STORAGE_INSTANCE=alpha` and `CORTEX_STORAGE_INSTANCE=beta`.
   - Wait for both to be `READY` (poll a `/health` endpoint or a
     configurable signal — design the contract in this phase).
   - Assert `_open_additional_local_clients` on each driver only sees its
     own primary path: introspect via the driver's
     `_additional_clients` and `_additional_storage_leases` lists; both
     should be empty.
   - Acquire `StorageLease` on B's `data.rdb` from a third "ingest"
     process; assert it does not raise `StorageLeaseConflictError` because
     MCP A no longer holds it.
   - Run `_mcp_stop_pattern("unified_mcp.py", instance_id="beta")` via a
     mock pid-file fixture; assert MCP A's PID is untouched and MCP B's
     pid-file sidecar is removed/updated.
2. Add `plans/260828-1428-instance-isolated-mcp-locks/reports/rollout-gates.md`
   listing:
   - Two consecutive green CI runs of the integration test.
   - `dev doctor` output captured with both gates off (legacy mode).
   - `dev doctor` output captured with both gates on (new mode).
   - A short runbook: how to flip the default, how to roll back.
3. Wire `dev doctor` (or `dev status`) to print the gate values so the
   report can be re-generated on demand.
4. Update `docs/development-rules.md` to point at the runbook.

## Risks

- End-to-end tests are slow and platform-flaky. Mitigation: keep this
  test in a separate `tests/integration/` marker; CI may keep it
  optional for fast PR feedback but mandatory for nightly + pre-release.
- Spawning two real MCP processes in a test relies on the same venv,
  Python path, and platform `ps` behavior. Mitigation: use the existing
  `dev.py` launchers as the subprocess under test; the test asserts
  behavior, not internals.
- Reading `READY` state from a real MCP requires a probe that today may
  not exist. Mitigation: if the MCP lacks a probe, add a minimal
  `/healthz` route behind a feature flag; fall back to a poll on the
  pid-file + log marker until the probe lands.

## Success criteria

- `tests/test_mcp_lease_surface.py` passes on macOS and Linux CI in the
  new `tests/integration/` lane.
- The rollout-gates report is committed with both `dev doctor` outputs.
- The runbook describes the flip + revert path with one command each.
