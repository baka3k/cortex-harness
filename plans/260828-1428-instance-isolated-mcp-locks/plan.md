---
title: "Per-instance isolated MCP leases and lifecycle scoping"
status: pending
created: 2026-08-28
mode: hi-plan --full
scope: "falkordb cross-instance discovery, lease surface per MCP process, dev.py pause-by-instance, targeted regression tests"
blockedBy: []
blocks: []
relatedPlans:
  - 260807-0929-mcp-ingest-query-concurrency
  - 260806-1648-local-file-storage
  - 260728-0000-unified-ingest-query-contract
  - 260807-1202-graph-ingest-write-path-hardening
  - make-mcp-lifecycle
---

# Per-instance isolated MCP leases and lifecycle scoping

## Overview

Right now a single MCP backend (`code-tiny` or `doc-tiny`) holding
`CORTEX_STORAGE_INSTANCE=A` also acquires exclusive `StorageLease`s on the
`data.rdb` of every sibling instance under the same `CORTEX_DATA_HOME`. As a
result:

- `dev sync B` (or any ingest of B) collides with the lease held by MCP A and
  fails.
- A future second MCP process for B cannot start because MCP A already owns
  B's file.
- `dev.py` pauses every `unified_mcp.py`/`mcp_graph_rag.py` process by name
  when it only meant to stop the owner of one instance, so a `dev sync B`
  tears down MCP A.

This plan shrinks each MCP's lease surface to its own instance and tightens
the lifecycle so a sync on instance B no longer touches MCP A. Cross-instance
queries remain available behind an explicit, well-documented opt-in.

## Scope challenge decisions

### 1. What does "per-instance isolation" mean here?

**Decision:** the default MCP boot path leases *only* the resolved instance.
A MCP started with `CORTEX_STORAGE_INSTANCE=A` owns `data.rdb` of A and
nothing else. Cross-instance reads are an opt-in code path used only by
tools that explicitly need them; those tools route through a sibling lease
that records its own owner identity and respects the normal conflict policy.

### 2. How do we keep cross-instance queries available?

**Decision:** introduce a single explicit gate.
`discover_falkordb_data_files(include_siblings: bool = False, exclude_self:
bool = True)` and matching call sites. Boot paths call with the defaults
(only the current instance). A `CROSS_INSTANCE_QUERY=1` opt-in env flag plus
a small `code-tiny/mcp/cross_instance.py` allowlist enables the legacy
"discover every sibling" behavior for tools that need it (impact analysis,
admin). The default MCP server *never* touches a sibling's file unless this
gate is on.

### 3. How does the dev lifecycle stop only the right MCP?

**Decision:** keep `_mcp_pids(pattern)` as the broad scan but add an
`env_match` filter. Each MCP is launched with
`CORTEX_STORAGE_INSTANCE` baked into its env (already true via
`_mcp_start_one` → `process_env`). `_mcp_pids` reads `/proc/<pid>/environ`
(Linux) and `ps eww` (macOS) / Win32 environment block (already read for
pid-file diagnostics) and drops processes whose `CORTEX_STORAGE_INSTANCE`
does not match the target instance, with the existing pattern still
required as the first filter. `_pause_mcp_for_sync` passes the instance
through. The pid-file written by `_mcp_start_one` now also records the
instance id so the fast path can use it without re-reading process env.

### 4. What is the rollback path?

**Decision:** both gates are behind env flags with the *current* behavior as
the default until rollout gates pass.

- `CORTEX_MCP_SCOPE_LEASES=0` keeps the legacy "lease every sibling"
  behavior in `discover_falkordb_data_files` boot path.
- `CORTEX_MCP_PAUSE_BY_INSTANCE=0` keeps the legacy pattern-only pause.
- `dev status` and `dev doctor` print the active gate values.

## Verified baseline

- `code-tiny/mcp/falkordb_discovery.py:22` returns every
  `<data_home>/v1/instances/*/falkordb/code/data.rdb` with no filter on
  `CORTEX_STORAGE_INSTANCE`.
- `code-tiny/mcp/cplus/cplus_mcp.py:234`, `code-tiny/mcp/java/java_mcp.py`,
  `code-tiny/mcp/android/android_mcp.py`, `code-tiny/mcp/fastmcp_server.py`,
  `code-tiny/mcp/services/impact_service.py`,
  `code-tiny/mcp/services/explore_service.py` all pass the unfiltered list
  to `get_shared_graph_driver` as `additional_paths`.
- `code-tiny/tools/graph/driver/falkordb_driver.py:392`
  `_open_additional_local_clients` calls `StorageLease(...).acquire()` on
  each sibling path. The lease is keyed by `(target, instance_id, owner_id,
  backend)` (`cortex_harness/storage/lease.py:20-58`), so the lock conflict
  is real, not advisory.
- `cortex_harness/dev.py:1818` `_mcp_stop_pattern(pattern)` runs
  `_mcp_pids("unified_mcp.py")` and `kill -TERM` every match with no
  instance filter. `_mcp_start_one` already sets
  `CORTEX_STORAGE_INSTANCE` into the MCP process env
  (`cortex_harness/dev.py:2620`).
- `cortex_harness/storage/lease.py:44-79` `acquire()` is fail-fast on
  conflict and returns holder metadata; the driver catches
  `StorageLeaseConflictError` and logs `"Skipping owned FalkorDB instance …"`,
  which is why the cross-instance read does not crash but silently
  degrades to one owner.

## Target architecture

```text
                CORTEX_STORAGE_INSTANCE=A
MCP A ───────────────────────────────────────────────
  driver = FalkorDBDriver(path=data.rdb of A)
  leases  = {A}                          # was: {A, B, C, ...}
  queries = scoped to A by default
  opt-in  = CROSS_INSTANCE_QUERY=1 → opens sibling paths
            via the same _open_additional_local_clients path,
            but with skip-on-conflict semantics already in place

MCP B (independent process) ────────────────────────
  driver = FalkorDBDriver(path=data.rdb of B)
  leases  = {B}
  ingest  = succeeds because nothing else owns B

dev sync B ──────────────────────────────────────────
  _pause_mcp_for_sync(B) →
    _mcp_pids("unified_mcp.py") filtered by
    CORTEX_STORAGE_INSTANCE == B
    → only B's MCP is paused
    → A keeps serving queries
```

### Lease surface rule

For an embedded FalkorDB target owned by process P with env
`CORTEX_STORAGE_INSTANCE=I`:

- P's primary lease covers only the target under `instances/I/`.
- A sibling lease is only opened if both:
  1. The call site explicitly passes a `CROSS_INSTANCE_QUERY` allowlisted
     tool, and
  2. The cross-instance gate is enabled at the process level.

Sibling discovery still scans `instances/*/`, but boot-path callers
default to `include_siblings=False` so they only see their own instance.

### Pause-by-instance rule

`_mcp_pids(pattern, *, instance_id=None)` returns the same broad scan as
today when `instance_id is None` (legacy callers), and an instance-filtered
list otherwise. Filtering is done by reading each PID's environment block
and matching `CORTEX_STORAGE_INSTANCE=instance_id`. On platforms where the
environment cannot be read, the pid-file sidecar written by
`_mcp_start_one` (now `dev-mcp-{name}-{instance_id}.pid` with an
`instance_id` line) is the authoritative fallback. `_pause_mcp_for_sync`
passes the resolved instance id so only the owning MCP pauses.

### Process and lifecycle rules

- `_mcp_start_one` writes a pid-file sidecar containing both PID and
  `CORTEX_STORAGE_INSTANCE`. The instance id is sourced from
  `_resolve_storage_instance(process_env)` so the same value used at start
  is the one used at stop.
- `_mcp_stop_pattern(pattern, instance_id=None)` keeps the legacy behavior
  when `instance_id is None` (only `_pause_mcp_for_sync` calls it with a
  non-None value today). New callers should always pass `instance_id`.
- A diagnostic warning is logged when more than one PID matches the
  pattern+instance pair (e.g. stale orphan) so the conflict is visible
  before any kill.

## Phases

1. [Phase 01 — lease scoping contract, default flags, and baseline metrics](phase-01-contract-and-baseline.md)
2. [Phase 02 — `discover_falkordb_data_files` filtering and per-tool opt-in](phase-02-discovery-filtering.md)
3. [Phase 03 — dev lifecycle pause-by-instance and pid-file sidecar](phase-03-dev-lifecycle-scoping.md)
4. [Phase 04 — regression tests, end-to-end isolation proof, rollout gates](phase-04-tests-and-rollout.md)

## Cross-plan dependencies

- `260807-0929-mcp-ingest-query-concurrency` covers *intra*-target
  concurrency (one owner per physical target, generation staging, bounded
  admission). This plan is the orthogonal *inter*-target axis: it does not
  weaken `StorageLease`, change the gateway contract, or alter generation
  staging. Phase 02 here passes `additional_paths=[]` from MCP boot paths
  by default, which lines up with the gateway's expectation that one MCP
  process is one owner — no conflict with that plan.
- `260806-1648-local-file-storage` already states "Multiple MCP processes
  never open the same embedded store directly". This plan makes that
  statement true when more than one MCP process is run (one per instance),
  which today is not exercised. No scope conflict.
- `260728-0000-unified-ingest-query-contract` requires `project_id` on
  every tool call; this plan does not change that contract.
- `260807-1202-graph-ingest-write-path-hardening` owns the writer-local
  journal inside one ingestion job. This plan only changes which lease is
  acquired before that journal runs; the journal itself is untouched.
- `make-mcp-lifecycle` plans how MCP processes are managed. This plan
  aligns `_mcp_pids` with that lifecycle model; if `make-mcp-lifecycle`
  introduces per-instance ports, the env-match in `_mcp_pids` becomes
  belt-and-braces rather than the only filter.

## Expected file areas

### Changed runtime contracts

- `code-tiny/mcp/falkordb_discovery.py` — add `include_siblings` and
  `exclude_self` parameters; default boot path filters out self.
- `code-tiny/mcp/cross_instance.py` (new) — central allowlist of tool
  names + flag check; small surface to keep audit easy.
- `code-tiny/mcp/cplus/cplus_mcp.py:211`,
  `code-tiny/mcp/java/java_mcp.py`,
  `code-tiny/mcp/android/android_mcp.py`,
  `code-tiny/mcp/fastmcp_server.py`,
  `code-tiny/mcp/services/explore_service.py`,
  `code-tiny/mcp/services/impact_service.py` — call with the new defaults;
  opt-in tools go through `cross_instance.is_allowed(...)`.
- `code-tiny/tools/graph/driver/falkordb_driver.py` — keep the
  `_open_additional_local_clients` body unchanged; the new contract is at
  the *call site*, not inside the driver. Driver already logs on conflict
  and skips siblings cleanly.
- `cortex_harness/dev.py` — `_mcp_pids` accepts `instance_id`,
  `_mcp_stop_pattern` forwards it, `_mcp_start_one` writes the sidecar pid
  file with instance metadata, `_pause_mcp_for_sync` resolves the target
  instance and passes it down.
- `cortex_harness/dev.py` — `dev status` and `dev doctor` print the active
  gate flags.

### Tests

- `code-tiny/tests/mcp/cplus/test_cplus_mcp.py` — extend the relocated-data-
  home test to cover the new `include_siblings`/`exclude_self` parameters.
- New `tests/test_dev_pause_by_instance.py` — proves that pausing one
  instance does not stop MCP processes of another instance.
- New `tests/test_mcp_lease_surface.py` — proves MCP A acquires only its
  own lease and dev sync of B succeeds.
- Update `tests/test_falkordb_driver_local.py` — the existing
  `additional_paths=[sibling_path]` tests stay valid; add a negative test
  showing that an MCP boot path no longer reaches the driver's sibling path
  by default.

### Operational evidence

- `docs/development-rules.md` add a short note on per-instance MCP and the
  two env flags.
- `docs/UNIFIED_INGEST_QUERY_CONTRACT.md` add a paragraph on
  `CROSS_INSTANCE_QUERY`.
- `dev doctor` output sample committed under
  `plans/260828-1428-instance-isolated-mcp-locks/reports/`.

## Scope boundaries

### Included

- Lease scoping for the FalkorDB code owner (the only embedded store
  surfaced via `additional_paths` today).
- Pause-by-instance in `cortex_harness/dev.py`.
- Default flags + `dev doctor` surfacing.
- Targeted unit + integration tests proving the new behavior on
  temporary instance paths.
- Rollout gates (no global flip until the regression suite passes on the
  disposable target).

### Excluded

- Removing or weakening `StorageLease`.
- Multi-process access to one embedded target.
- Federated cross-server query planning.
- Document-owner (`doc-tiny`) pause scoping — this plan does not touch
  `mcp_graph_rag.py` because the lifecycle script does not pause it today;
  a follow-up may add the same gate if `make-mcp-lifecycle` adopts it.
- A new remote/server substrate.
- Changing parser or analyzer semantics.

## Success criteria

- An MCP boot path with `CORTEX_STORAGE_INSTANCE=A` acquires only the lease
  for A; nothing else. Verified by a test that inspects the leases owned
  by the driver.
- `dev sync B` no longer stops MCP A; verified by a test that runs
  `_pause_mcp_for_sync("code", instance=B)` and asserts MCP A's PID is
  still alive afterwards.
- A new MCP B started against the same `CORTEX_DATA_HOME` succeeds while
  MCP A is running; verified by a test that boots both processes against
  temporary paths and asserts both reach `READY` without lease conflict.
- Cross-instance query still works when `CROSS_INSTANCE_QUERY=1` is set
  and the tool is on the allowlist. Verified by an existing-style test
  against a temporary sibling path.
- Rollback: setting `CORTEX_MCP_SCOPE_LEASES=0` and
  `CORTEX_MCP_PAUSE_BY_INSTANCE=0` restores the legacy behavior with no
  code change required.

## Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| A user relied on cross-instance reads by default | The default behavior is gated, not removed; `CROSS_INSTANCE_QUERY=1` plus allowlisted tools restores it. `dev doctor` warns when the gate is off and a sibling write conflict is logged. |
| Reading process env is platform-fragile | pid-file sidecar written by `_mcp_start_one` carries the instance id; use it as the primary filter, env read as a backup. Tests cover both. |
| Stale pid files after crash leave orphan processes | `_mcp_stop_pattern` already SIGTERMs then SIGKILLs after a deadline; the new filter still applies the deadline and the kill sequence. A new diagnostic warns when more than one PID matches the (pattern, instance) pair. |
| Changing default behavior breaks an undocumented caller | The legacy env flag is honored first; rollout goes through feature-flag-on-by-default only after Phase 04 gates pass. |
| Driver still opens siblings when given the list | That is correct behavior; the change is upstream at the call site. The driver's `StorageLeaseConflictError` skip path remains. |
| Filter logic diverges between cplus/java/android/services | Centralize in `code-tiny/mcp/cross_instance.py` and a single helper that wraps `discover_falkordb_data_files(...)` with the boot defaults. |

## Verification strategy

- Unit: `discover_falkordb_data_files` filters by `CORTEX_STORAGE_INSTANCE`
  in the boot default; the cross-instance allowlist is honored; pid-file
  sidecar round-trips.
- Integration (temporary instance paths): MCP A holds only lease A; dev
  sync on B succeeds; new MCP B starts while A is running; both serve
  queries concurrently.
- Regression: existing `test_falkordb_driver_local.py`,
  `test_cplus_mcp.py`, `test_explore_graph_falkor_compat.py` keep passing;
  new tests added for the gate.
- Rollout: both env flags default to "off" (legacy behavior) until the
  disposable-target integration tests pass twice consecutively, then flip
  the defaults and document the gate values in `dev doctor`.

## Delivery command

After approval, implement with:

```text
/hi-craft plans/260828-1428-instance-isolated-mcp-locks/plan.md
```
