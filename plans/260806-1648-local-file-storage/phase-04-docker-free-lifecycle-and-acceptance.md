# Phase 04: Docker-Free Lifecycle and Acceptance

## Context

Even after local clients exist, startup remains Docker-dependent until the
cross-platform lifecycle scripts, Make targets, CLI help, doctor checks,
installers, documentation, and tests stop managing containers and probing
database ports.

## Requirements

- No startup, sync, MCP, or doctor workflow invokes Docker.
- Local storage initialization is idempotent and non-destructive.
- macOS, Windows, and Linux launchers have equivalent behavior.
- Documentation contains a complete local quick start and data-location guide.
- Acceptance proves persistence and simultaneous MCP operation.
- Lifecycle commands expose instance/owner identity and migrate the existing
  repository-local layout without data loss.

## Architecture

Replace service lifecycle management with storage lifecycle management:

- `storage layout`: report data root, schema version, instance, owner paths,
  logical targets, and active leases;
- `storage-init`: resolve the instance tree, create owner directories and the
  manifest, open/close stores, and report logical targets;
- `storage migrate-layout`: dry-run by default; require owners stopped, copy
  legacy repository-local stores, hash/inventory/reopen them, switch only after
  validation, and retain sources;
- `storage backup`: stop or quiesce one owner, snapshot its Qdrant and FalkorDB
  data with a manifest, then reopen and verify;
- `doctor`: check Python/dependencies/platform libraries, path permissions,
  Qdrant round-trip, FalkorDB graph round-trip, and MCP ports only;
- `start`/`stop`: manage MCP processes, not databases;
- optional one-release aliases for `infra-up`/`infra-down` may initialize/no-op
  locally with a deprecation message, but contain no Docker code.

## Related Files

- `Makefile`
- `scripts/mcp-lifecycle.py`
- `scripts/mcp-lifecycle.ps1`
- `cortex_harness/dev.py`
- platform installers under `installers/`
- `ReadMe.md`
- `docs/DATABASE_INTEGRATION.md`
- `docs/HARNESS_WORKFLOW.md`
- `code-tiny/README.md`
- `code-tiny/mcp/Readme.md`
- `doc-tiny/Readme.md`
- `tests/test_make_lifecycle.py`
- `tests/test_dev_init_graph_provider.py`
- `tests/test_mcp_runtime_config.py`
- new end-to-end local storage smoke tests

## Implementation Steps

1. Remove Docker image/container/volume discovery and mutation from Python and
   PowerShell lifecycle scripts.
2. Add equivalent `storage-init`/doctor implementations on POSIX and Windows.
3. Update Make and `dev` commands/help; keep only explicitly chosen
   compatibility aliases and test their no-Docker behavior.
4. Replace database port/HTTP/Redis readiness probes with temporary local
   client read/write probes that cannot modify production collections/graphs.
   Report duplicate-owner leases separately from permissions or corruption.
5. Update dependency checks for Python 3.12, `qdrant_client`, `falkordblite`,
   and platform requirements such as macOS `libomp`.
6. Update installers to provision runtime dependencies without Docker Desktop.
7. Rewrite quick-start, database configuration, troubleshooting, backup, and
   reset documentation around local paths and `.rdb`/Qdrant directories.
8. Document the canonical tree, instance/owner/project identity model, safe
   concurrent-access rules, backup/restore, repository-local migration, and the
   explicit rule that databases are centralized Cortex Harness application
   data under the current account's `~/.cortext-harness` directory rather than
   source-project cache.
9. Document explicit remote export/re-ingest steps without deleting source
   services or volumes.
10. Run unit suites for config, driver, vector adapter, lifecycle, MCP, and
   project scoping.
11. Run a clean end-to-end acceptance sequence with two projects and two named
    instances: install, init, storage init, code ingest, doc ingest, start both
    MCP servers, query both projects, reject a duplicate owner, stop, restart,
    query persisted data, reset one project, and confirm the other project and
    instance remain unchanged.
    Extend path-resolution coverage to ten projects in ten unrelated source
    directories and prove none receives a local database directory.
12. Run migration acceptance from the current
    `./local_qdrant_db/{code,doc}` and `./local_falkordb_db` layout. Verify
    target hashes/inventories and prove the source remains recoverable.
13. Add exact-search gates for Docker invocations, remote database defaults,
   raw Qdrant REST paths, generated local database files in Git status, and
   hardcoded `/Users/<name>`, `/home/<name>`, or `C:\\Users\\<name>` defaults.

## Todo

- [ ] Remove Docker lifecycle code on both platforms.
- [ ] Add local storage commands and doctor checks.
- [ ] Add layout, migration, backup, and owner-lease diagnostics.
- [ ] Update installers and documentation.
- [ ] Run unit and integration suites.
- [ ] Run simultaneous MCP and restart-persistence acceptance.
- [ ] Confirm clean Git status except intended source changes.

## Risks

- PowerShell and POSIX lifecycle implementations can drift unless backed by the
  same acceptance cases.
- A doctor write probe must use temporary data and always clean up its own test
  graph/collection.
- Existing users may rely on `infra-up`; deprecation behavior must be explicit.

## Success Criteria

- Docker is absent and the application starts, ingests, queries, and restarts
  successfully.
- Doctor reports local paths and actionable platform dependency failures.
- Both MCP servers run together without storage locks or external database
  listeners.
- Two named instances run concurrently with disjoint paths; a duplicate owner
  in one instance fails before database open.
- Two projects coexist inside each role owner and a scoped reset does not
  affect the other project.
- Restart tests prove graph and vector persistence.
- Migration tests prove repository-local data is copied, validated, switched,
  and retained non-destructively.
- macOS, Linux, and Windows acceptance resolves
  `<current-user-home>/.cortext-harness` from the running account without a
  hardcoded username or platform application-data directory.
- Documentation and command help describe only the supported local startup
  path, with separate migration guidance for legacy remote data.
