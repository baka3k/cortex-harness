---
title: "Multi-instance unscoped fan-out as the default MCP query behavior"
status: pending
created: 2026-08-28
mode: hi-plan --full
scope: "falkordb sibling-store lease semantics, MCP boot-path default, dev.py pause-by-instance (carried over), targeted regression tests"
supersedes: 260828-1428-instance-isolated-mcp-locks
blockedBy: []
blocks: []
relatedPlans:
  - 260828-1428-instance-isolated-mcp-locks
  - 260807-0929-mcp-ingest-query-concurrency
  - 260806-1648-local-file-storage
  - 260728-0000-unified-ingest-query-contract
  - 260807-1202-graph-ingest-write-path-hardening
  - make-mcp-lifecycle
---

# Multi-instance unscoped fan-out as the default MCP query behavior

## Overview

Plan `260828-1428-instance-isolated-mcp-locks` shipped a strict per-instance
isolation default: MCP A's boot path passed `additional_paths=[]` so the
driver opened only A's `data.rdb`, and a `cross_instance` allowlist gated
the few tools that needed cross-instance reads. The user revised the
requirement on 2026-08-28: **unscoped multi-instance query fan-out is the
default**, while the lifecycle guarantee (a `dev sync` on instance B must
not stop MCP A) still holds.

The two goals are reconciled by changing the *lease surface* of the
FalkorDB driver, not the read surface. The primary store keeps its
exclusive `StorageLease` (it is the file MCP A owns and writes to), and
**sibling stores are opened read-only with no application lease** so they
do not block concurrent ingests on those instances.

This plan supersedes `260828-1428-instance-isolated-mcp-locks` and
reuses its dev-lifecycle scoping (Phase 03 of that plan) verbatim. The
per-instance isolation contract (`additional_paths=[]` boot default, the
`cross_instance` allowlist) is reverted.

## Scope challenge decisions

### 1. What does "default fan-out" mean here?

**Decision:** the boot path passes every discovered sibling
`data.rdb` to the driver, exactly as the pre-`1428` code did. Unscoped
queries (`_list_databases` then fan-out via `_run_cypher_first`) see
graphs from the current instance **plus every sibling** with no opt-in.
Scoped queries (`project_id` set) still use only the caller's `dbs`
list intersected with `available`, so per-project isolation is
preserved by the caller's intent, not by the discovery layer.

### 2. How do we keep ingest of B working while MCP A is reading B?

**Decision:** the driver does **not** acquire `StorageLease` for sibling
paths. The primary store still takes the exclusive lease as it does
today. Sibling stores are opened through the existing
`_open_local_falkordb` helper which spawns a short-lived
`falkordblite` redis-server process per file. The writer (the sync
process for B) takes its own exclusive lease on B's primary file;
MCP A's read-only falkordblite for B's file does not compete with that
lease.

**Risk acknowledged:** if the writer is mid-write (RDB rewrite) at the
exact moment the read-only falkordblite starts, the read process may
see a half-written RDB. falkordblite's rewrite is append-only then
atomic rename; the read either sees the previous complete snapshot or
the new one. We accept this for the cross-instance read path; the
primary lease still serializes writes against the primary reader
(MCP A's own writes), and a writer never reads its own file through
this code path.

### 3. What about the dev lifecycle pause-by-instance?

**Decision:** keep `dev.py`'s `_mcp_pids(instance_id=)` and
`_mcp_stop_pattern(instance_id=)` from `260828-1428` Phase 03. A
`dev sync code --instance beta` must pause only the MCP for `beta`,
not the MCP for `alpha` whose boot process is happily reading
`beta`'s graphs. The `_resolve_storage_instance` helper, the per-
instance sidecar pid file, and `_legacy_pause_by_instance_disabled`
all stay.

### 4. What happens to `code-tiny/mcp/cross_instance` and the
`CORTEX_MCP_SCOPE_LEASES=0` escape hatch?

**Decision:** delete `cross_instance.py` and remove the
`CORTEX_MCP_SCOPE_LEASES=0` env-var legacy hook. They were gating
behavior we are now re-enabling by default. The
`CORTEX_MCP_PAUSE_BY_INSTANCE=0` hook stays — pause-by-instance is a
lifecycle invariant we want to keep documented, even if a future
change relaxes it.

### 5. What is the rollback path?

**Decision:** the entire change is a code revert in two places (driver
+ boot paths), so the rollback is the commit revert. No new env flag
is required because there is no new "off" state to fall back to. The
dev lifecycle scoping (pause-by-instance) is preserved through the
revert because it lives in `dev.py`, not in the driver.

## Verified baseline

- `code-tiny/mcp/falkordb_discovery.py:22` returns every
  `data.rdb` under `instances/*/` (filtered by the new
  `include_siblings`/`exclude_self` kwargs that were added in plan
  `1428`).
- `code-tiny/mcp/cplus/cplus_mcp.py:237`,
  `code-tiny/mcp/java/java_mcp.py:202`,
  `code-tiny/mcp/android/android_mcp.py:216`,
  `code-tiny/mcp/fastmcp_server.py:221` all pass
  `additional_paths=[]` after plan `1428`.
- `code-tiny/tools/graph/driver/falkordb_driver.py:418-425`
  acquires an exclusive `StorageLease` on every sibling in
  `additional_paths` — the root cause of the cross-instance ingest
  conflict.
- `code-tiny/mcp/cross_instance.py` exists from plan `1428` but is no
  longer needed: services route through
  `self_and_allowed_siblings_paths(tool_name)` only when the gate is
  open, which by default it is not.
- `cortex_harness/dev.py:1760-2119` already implements
  pause-by-instance per plan `1428` Phase 03; this plan reuses those
  helpers unchanged.
- `cortex_harness/storage/lease.py:44-69` uses
  `LOCK_EX | LOCK_NB` for the primary. We do **not** change this —
  the primary lease is the write-serialization guarantee.

## Target architecture

```text
                          CORTEX_STORAGE_INSTANCE=A
MCP A ──────────────────────────────────────────────────
  driver = FalkorDBDriver(
      path     = <instances/A/.../data.rdb>,       # exclusive lease
      additional_paths = [B.rdb, C.rdb, D.rdb, ...] # NO lease
  )
  reads    = union of graphs in A, B, C, D, ...
  writes   = only graphs that resolve to A's path
  ingest   = lock conflict impossible for siblings:
             writer on B takes its own exclusive lease
             on B.rdb; MCP A holds no lease on B.rdb

MCP B (independent process) ───────────────────────────
  driver = FalkorDBDriver(path=B.rdb, additional_paths=[A.rdb, C.rdb, ...])
  writes  = only graphs that resolve to B
  ingest  = succeeds because no other process leases B.rdb

dev sync code --instance beta ────────────────────────
  _pause_mcp_for_sync(instance_id="beta")
    _mcp_pids("unified_mcp.py", instance_id="beta")
    → only the B MCP is paused
    → the A MCP keeps reading B.rdb via its falkordblite
```

### Lease surface rule

For an embedded FalkorDB target opened by process P:

- **Primary path** (the file at `CORTEX_STORAGE_INSTANCE=<I>`): the
  driver acquires an exclusive `StorageLease(I)` at construction
  (`falkordb_driver.py:296-301`). Same as today.
- **Sibling paths** (every other `data.rdb` in `instances/*/`): the
  driver opens them via `_open_local_falkordb(candidate)` **without
  acquiring any lease**. The `_additional_storage_leases` list is
  replaced by a simpler `_additional_clients` plus a per-file
  `_additional_open_paths` for diagnostics.
- **Conflict policy**: the writer (sync) for a sibling does not
  conflict with the reader (MCP A). The writer's exclusive lease is
  on its own primary file; MCP A is not in the lock table for that
  file.

### Read consistency

- A read of sibling graph `g` returns whichever snapshot
  `falkordblite` is currently serving.
- falkordblite uses append-only AOF and atomic-rename RDB rewrite, so
  a reader always sees either the previous complete state or the new
  complete state.
- The MCP A response to the caller does **not** include a
  generation/freshness token for sibling reads in this iteration. A
  follow-up plan may add a per-sibling manifest if cross-instance
  staleness becomes a complaint.

### Pause-by-instance (carried over from `1428`)

Unchanged. `_mcp_pids(pattern, instance_id=...)` filters by
`CORTEX_STORAGE_INSTANCE` resolved from the pid-file sidecar. The
`CORTEX_MCP_PAUSE_BY_INSTANCE=0` env flag is the documented legacy
escape hatch and the only surviving "off" toggle.

## Phases

1. [Phase 01 — driver: drop exclusive lease on sibling stores](phase-01-driver-sibling-no-lease.md)
2. [Phase 02 — boot paths and services: revert to default fan-out](phase-02-boot-paths-fanout-default.md)
3. [Phase 03 — discovery: revert to legacy return-every-sibling](phase-03-discovery-revert.md)
4. [Phase 04 — tests: regression for the new default + carry over dev lifecycle tests](phase-04-tests.md)

## Cross-plan dependencies

### `260828-1428-instance-isolated-mcp-locks` (superseded)

Phase 03 (dev lifecycle pause-by-instance) is reused verbatim. The
remaining phases of `1428` are reversed by this plan:

- Phase 01 (lease scoping contract): the new `include_siblings` /
  `exclude_self` kwargs stay but the *default* of
  `discover_falkordb_data_files` flips back to legacy. The
  `CORTEX_MCP_SCOPE_LEASES=0` env flag is removed.
- Phase 02 (boot paths and services): the four boot paths revert
  to passing the full discovery list. `impact_service` and
  `explore_service` revert to `discover_falkordb_data_files()`
  directly without the `cross_instance` gate.
- `code-tiny/mcp/cross_instance.py` is deleted.
- `code-tiny/mcp/cross_instance` env flags and allowlist are removed.

### `260807-0929-mcp-ingest-query-concurrency`

Orthogonal. That plan owns *intra*-target concurrency (one writer
per physical target, generation staging). The exclusive lease on the
**primary** path in this plan is what makes the gateway's "one
writer per target" rule work. No change to that plan.

### `260806-1648-local-file-storage`

Completed. Stated "Multiple MCP processes never open the same
embedded store directly" — that contract is preserved for the
*primary* path; siblings are read through a separate falkordblite
process spawned by the same MCP.

### `260728-0000-unified-ingest-query-contract`

Requires `project_id` on every tool call. Unchanged. Scoped queries
still intersect the caller's `dbs` with `available`, so
per-project isolation is enforced by the caller's intent, not by
the discovery layer.

## Expected file areas

### Reverted runtime contracts

- `code-tiny/tools/graph/driver/falkordb_driver.py`:
  - `_open_additional_local_clients` no longer creates a
    `StorageLease` for siblings. It still opens the file via
    `_open_local_falkordb(candidate)`, registers the graphs, and
    appends to `_additional_clients`. The `_additional_storage_leases`
    list is removed; `_additional_clients` keeps the same name.
  - The `StorageLeaseConflictError` skip path is removed (no
    conflict can occur for siblings).
  - The primary lease code at `falkordb_driver.py:296-301` is
    unchanged.
- `code-tiny/mcp/cplus/cplus_mcp.py:237`,
  `code-tiny/mcp/java/java_mcp.py:202`,
  `code-tiny/mcp/android/android_mcp.py:216`,
  `code-tiny/mcp/fastmcp_server.py:221`:
  `config["additional_paths"] = discover_falkordb_data_files()`.
- `code-tiny/mcp/services/impact_service.py:55`,
  `code-tiny/mcp/services/explore_service.py:266`:
  revert to `discover_falkordb_data_files()` directly. The
  `cross_instance` import is removed.
- `code-tiny/mcp/falkordb_discovery.py:1-130`:
  default `include_siblings=True, exclude_self=False` (or
  equivalently, no kwargs and a return of the full list). The
  `CORTEX_MCP_SCOPE_LEASES=0` env-var branch is removed.

### Deleted modules and flags

- `code-tiny/mcp/cross_instance.py` — delete.
- `CORTEX_MCP_SCOPE_LEASES` — no longer read anywhere.
- `CROSS_INSTANCE_QUERY` — no longer read anywhere.

### Reused runtime contracts (unchanged)

- `cortex_harness/dev.py:1760-2119` — pause-by-instance, sidecar
  pid files, `dev mcp-gates` command, `_legacy_pause_by_instance_disabled`.
- `cortex_harness/storage/lease.py:19-89` — exclusive lock semantics
  on the primary.

### Tests

- `tests/test_mcp_lease_surface.py` — replace the
  "default is self-only" assertions with "default is every sibling,
  and primary lease is exclusive, and sibling has no lease". Carry
  the cross-instance gate tests over as "gate is removed, no env
  var reads".
- `tests/test_dev_pause_by_instance.py` — keep as-is.
- `code-tiny/tests/mcp/cplus/test_cplus_mcp.py` — restore the
  original "every sibling" expectation in
  `test_discovery_honors_relocated_data_home`. Restore
  `test_graph_driver_receives_all_discovered_instance_files` (the
  one that asserted `additional_paths == [primary, sibling]`).
- `tests/test_dev_lifecycle_commands.py` — unchanged.
- New `tests/test_mcp_sibling_no_lease.py` — proves that
  `_open_additional_local_clients` does not call `StorageLease` for
  siblings, and that the primary lease still works.

### Operational evidence

- `plans/260828-1508-multi-instance-fanout-default/reports/revert-summary.md` —
  the commit-by-commit revert trail from `1428` to this plan.
- `docs/development-rules.md` — short note: "MCP fan-out is the
  default; pause-by-instance is the lifecycle invariant; primary
  lease is exclusive; siblings have no lease".

## Scope boundaries

### Included

- Removing the exclusive lease on sibling stores in the FalkorDB
  driver.
- Reverting boot paths and services to pass the full discovery list.
- Deleting `cross_instance.py` and the `CORTEX_MCP_SCOPE_LEASES` /
  `CROSS_INSTANCE_QUERY` env flags.
- Updating tests to assert the new default and the driver
  no-lease behavior.
- Carrying over the dev lifecycle scoping from `1428` Phase 03.

### Excluded

- Adding a per-sibling freshness/generation manifest.
- Switching the primary lease to a shared lock (would re-introduce
  the ingest conflict this plan removes).
- Adding read-side conflict detection between the writer's RDB
  rewrite and the reader's falkordblite spawn (falkordblite's
  append-only / atomic-rename semantics make this acceptably safe).
- Touching `doc-tiny` lifecycle — that subsystem does not pause
  MCPs today and is not in scope.
- Server-mode / remote FalkorDB adapters.

## Success criteria

- `_open_additional_local_clients` makes **zero** `StorageLease`
  calls for siblings. Verified by a unit test that inspects
  `StorageLease.acquire` calls.
- An MCP with `CORTEX_STORAGE_INSTANCE=alpha` opens every
  `instances/*/falkordb/code/data.rdb` for reads and registers
  every graph from every instance in `list_databases()`.
- `dev sync code --instance beta` does not stop MCP A. Verified by
  carrying over `test_dev_pause_by_instance.py` (unchanged) and a
  new end-to-end test that asserts MCP A's pid file is preserved.
- Ingest of instance B (a `dev sync code --instance beta` or a
  direct `StorageLease.acquire()` from an ingest worker) succeeds
  while MCP A is running. Verified by
  `test_ingest_of_beta_succeeds_while_mcp_alpha_holds_alpha` (the
  test from `1428`, repurposed: the lease is on alpha, not beta,
  and the ingest of beta must not conflict).
- Rollback is the commit revert; no env-flag-only fallback.

## Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Reader sees a half-written sibling RDB | falkordblite's append-only AOF + atomic-rename rewrite means a reader sees either the old or the new snapshot, never a torn write. Document the assumption in `docs/development-rules.md`. |
| falkordblite port conflict when MCP A and MCP B both open C's file | Each `falkordblite` instance picks an ephemeral port; documented in `_open_local_falkordb`. No collision. |
| A second MCP for the same primary is launched accidentally | The exclusive primary `StorageLease` raises `StorageLeaseConflictError` at boot; `StorageLease` semantics on the primary are unchanged. |
| `cross_instance` removal breaks a tool the user wrote against it | `cross_instance` shipped in this same commit; no external dependency. |
| Stale references in tests / docs | The revert summary report (Phase 04) lists every file and test that flips; tests and docs updated in the same commit. |
| Future code re-introduces sibling lease | The new test `test_mcp_sibling_no_lease.py` pins the no-lease contract; any regression is caught at CI. |
| Reader fork-bombs the host by spawning one falkordblite per sibling per query | The driver opens sibling clients **once** at construction, not per query. `_open_additional_local_clients` is called from `__init__`. |

## Verification strategy

- Unit:
  - `_open_additional_local_clients` makes zero `StorageLease`
    calls for siblings.
  - `discover_falkordb_data_files()` with no args returns every
    sibling (legacy default restored).
  - `_list_databases()` after driver construction contains graphs
    from every sibling.
- Integration (temporary instance paths):
  - Two MCPs run for `alpha` and `beta` simultaneously; both
    reach `READY`.
  - `_list_databases` from MCP A's driver includes graphs from
    B's file.
  - An ingest of `beta` succeeds while MCP A is running and
    reading B's graphs.
  - `dev sync code --instance beta` pauses only MCP B; MCP A's
    pid file is preserved.
- Regression: all tests in `test_dev_pause_by_instance.py`,
  `test_falkordb_driver_local.py`, `test_cplus_mcp.py`,
  `test_explore_graph_falkor_compat.py`, and
  `test_dev_lifecycle_commands.py` continue to pass.
- Rollout: no flag flip. The change is a code revert in
  `falkordb_driver.py` and the boot paths; if a regression is
  observed, the commit revert restores the prior behavior.

## Delivery command

After approval, implement with:

```text
/hi-craft plans/260828-1508-multi-instance-fanout-default/plan.md
```
