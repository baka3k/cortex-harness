# Phase 01 — Driver: drop exclusive lease on sibling stores

## Context

The FalkorDB driver in
`code-tiny/tools/graph/driver/falkordb_driver.py` is the single point
that opens both the primary and the sibling stores. Today it acquires
an exclusive `StorageLease` on every sibling in
`_open_additional_local_clients` (`falkordb_driver.py:418-425`). The
plan `260828-1428-instance-isolated-mcp-locks` work-around was to
stop *passing* siblings to the driver; the actual fix is to keep
siblings open and stop *leasing* them.

## Goals

- `_open_additional_local_clients` opens every sibling `data.rdb`
  via `_open_local_falkordb(candidate)` without acquiring any
  `StorageLease`.
- The primary lease code at `falkordb_driver.py:296-301` is
  unchanged.
- The driver constructor still honors the legacy
  `additional_paths=[]` opt-out (e.g. for a server-mode driver or a
  test fixture) so that callers can still pass an empty list when
  they do not want sibling fan-out.
- The `StorageLeaseConflictError` skip path in
  `_open_additional_local_clients` is removed — no lease, no
  conflict.
- The `_additional_storage_leases` list is removed; diagnostics
  track `_additional_open_paths` instead.

## Related files

- `code-tiny/tools/graph/driver/falkordb_driver.py:392-436`
  (`_open_additional_local_clients`).
- `code-tiny/tests/mcp/cplus/test_cplus_mcp.py` (assertions about
  the driver's `additional_paths`).
- `tests/test_falkordb_driver_local.py` (existing
  `additional_paths=[sibling_path]` direct-driver tests).

## Implementation steps

1. Rewrite `_open_additional_local_clients` so it:
   - Skips the primary path (already owned by the exclusive lease
     on `self._path`).
   - For each remaining candidate:
     - Resolves `instance_id` from
       `candidate.parents[2].name` (no change).
     - Opens a read-only falkordblite via
       `_open_local_falkordb(candidate)`.
     - Appends the client to `self._additional_clients` and the
       path to a new `self._additional_open_paths: list[Path]`.
     - Registers graphs via `_register_client_graphs(client)`.
   - No `StorageLease` call anywhere in the method.
2. Remove the `StorageLeaseConflictError` import / branch — it is
   unreachable once the lease is gone. Keep the generic `Exception`
   branch so a malformed sibling file still degrades to a warning
   instead of crashing the driver.
3. Update the docstring to state: "Sibling stores are opened for
   read-only fan-out. No application lease is held; concurrent
   writes to a sibling by its owner are allowed."
4. Adjust the constructor (`falkordb_driver.py:295-312`) to drop
   the `StorageLease` reference path for siblings — only the
   primary lease path stays. No API change at the constructor
   level.
5. Tests:
   - Update `code-tiny/tests/mcp/cplus/test_cplus_mcp.py`:
     - Restore the original test
       `test_graph_driver_receives_all_discovered_instance_files`
       that asserted
       `additional_paths == [primary_path, sibling_path]`.
     - Update `test_discovery_honors_relocated_data_home` to
       assert the legacy default returns every sibling.
   - Keep `tests/test_falkordb_driver_local.py` as-is — its
     `additional_paths=[sibling_path]` tests already exercise
     the read-only sibling path.
   - New `tests/test_mcp_sibling_no_lease.py`:
     - Patch `StorageLease.acquire` and assert it is called
       exactly once (for the primary) when the driver is built
       with `additional_paths=[sibling_path]`.
     - Assert `self._additional_storage_leases` is empty or
       removed.
     - Assert `len(self._additional_clients) == 1` and the
       sibling's graphs are registered.

## Risks

- The driver still imports `StorageLease` for the primary lease,
  so the symbol stays; we just stop using it for siblings.
- The `_open_additional_local_clients` change has no other
  call sites in the driver; the only consumer is the constructor.
- The falkordblite port allocation must remain per-process. The
  existing `_open_local_falkordb` helper handles that; no change
  here.

## Success criteria

- `StorageLease.acquire` is called exactly once per driver
  construction regardless of how many siblings are passed.
- A driver built with `additional_paths=[sibling_path]` returns
  sibling graphs from `list_databases()`.
- The primary `StorageLeaseConflictError` semantics on the
  primary file are unchanged.
