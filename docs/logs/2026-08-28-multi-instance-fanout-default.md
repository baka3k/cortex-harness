# Multi-instance unscoped fan-out as the default MCP query behavior — 2026-08-28

## Context

Plan `260828-1428-instance-isolated-mcp-locks` shipped earlier the same day
introduced a per-instance isolation default: each MCP boot path
leased exactly one `CORTEX_STORAGE_INSTANCE`'s `data.rdb`, and
cross-instance reads were gated behind `CROSS_INSTANCE_QUERY=1` plus an
allowlist (`analyze_workflow_impact`, `explore_graph`). The dev
lifecycle scoping (pause-by-instance) from `1428` Phase 03 was
preserved. Users running multiple instances, however, expected fan-out
to remain the default unscoped query behavior — the gate was the wrong
default. Plan `260828-1508-multi-instance-fanout-default` was created to
revert the gate while keeping the lifecycle invariant. Commit `f475f36`
implements that plan.

## Change

### Driver — siblings have no lease
- `code-tiny/tools/graph/driver/falkordb_driver.py:392-422` —
  `_open_additional_local_clients` opens every sibling `data.rdb` via
  `_open_local_falkordb(candidate)` **without** acquiring any
  `StorageLease`. The `StorageLeaseConflictError` skip branch is
  removed; the `lease.acquire()` / `lease.release()` calls are gone.
  Concurrent ingests on sibling instances are no longer blocked by the
  reader; falkordblite's append-only AOF + atomic-rename RDB rewrite
  guarantees readers see the previous or new complete snapshot, never a
  torn write.
- `falkordb_driver.py:35` — `StorageLeaseConflictError` no longer
  imported by the driver.
- `falkordb_driver.py:247-249` — `_additional_storage_leases` replaced
  by `_additional_open_paths: List[Path]` for diagnostics.
- `falkordb_driver.py:296-301` (primary lease) — unchanged.

### Boot paths and services — default fan-out restored
- `code-tiny/mcp/cplus/cplus_mcp.py:237` — `config["additional_paths"] =
  discover_falkordb_data_files()`.
- `code-tiny/mcp/java/java_mcp.py:202` — same revert.
- `code-tiny/mcp/android/android_mcp.py:216` — same revert.
- `code-tiny/mcp/fastmcp_server.py:221` — same revert.
- `code-tiny/mcp/services/impact_service.py:47,55` — drops
  `from cross_instance import self_and_allowed_siblings_paths`; calls
  `discover_falkordb_data_files()` directly.
- `code-tiny/mcp/services/explore_service.py:266` — same revert.

### Cross-instance gate — deleted
- `code-tiny/mcp/cross_instance.py` — **deleted** (`ALLOWLIST`,
  `enabled()`, `is_allowed()`, `sibling_paths_if_allowed()`,
  `self_and_allowed_siblings_paths()`, `CROSS_INSTANCE_OPT_IN_ENV`).
- `CORTEX_MCP_SCOPE_LEASES` — no longer read anywhere.
- `CROSS_INSTANCE_QUERY` — no longer read anywhere.

### Discovery — legacy defaults restored
- `code-tiny/mcp/falkordb_discovery.py:36-50` — `discover_falkordb_data_files`
  defaults are now `include_siblings=True, exclude_self=False` (pre-1428
  behavior).
- `falkordb_discovery.py` — `LEGACY_INCLUDE_SIBLINGS_ENV` constant and
  `_legacy_include_siblings()` helper removed.
- Module docstring rewritten to describe the multi-instance fan-out
  default and the no-lease sibling semantics.

### Dev lifecycle — pause-by-instance kept
- `cortex_harness/dev.py:2574-2596` (`dev mcp-gates`) — surfaces only
  `CORTEX_MCP_PAUSE_BY_INSTANCE`. The deleted flags
  (`CORTEX_MCP_SCOPE_LEASES`, `CROSS_INSTANCE_QUERY`) are gone.
- `cortex_harness/dev.py:1760-2119` — pause-by-instance helpers,
  sidecar pid files, `_legacy_pause_by_instance_disabled`, and
  `_mcp_start_one` per-instance sidecar recording all carried over
  verbatim from `1428` Phase 03.

### Tests
- `tests/test_mcp_lease_surface.py` — `CrossInstanceGateTests` removed;
  `LeaseSurfaceTests` rewritten for the fan-out default and explicit
  kwargs (`test_default_discovery_returns_every_sibling`,
  `test_alpha_driver_includes_every_sibling`,
  `test_explicit_kwargs_filter_siblings`,
  `test_ingest_of_alpha_still_conflicts`,
  `test_ingest_of_beta_succeeds_while_mcp_alpha_holds_alpha`).
  Legacy escape hatch test removed.
- `tests/test_mcp_sibling_no_lease.py` (new) — three tests pinning the
  no-lease contract: single sibling, multiple siblings, and no
  siblings. Records `StorageLease.acquire` calls and asserts exactly
  one per driver construction.
- `code-tiny/tests/mcp/cplus/test_cplus_mcp.py` — restored to the
  pre-1428 discovery and additional_paths expectations; the
  `1428`-added `test_discovery_siblings_*` tests are replaced by a
  single `test_discovery_explicit_kwargs_filter_siblings`.
- `tests/test_dev_pause_by_instance.py`, `tests/test_falkordb_driver_local.py`,
  `tests/test_explore_graph_falkor_compat.py`,
  `tests/test_dev_lifecycle_commands.py`,
  `tests/test_storage_layout.py` — unchanged.

### Docs and reports
- `plans/260828-1508-multi-instance-fanout-default/reports/revert-summary.md`
  (new) — file-by-file revert trail from `1428` to this plan, with
  the verification commands listed.
- `docs/development-rules.md` referenced by the plan does not exist in
  this repository; the invariant is recorded in the plan itself rather
  than a separate note.

## Impact

Operators running multiple MCP instances get back the unscoped
multi-instance fan-out behavior they expected: `_list_databases`
followed by fan-out via `_run_cypher_first` returns graphs from every
instance with no opt-in. Ingest of instance B is no longer blocked by
MCP A holding A's lease — they are independent leases on independent
files. The `dev sync B` lifecycle still pauses only MCP B (pause-by-
instance invariant preserved from `1428`). Risk: **low** — the change
is a code revert with one architectural improvement (drop the lease on
siblings, keep the read fan-out), all 68 tests in the affected suites
pass, and the rollback path is the commit revert (no env-flag-only
fallback exists by design). Single-instance deployments see no
behavior change; only the discovery list grows from one entry to two
when the data home contains two instances.

## Decision

The lease surface (write serialization) and the read surface (fan-out)
were conflated in `1428`: the plan narrowed both to be safe. Separating
them is the correct fix. Siblings don't need an application lease
because falkordblite already serializes its own writes via
append-only AOF + atomic rename — the lease was solving a non-problem
for the read path and creating a real problem (cross-instance ingest
conflict) for the write path. The primary lease on the boot instance's
file remains exclusive, which preserves the `1428`-era
single-instance conflict semantics. The dev-lifecycle scoping (pause-
by-instance, sidecar pid file, `CORTEX_MCP_PAUSE_BY_INSTANCE=0`
escape hatch) is preserved because it solves a different problem
(distinguishing which MCP to stop during sync) that the lease change
does not address.

## References

- plan: ./plans/260828-1508-multi-instance-fanout-default/plan.md
- supersedes: ./plans/260828-1428-instance-isolated-mcp-locks/plan.md
- commit: f475f36
- revert summary: ./plans/260828-1508-multi-instance-fanout-default/reports/revert-summary.md
- related log: ./docs/logs/2026-08-28-per-instance-mcp-isolation.md
- related: code-tiny/tools/graph/driver/falkordb_driver.py:392-422
- related: code-tiny/mcp/falkordb_discovery.py:36-50
- related: code-tiny/mcp/cplus/cplus_mcp.py:237
- related: cortex_harness/dev.py:2574-2596
- related: tests/test_mcp_sibling_no_lease.py
- related: tests/test_mcp_lease_surface.py
