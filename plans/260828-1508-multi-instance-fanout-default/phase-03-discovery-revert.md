# Phase 03 — Discovery: revert to legacy return-every-sibling

## Context

`falkordb_discovery.discover_falkordb_data_files` is the entry point
that lists every `data.rdb` under `<data_home>/v1/instances/*/falkordb/code/`.
Plan `1428` made the function default to "self only" and added the
`CORTEX_MCP_SCOPE_LEASES=0` env var to opt back in. This phase
reverts the default to the legacy behavior and removes the
opt-in/opt-out env var, since the legacy behavior is the new
default.

## Goals

- `discover_falkordb_data_files()` with no args returns every
  sibling, including self.
- The explicit kwargs `include_siblings` and `exclude_self` stay
  available for callers that need to filter (e.g. tests, future
  opt-out). The defaults are
  `include_siblings=True, exclude_self=False`.
- The `CORTEX_MCP_SCOPE_LEASES` env var and the
  `_legacy_include_siblings()` helper are removed.
- The docstring of `falkordb_discovery.py` is updated to describe
  the new default in one sentence and a one-paragraph example.

## Related files

- `code-tiny/mcp/falkordb_discovery.py:1-130`.
- `code-tiny/tests/mcp/cplus/test_cplus_mcp.py`.

## Implementation steps

1. Replace the body of `discover_falkordb_data_files` with the
   pre-`1428` implementation, but keep the explicit
   `include_siblings` / `exclude_self` / `current_instance` /
   `data_home` kwargs for testability:
   ```python
   def discover_falkordb_data_files(
       *,
       include_siblings: bool = True,
       exclude_self: bool = False,
       current_instance: Optional[str] = None,
       data_home: Optional[Path] = None,
   ) -> List[Path]:
       ...
   ```
2. Drop the `LEGACY_INCLUDE_SIBLINGS_ENV` constant and the
   `_legacy_include_siblings` helper.
3. Update the module docstring to remove the "Phase 02/03" language
   and replace with: "Returns every ``data.rdb`` under
   ``<data_home>/v1/instances/*/falkordb/code``. The driver's
   primary lease protects only the path passed as ``path``;
   siblings are read without a lease."
4. Tests:
   - Restore the original
     `test_discovery_honors_relocated_data_home` expectation
     (`discovered == [first, second]`).
   - Remove the `CORTEX_MCP_SCOPE_LEASES=0` test case.
   - Add `test_discovery_explicit_kwargs_filter_siblings` to
     cover the explicit-kwarg path (one test for `exclude_self`
     and one for `include_siblings=False`).
5. Search the codebase for any remaining references to
   `CORTEX_MCP_SCOPE_LEASES` and remove them.

## Risks

- Removing the env var is a public-API change for any user who
  set it manually. Mitigation: the env var never shipped outside
  plan `1428` which was committed the same day; no external
  dependency.
- Reverting the default re-introduces the "every sibling" read at
  every boot. Phase 01 ensures the lease is not acquired for
  siblings, so this is the desired behavior.

## Success criteria

- `discover_falkordb_data_files()` with no args returns every
  sibling on a temp `CORTEX_DATA_HOME` with two instances.
- The explicit kwargs still let a caller filter
  (`include_siblings=False, exclude_self=True` returns just
  the primary file).
- `grep -r "CORTEX_MCP_SCOPE_LEASES" code-tiny/ cortex_harness/`
  returns no matches.
