# Phase 02: FalkorDBLite Graph Runtime

## Context

The existing provider-neutral graph layer is valuable, but the concrete
FalkorDB driver creates a remote `falkordb.FalkorDB` client and assumes Redis
network retries. The local implementation must preserve its normalized result
shape and Cypher compatibility behavior while changing backend ownership.

## Requirements

- Open the configured `.rdb` file through `falkordblite`.
- Preserve graph selection, query parameters, normalized rows, schema helpers,
  and driver method contracts.
- Persist data across close/reopen and application restarts.
- Support many project graphs within each owner file without external
  services.
- Give code, document, and additional MCP owners distinct `.rdb` files.
- Close embedded resources deterministically.

## Architecture

Keep `GraphProvider.FALKORDB` as the logical provider to avoid unrelated writer
and query churn. Extend `FalkorDBDriver` with a role-derived local `path`
configuration and encapsulate the pinned FalkorDBLite import in one constructor
helper. The driver continues to return `(records, keys, summary)` and to expose
the selected graph for schema helpers. One owner file contains multiple
registry-resolved project graphs; different MCP owners never construct
FalkorDBLite against the same file.

## Related Files

- `code-tiny/tools/graph/driver/falkordb_driver.py`
- `code-tiny/tools/graph/core/factory.py`
- `code-tiny/tools/graph/cli.py`
- `code-tiny/tools/graph/core/provider_runtime.py`
- `doc-tiny/graph_store.py`
- `code-tiny/tools/sync/incremental_sync.py`
- graph setup/reset scripts
- `tests/test_falkordb_driver.py`
- `code-tiny/tests/test_driver/test_falkordb_driver.py`
- `tests/test_doc_graph_store.py`

## Implementation Steps

1. Add owner-aware `path` to the driver/factory configuration and make it the
   canonical FalkorDB setting. The launcher resolves
   `FALKORDB_CODE_PATH`/`FALKORDB_DOC_PATH` to the process-local
   `FALKORDB_PATH` compatibility value.
2. Instantiate the documented backend from the pinned `falkordblite` package,
   using the user-requested direct import only if the pinned version actually
   exports it.
3. Resolve/create the parent directory before opening the `.rdb` file and emit
   the absolute path in safe diagnostic messages.
4. Retain query normalization and result conversion; remove remote URI parsing,
   TCP/TLS/auth configuration, and connection retry behavior that is not
   applicable to the embedded backend.
5. Verify `select_graph`, `list_graphs`, `query`, read-only query, index helpers,
   and close semantics against the pinned package.
6. Replace shared CLI options with `--falkordb-path`; preserve
   `--falkordb-graph` as the logical graph selector.
7. Update `doc-tiny/graph_store.py` to construct the same local driver and keep
   its Neo4j-like session adapter stable for ingest/query scripts.
8. Acquire the application owner lease before constructing FalkorDBLite and
   release it only after the embedded backend is closed. A duplicate owner
   fails before any `.rdb` access.
9. Put all registered project graphs for a role in that role's owner file and
   prove project-scoped reset never deletes another graph.
10. Add temporary-directory persistence, multiple-project graph, schema,
    batch-write, query normalization, reset, duplicate-owner, and error-path
    tests.

## Todo

- [ ] Implement local backend construction.
- [ ] Remove canonical network configuration.
- [ ] Update factory, CLI, and document adapter.
- [ ] Verify package API and graph/schema behavior.
- [ ] Add owner lease and separate role files.
- [ ] Add restart and concurrent-service tests.

## Risks

- FalkorDBLite still manages an embedded Redis/FalkorDB process internally;
  close and multi-process ownership semantics must be tested, not assumed. The
  safe default is one process owner per `.rdb`.
- Inheriting high-level methods from `Neo4jDriver` retains query-shape
  dependencies; existing FalkorDB normalization tests must remain green.
- The embedded library may have platform-specific binary or OpenMP needs.

## Success Criteria

- The role-derived `FALKORDB_PATH` plus registry graph name is sufficient to
  construct the driver.
- Code and document owners run concurrently with distinct `.rdb` files; a
  second process using either owner is rejected with an actionable diagnostic.
- At least two project graphs coexist in one owner file and remain isolated
  across query and reset operations.
- No local graph startup path needs a URI, host, port, credentials, TLS, Docker,
  or a pre-running Redis/FalkorDB service.
- Data written to a temporary `.rdb` is readable after the driver and process
  are restarted.
- Existing provider-neutral driver, writer, and document graph-store contracts
  remain compatible.
