# Revert summary — `260828-1508-multi-instance-fanout-default`

This document lists every file changed in plan
`260828-1428-instance-isolated-mcp-locks` (commit `1762772`) and the
corresponding change in this plan. The current plan reverts most of the
runtime changes and preserves the dev-lifecycle scoping (Phase 03 of
`1428`). The driver-level change is *not* a straight revert: the sibling
stores are opened for read-only fan-out, but the exclusive
`StorageLease` is dropped from siblings rather than dropped from the
discovery list.

## Files and symbols

### `code-tiny/tools/graph/driver/falkordb_driver.py`

| Aspect | `1428` state | This plan |
| --- | --- | --- |
| `_open_additional_local_clients` | Acquires a `StorageLease` on every sibling; logs and skips on `StorageLeaseConflictError`. | Opens every sibling via `_open_local_falkordb(candidate)` *without* acquiring any lease. The primary lease code at lines 296-301 is unchanged. |
| `_additional_storage_leases` | Tracked as `List[StorageLease]`; released in `close()`. | Removed; replaced by `_additional_open_paths: List[Path]` for diagnostics. |
| `StorageLeaseConflictError` import | Imported and used as a sibling skip branch. | Removed from the driver import (still imported in `cortex_harness/storage` and re-exported; only the driver's local use is gone). |

### `code-tiny/mcp/cplus/cplus_mcp.py` (line 237)

`1428`: `config["additional_paths"] = []`.

This plan: `config["additional_paths"] = discover_falkordb_data_files()`.

### `code-tiny/mcp/java/java_mcp.py` (line 202)

Same revert as cplus_mcp.py.

### `code-tiny/mcp/android/android_mcp.py` (line 216)

Same revert as cplus_mcp.py.

### `code-tiny/mcp/fastmcp_server.py` (line 221)

Same revert as cplus_mcp.py.

### `code-tiny/mcp/services/impact_service.py` (line 55)

`1428`: lazy `from cross_instance import self_and_allowed_siblings_paths` + `self_and_allowed_siblings_paths("analyze_workflow_impact")`.

This plan: lazy `from falkordb_discovery import discover_falkordb_data_files` + `discover_falkordb_data_files()`.

### `code-tiny/mcp/services/explore_service.py` (line 266)

Same revert as impact_service.py.

### `code-tiny/mcp/cross_instance.py`

`1428`: new module with `enabled()`, `is_allowed()`, `sibling_paths_if_allowed()`, `self_and_allowed_siblings_paths()`, `CROSS_INSTANCE_OPT_IN_ENV`, and `ALLOWLIST`.

This plan: **deleted**.

### `code-tiny/mcp/falkordb_discovery.py`

| Aspect | `1428` state | This plan |
| --- | --- | --- |
| Default kwargs | `include_siblings=False, exclude_self=True` (self only). | `include_siblings=True, exclude_self=False` (every sibling + self). |
| `LEGACY_INCLUDE_SIBLINGS_ENV` | `"CORTEX_MCP_SCOPE_LEASES"`, plus `_legacy_include_siblings()` helper that flipped the defaults when env == "0". | Removed. |
| Module docstring | Cited the per-instance MCP lease isolation plan and `cross_instance.CROSS_INSTANCE_OPT_IN`. | Rewritten to describe the multi-instance fan-out default and the no-lease sibling semantics. |

### `cortex_harness/dev.py` (`dev mcp-gates` command, lines 2574-2602)

`1428`: surfaces `CORTEX_MCP_SCOPE_LEASES`, `CORTEX_MCP_PAUSE_BY_INSTANCE`, and `CROSS_INSTANCE_QUERY`.

This plan: surfaces only `CORTEX_MCP_PAUSE_BY_INSTANCE` (the surviving lifecycle gate). The `CORTEX_STORAGE_INSTANCE` row is preserved.

### `cortex_harness/dev.py` (pause-by-instance helpers, lines 1760-2119)

`1428`: introduced `_resolve_storage_instance`, `_mcp_pid_sidecar_path`,
`_mcp_pid_sidecar_recorded_pid`, `_pid_instance_id`,
`_read_instance_from_process_env`, `_mcp_pids(instance_id=…)`,
`_mcp_stop_pattern(instance_id=…)`, `_legacy_pause_by_instance_disabled`,
and the per-instance pid sidecar written by `_mcp_start_one`.

This plan: **carried over verbatim**. The lifecycle invariant from
`1428` Phase 03 is preserved.

## Env flags removed

- `CORTEX_MCP_SCOPE_LEASES` — no longer read in `falkordb_discovery.py` or `dev.py`.
- `CROSS_INSTANCE_QUERY` — no longer read in `cross_instance.py` (deleted) or `dev.py`.

## Env flags preserved

- `CORTEX_MCP_PAUSE_BY_INSTANCE` — read only in `cortex_harness/dev.py`. The legacy "pause by pattern" mode is still reachable.
- `CORTEX_STORAGE_INSTANCE` — unchanged.

## Tests

### `tests/test_mcp_lease_surface.py`

`1428`: `LeaseSurfaceTests` asserted the self-only default and a
`CORTEX_MCP_SCOPE_LEASES=0` legacy escape hatch; `CrossInstanceGateTests`
covered `cross_instance.enabled()`, `is_allowed()`, `sibling_paths_if_allowed()`,
and `self_and_allowed_siblings_paths()`.

This plan:
- `LeaseSurfaceTests` rewritten to assert the fan-out default
  (`test_default_discovery_returns_every_sibling`,
  `test_alpha_driver_includes_every_sibling`,
  `test_explicit_kwargs_filter_siblings`), the unchanged same-instance
  conflict (`test_ingest_of_alpha_still_conflicts`), and the unchanged
  cross-instance non-conflict (`test_ingest_of_beta_succeeds_while_mcp_alpha_holds_alpha`).
- The legacy escape hatch test is removed.
- `CrossInstanceGateTests` is removed entirely.

### `tests/test_mcp_sibling_no_lease.py` (new)

Pins the no-lease contract on siblings. Uses a recording stub for
`StorageLease.acquire` to assert exactly one acquisition per driver
construction, regardless of how many siblings are passed.

### `code-tiny/tests/mcp/cplus/test_cplus_mcp.py`

`1428`: `test_graph_driver_receives_no_sibling_paths_by_default` asserted
`additional_paths == []`. New tests `test_discovery_siblings_exclude_self_by_default`
and `test_discovery_siblings_include_self_when_exclude_self_false` covered
the explicit kwargs.

This plan:
- Restored `test_graph_driver_receives_all_discovered_instance_files`
  asserting `additional_paths == [primary_path, sibling_path]` against a
  temporary `CORTEX_DATA_HOME` with two instances.
- Restored `test_discovery_honors_relocated_data_home` to assert
  `discovered == [first, second]` (legacy default).
- Removed `test_discovery_siblings_*` from `1428`.
- Added `test_discovery_explicit_kwargs_filter_siblings` for the
  explicit-kwarg branch.

### `tests/test_dev_pause_by_instance.py`

`1428`: introduced 9 tests covering per-instance pause/stop helpers
and the sidecar pid round-trip.

This plan: **carried over verbatim**. The dev-lifecycle scoping is
unchanged.

### `tests/test_falkordb_driver_local.py`, `tests/test_dev_lifecycle_commands.py`, `tests/test_explore_graph_falkor_compat.py`

`1428`: not modified by this plan (`test_dev_lifecycle_commands.py` was
updated in `1428` to assert pause-by-instance; the others were
unaffected).

This plan: **carried over verbatim**.

## Documentation

### `docs/development-rules.md`

The plan referenced a short note. The file does not exist in this
repository (verified by `ls docs/` and `grep -r "development-rules"
docs/`); the note is therefore omitted. The plan itself records the
invariant in the `Target architecture` and `Pause-by-instance` sections
so operators have a single source of truth.

## Verification

Run the following test suites:

```text
pytest -q tests/test_mcp_lease_surface.py \
          tests/test_mcp_sibling_no_lease.py \
          tests/test_dev_pause_by_instance.py \
          tests/test_falkordb_driver_local.py \
          tests/test_explore_graph_falkor_compat.py \
          tests/test_dev_lifecycle_commands.py \
          code-tiny/tests/mcp/cplus/test_cplus_mcp.py
```

Confirm zero references remain:

```text
git grep -n "CORTEX_MCP_SCOPE_LEASES\|CROSS_INSTANCE_QUERY"
git grep -n "cross_instance"
git grep -n "_additional_storage_leases"
```

All four must produce no source matches.
