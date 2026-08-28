# Phase 02 — Boot paths and services: revert to default fan-out

## Context

Plan `260828-1428-instance-isolated-mcp-locks` Phase 02 changed the
four boot paths (cplus / java / android / fastmcp_server) to pass
`additional_paths = []`, and routed `impact_service` and
`explore_service` through `cross_instance.self_and_allowed_siblings_paths(...)`.
With the new requirement, fan-out is the default — the boot paths
and the service-level readers go back to the legacy call shape.

## Goals

- The four boot paths pass the full discovery list to the driver.
- `impact_service` and `explore_service` call
  `discover_falkordb_data_files()` directly with no gate.
- `code-tiny/mcp/cross_instance.py` is deleted.
- The `CORTEX_MCP_SCOPE_LEASES=0` env-var branch in
  `falkordb_discovery.py` is removed.
- The `CROSS_INSTANCE_QUERY` env var and the `ALLOWLIST` are no
  longer read anywhere; remove the constants and the helper.

## Related files

- `code-tiny/mcp/cplus/cplus_mcp.py:237`.
- `code-tiny/mcp/java/java_mcp.py:202`.
- `code-tiny/mcp/android/android_mcp.py:216`.
- `code-tiny/mcp/fastmcp_server.py:221`.
- `code-tiny/mcp/services/impact_service.py:55`.
- `code-tiny/mcp/services/explore_service.py:266`.
- `code-tiny/mcp/cross_instance.py` (delete).
- `code-tiny/mcp/falkordb_discovery.py:1-130` (revert default).

## Implementation steps

1. Revert the four boot paths: each
   `config["additional_paths"] = []` becomes
   `config["additional_paths"] = discover_falkordb_data_files()`.
2. Revert `impact_service.py:55`:
   - Remove `from cross_instance import self_and_allowed_siblings_paths`.
   - Restore the original
     `"additional_paths": discover_falkordb_data_files()`.
3. Revert `explore_service.py:266` similarly.
4. Delete `code-tiny/mcp/cross_instance.py`.
5. In `falkordb_discovery.py`:
   - Revert the default kwargs of
     `discover_falkordb_data_files` to "every sibling, including
     self" — the pre-`1428` behavior. Keep the explicit kwargs
     (`include_siblings`, `exclude_self`) for testability and
     future opt-out callers, but make the defaults
     `include_siblings=True, exclude_self=False`.
   - Remove the `LEGACY_INCLUDE_SIBLINGS_ENV` constant and the
     `_legacy_include_siblings` helper.
6. Run the existing tests and update the affected assertions:
   - `code-tiny/tests/mcp/cplus/test_cplus_mcp.py`:
     - `test_discovery_honors_relocated_data_home` should now
       pass with the original expectation
       (`discovered == [first, second]`).
     - The new tests added in plan `1428`
       (`test_discovery_siblings_exclude_self_by_default`,
       `test_discovery_siblings_include_self_when_exclude_self_false`)
       are removed; the explicit-kwarg behavior is covered by
       a single new test
       `test_discovery_explicit_kwargs_filter_siblings`.
7. Search the rest of the tree for references to
   `cross_instance`, `CORTEX_MCP_SCOPE_LEASES`, or
   `CROSS_INSTANCE_QUERY` and remove them.

## Risks

- A reader of plan `1428` might have wired a tool against
  `cross_instance.is_allowed`. Mitigation: `cross_instance` shipped
  in the same commit; no external dependency. The git log records
  the change.
- A future contributor might re-introduce a sibling lease. The new
  test in Phase 01 (`test_mcp_sibling_no_lease.py`) pins the
  no-lease contract at the driver level, which is independent of
  the boot path.
- `discover_falkordb_data_files` becomes a "do what it says" helper
  again; that is the desired pre-`1428` contract.

## Success criteria

- `grep -r "cross_instance" code-tiny/ cortex_harness/ doc-tiny/`
  returns no source matches.
- `grep -r "CORTEX_MCP_SCOPE_LEASES\|CROSS_INSTANCE_QUERY" code-tiny/`
  returns no source matches.
- `tests/test_mcp_lease_surface.py::CrossInstanceGateTests` is
  removed; `LeaseSurfaceTests` is updated to assert the
  fan-out default instead of the self-only default.
- All previously passing tests still pass after the revert.
