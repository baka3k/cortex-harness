---
title: "Docker-Free Local Qdrant and FalkorDBLite Storage"
status: in_progress
created: 2026-08-06
updated: 2026-08-06
mode: hi-plan --fast
scope: runtime storage, configuration, launchers, dependencies, tests, documentation
relatedPlans:
  - neo4j-to-falkordb-migration
  - 260728-0000-unified-ingest-query-contract
blockedBy: []
---

# Docker-Free Local Qdrant and FalkorDBLite Storage

## Overview

Convert Cortex Harness from Docker-managed Qdrant and FalkorDB services to
machine-local persistent storage with explicit instance and process ownership:

- Qdrant uses `qdrant-client` local mode beneath a user data root, partitioned
  by harness instance and storage owner.
- FalkorDB uses the `falkordblite` package with one `.rdb` file per storage
  owner beneath the same instance root.
- `dev`, Make, MCP launchers, analyzers, document ingestion, reset/validation
  scripts, and tests start without Docker and without services listening on
  ports 6333 or 6379.
- Multiple projects share each role-owned physical store and remain isolated
  by registry-resolved graph and collection names.
- Multiple MCP processes never open the same embedded store directly. Each
  store has one stable owner; other MCPs use the owner MCP's interface or use a
  distinct store.

This is a hard local-runtime cutover. Remote Qdrant/FalkorDB settings may be
reintroduced later as an explicit optional backend, but they are not part of
this plan's startup or acceptance path.

## Verified Current State

- `scripts/mcp-lifecycle.py` and `scripts/mcp-lifecycle.ps1` pull images,
  create containers and volumes, test Docker, and probe Qdrant/FalkorDB ports.
- `Makefile` and `cortex_harness/dev.py` expose `infra-up`, `infra-down`, and a
  Docker-aware `doctor` command.
- `code-tiny/tools/graph/driver/falkordb_driver.py` imports
  `falkordb.FalkorDB` and constructs a Redis/RESP client from URI, host, port,
  credentials, and TLS settings.
- `code-tiny/tools/graph/core/factory.py`, `code-tiny/tools/graph/cli.py`,
  `doc-tiny/graph_store.py`, and runtime config loaders propagate those network
  settings throughout code and document workflows.
- Direct `QdrantClient(...)` construction exists in the main `doc-tiny`
  reset, ingest, query, MCP, and legacy loader scripts.
- Much of `code-tiny` does not use `qdrant-client`; it calls Qdrant's REST API
  directly for collection management, upsert, query, scroll, payload update,
  cleanup, Living Docs, and validation. Local Qdrant mode does not expose that
  HTTP API, so configuration-only changes are insufficient.
- The repository currently supports Python `>=3.10`, while the current
  official FalkorDBLite documentation requires Python `>=3.12`.
- Qdrant local persistent storage takes an exclusive lock on its storage
  directory. The code and document MCP servers therefore cannot each open one
  shared `./local_qdrant_db` path concurrently.
- A baseline local implementation now exists in the worktree under
  `cortex_harness/storage/`, with current defaults rooted at
  `./local_qdrant_db/{code,doc}` and `./local_falkordb_db/cortex.rdb`.
  Those repository-local paths are suitable for a smoke test but do not give
  stable ownership across multiple checked-out projects or named MCP
  instances, so the directory contract must be corrected before acceptance.
- The current local data footprint contains initialized Qdrant `code` and
  `doc` stores and an empty FalkorDB directory. Migration must preserve these
  paths as sources and must not delete or overwrite them.
- Existing project-registry lookup may walk upward from the current directory
  to find `.cortext-harness/config`. That behavior is configuration discovery
  only. Database-root resolution must be a separate function beginning at
  `Path.home()` and must never reuse an upward CWD search.

## Target Storage Contract

### Storage location decision: centralized, not project-local cache

**Default decision:** Qdrant and FalkorDB data is persistent Cortex Harness
application data stored centrally under the current user's Cortex Harness data
directory. It is not stored beside each indexed source project and is not a
disposable cache.

The ten-project case behaves as follows:

```text
Source projects (may live anywhere and may move):
  /work/customer-a/service-api
  /work/customer-b/mobile-app
  /Users/alice/src/project-03
  /Volumes/code/project-04
  ... six more unrelated locations ...

One centralized Cortex Harness data root:
  <current-user-home>/.cortext-harness/v1/instances/default/
  ├── qdrant/code/           # code collections for all 10 projects
  ├── qdrant/doc/            # document collections for all 10 projects
  ├── falkordb/code/data.rdb # code graphs for all 10 projects
  └── falkordb/doc/data.rdb  # document graphs for all 10 projects
```

`ProjectRegistry` records each source location and maps its `project_id` to
logical graph and collection names. Qdrant manages those collections inside
the owner directory; Cortex Harness must not create or depend on a manual
`qdrant/<project-id>/` directory. FalkorDB stores multiple named project graphs
inside the role-owned `.rdb` file.

This gives the following lifecycle rules:

- Moving or renaming a source-project directory updates its registered source
  path but does not move, duplicate, or invalidate its database storage.
- Removing a source checkout does not implicitly delete indexed data. Project
  data is removed only through an explicit project-scoped purge/reset command.
- Deleting a source project's build caches does not affect Cortex Harness
  databases; deleting the centralized data root does.
- Backups are taken from the centralized instance/owner paths, not from ten
  source repositories.
- A project-local database layout is allowed only through an explicit
  `CORTEX_DATA_HOME` override for an isolated/portable use case. It is never
  selected automatically from the current working directory.
- If the ten projects require physical isolation, assign them to separate
  `CORTEX_STORAGE_INSTANCE` values. The default instance intentionally shares
  physical owner stores while retaining logical project isolation.

### Default data-root location

The default is identical conceptually on every platform:

```python
data_root = Path.home() / ".cortext-harness"
```

The account directory is discovered at runtime. A username, `/Users/...`,
`/home/...`, drive letter, or `C:\\Users\\...` path must never be hardcoded in
source, generated config, manifests, tests, or launcher scripts.

| Platform | Resolved example |
| --- | --- |
| macOS | `/Users/<current-account>/.cortext-harness` |
| Linux | `/home/<current-account>/.cortext-harness` |
| Windows | `C:\\Users\\<current-account>\\.cortext-harness` |

For any concrete machine, the displayed path is produced only after runtime
home resolution. The plan and implementation retain the symbolic
`<current-account>` form and do not record a developer account as a default.

The repository's `.cortext-harness/`, `.harness/`, and `.cache/` directories
may contain lightweight configuration, orchestration state, logs, or
rebuildable artifacts. They are not the default Qdrant/FalkorDB data root.
A project-local `.cortext-harness` directory and the home-level
`Path.home() / ".cortext-harness"` directory may therefore both exist; only
the latter owns default database data.

### Identity model

Keep physical ownership separate from logical project scope:

| Identity | Example | Responsibility |
| --- | --- | --- |
| Data root | `Path.home() / ".cortext-harness"` | Stable per-account base outside every source checkout |
| Storage schema | `v1` | Allows a future layout migration without guessing directory contents |
| Instance ID | `default`, `team-a` | One independently managed harness deployment/profile |
| Owner ID | `code`, `doc`, `custom-search` | Exclusive embedded-store owner, normally one MCP process |
| Project ID | `cortext`, `digital_key` | Logical graph/collection scope resolved by `ProjectRegistry` |

`instance_id` and `owner_id` are stable configuration values, never a PID,
port, checkout path, display label, or raw project name. They are normalized
with the same validated slug function on every platform. `project_id` does not
become a physical directory because each owner must retain cross-project
search and one predictable lock boundary.

### Canonical directory tree

```text
<data-root>/
└── v1/
    └── instances/
        └── <instance-id>/
            ├── manifest.json
            ├── qdrant/
            │   ├── code/                 # all code collections, one process owner
            │   ├── doc/                  # all document collections, one process owner
            │   └── <additional-owner>/   # optional extra MCP-owned store
            ├── falkordb/
            │   ├── code/data.rdb         # all registry-resolved code graphs
            │   ├── doc/data.rdb          # all registry-resolved document graphs
            │   └── <additional-owner>/data.rdb
            └── backups/
                └── <UTC timestamp>/
                    ├── manifest.json
                    ├── qdrant/<owner-id>/
                    └── falkordb/<owner-id>/data.rdb
```

Runtime PID files, generated launch scripts, logs, and transient health-check
data remain under the existing cache/state directory; they are not persistent
database content and do not belong under the data root.

### Path settings

The default data root is always `Path.home() / ".cortext-harness"`. A single
`CORTEX_DATA_HOME` override makes CI, portable installations, and advanced
setups deterministic. No `platformdirs`, `LOCALAPPDATA`, `APPDATA`,
`XDG_DATA_HOME`, or macOS `Library/Application Support` branch participates in
the default database path. The resolved absolute path is the public contract,
and source project roots are never candidates in default resolution.

| Setting | Derived default | Purpose |
| --- | --- | --- |
| `CORTEX_DATA_HOME` | `Path.home() / ".cortext-harness"` | Optional override; otherwise the per-account persistent-data root |
| `CORTEX_STORAGE_INSTANCE` | `default` | Selects one harness deployment/profile |
| `QDRANT_PATH` | `<root>/v1/instances/<instance>/qdrant` | Compatibility base for role paths |
| `QDRANT_CODE_PATH` | `${QDRANT_PATH}/code` | Exclusive code-owner Qdrant store |
| `QDRANT_DOC_PATH` | `${QDRANT_PATH}/doc` | Exclusive document-owner Qdrant store |
| `FALKORDB_CODE_PATH` | `<root>/v1/instances/<instance>/falkordb/code/data.rdb` | Exclusive code-owner graph store |
| `FALKORDB_DOC_PATH` | `<root>/v1/instances/<instance>/falkordb/doc/data.rdb` | Exclusive document-owner graph store |
| `FALKORDB_PATH` | Role-derived compatibility alias | Resolved only after the launcher selects an owner |
| `FALKORDB_GRAPH` | Registry-resolved graph name | Logical project graph inside the owner `.rdb` |

Explicit `QDRANT_*_PATH` and `FALKORDB_*_PATH` overrides remain available for
tests and controlled migration. Relative overrides resolve against the active
project root for backward compatibility; defaults no longer do. Configuration
precedence is CLI, active project config, environment, then the derived
instance path.

`QDRANT_URL`, `QDRANT_HOST`, `QDRANT_PORT`, `FALKORDB_URI`,
`FALKORDB_HOST`, `FALKORDB_PORT`, credentials, TLS, and browser-port settings
are removed from the canonical local contract. If transitional aliases are
accepted for one release, they must emit a clear error or deprecation message;
they must never silently start or target a network service.

### Storage-owner boundary

The embedded database directory/file is the concurrency boundary:

- `code` owns its Qdrant directory and FalkorDB file; `doc` owns different
  paths. Additional MCPs get a different validated `owner_id`.
- All projects handled by one owner use separate registry-resolved collections
  and graphs inside that owner's store.
- An ingest, reset, backfill, or validation process that needs an owner's data
  must either run while that owner MCP is stopped or submit the operation to
  the owner MCP. It must not bypass the owner and open the same path.
- Startup acquires an application lease containing instance, owner, process,
  and resolved path metadata before opening the database. A conflict fails
  immediately with the current owner and remediation steps.
- Cross-project full search is allowed within one owner store. Cross-owner
  aggregation is an MCP/service concern, not filesystem sharing.
- If arbitrary concurrent processes must access the same dataset, embedded
  mode is the wrong backend; use Qdrant/FalkorDB server mode. That optional
  backend remains outside this local-storage cutover.

### Shared client boundary

Add a single application-owned Qdrant adapter, tentatively under
`cortex_harness/storage/`, that:

- creates `QdrantClient(path=...)` instances;
- selects the instance and owner path from explicit typed configuration;
- exposes collection, query, scroll, upsert, delete, payload, and index
  operations used by current REST helpers;
- owns one client per resolved path per process, holds the owner lease, and
  closes it on shutdown;
- converts existing dictionary payloads to `qdrant_client.models` at one
  boundary;
- provides test injection so unit tests never touch real project data.

No migrated runtime module should assemble `/collections/...` or `/points/...`
URLs after this boundary lands.

### Embedded graph boundary

Retain the existing `GraphDriver` and `FalkorDBDriver` public result contract,
but construct the backend from the role-derived `FALKORDB_PATH` and select the
logical graph via `select_graph`. The code and document owners use different
`.rdb` files; each file may contain many project graphs. Remove connection
retry and Docker/port assumptions that only apply to a remote Redis server.

The user-provided example imports `FalkorDBLite` directly, while the current
official Python documentation imports `FalkorDB` from
`redislite.falkordb_client`. Implementation must pin and inspect the selected
`falkordblite` release, then use its supported public import. The application
adapter—not scattered call sites—absorbs this package-surface difference.

## Phases

1. [Phase 01 — Local storage contract and dependencies](phase-01-storage-contract-and-dependencies.md)
2. [Phase 02 — FalkorDBLite graph runtime](phase-02-falkordblite-graph-runtime.md)
3. [Phase 03 — Qdrant local client migration](phase-03-qdrant-local-client-migration.md)
4. [Phase 04 — Docker-free lifecycle and acceptance](phase-04-docker-free-lifecycle-and-acceptance.md)

## Cross-Plan Dependencies

- `neo4j-to-falkordb-migration` supplies the provider-neutral graph driver,
  FalkorDB query normalization, and `doc-tiny/graph_store.py` adapter. This plan
  changes that plan's deployment assumption from a remote `falkordb` client on
  port 6379 to `falkordblite` backed by instance/owner-scoped `.rdb` files.
  Both plan files are updated bidirectionally.
- `260728-0000-unified-ingest-query-contract` owns project/graph/collection
  naming and launcher environment parity. This plan preserves those logical
  names while replacing URL/host/port resolution with versioned machine-local
  instance/owner paths. Both plan files are updated bidirectionally; either
  implementation sequence must keep `ProjectRegistry` as the naming authority
  and must not turn `project_id` into a physical directory boundary.
- No implementation phase is blocked on either plan because the needed driver,
  registry, and launcher seams already exist in the current source. Work in
  overlapping files must be merged against their latest state.

## Expected File Areas

### Dependencies and configuration

- `pyproject.toml`, `requirements.txt`, `code-tiny/requirements.txt`,
  `doc-tiny/requirements.txt`
- `.gitignore`
- `cortex_harness/dev.py`
- `scripts/mcp_runtime_config.py`
- `code-tiny/tools/common/harness_config.py`
- `code-tiny/tools/common/project_registry.py`
- New or revised `cortex_harness/storage/config.py`, `layout.py`, `lease.py`,
  and `migration.py`
- `.cortext-harness/config/*.json` schema/examples only; do not overwrite a
  user's active configuration during implementation

### Graph storage

- `code-tiny/tools/graph/driver/falkordb_driver.py`
- `code-tiny/tools/graph/core/factory.py`
- `code-tiny/tools/graph/cli.py`
- `code-tiny/tools/graph/core/provider_runtime.py`
- `doc-tiny/graph_store.py`
- graph setup, reset, validation, and driver tests under `code-tiny/scripts/`,
  `doc-tiny/`, and `tests/`

### Vector storage

- New shared modules under `cortex_harness/storage/`
- Direct client call sites in `doc-tiny/0_reset_all.py`,
  `doc-tiny/graphrag_ingest_langextract.py`,
  `doc-tiny/graphrag_query_langextract.py`, `doc-tiny/mcp_graph_rag.py`, and
  `doc-tiny/neo4j_loader.py`
- REST-based shared paths in `code-tiny/tools/common/primary_vector_sync.py`,
  `intelligent_retrieval.py`, `incremental_cleanup.py`, `message_scan.py`, and
  `code-tiny/tools/cobol/qdrant.py`
- Analyzer-local vector writers, MCP query/list helpers, Living Docs scripts,
  `code-tiny/scripts/backfill_project_scope_keys.py`, and
  `scripts/validate_retrieval.py`

### Lifecycle and documentation

- `Makefile`, `scripts/mcp-lifecycle.py`, `scripts/mcp-lifecycle.ps1`
- `ReadMe.md`, `docs/DATABASE_INTEGRATION.md`, `docs/HARNESS_WORKFLOW.md`,
  `code-tiny/README.md`, `code-tiny/mcp/Readme.md`, `doc-tiny/Readme.md`, and
  relevant skill/environment references
- `tests/test_make_lifecycle.py`, `tests/test_dev_init_graph_provider.py`,
  `tests/test_mcp_runtime_config.py`, graph-driver tests, Qdrant contract tests,
  and end-to-end smoke tests

## Data Safety and Migration

- Never delete Docker volumes or remote data as part of this change.
- Do not pretend an existing remote Qdrant/FalkorDB dataset has been converted.
  Provide an explicit export/import or re-ingest path and label it as such.
- Create local directories lazily and atomically; fail with instance, owner,
  and resolved path when permissions or locking prevent startup.
- Keep `local_qdrant_db/` and `local_falkordb_db/` ignored as legacy migration
  sources even after defaults move outside the repository.
- Provide `storage layout`, `storage migrate-layout`, and `storage backup`
  commands. Migration is dry-run by default, requires all affected owners to
  be stopped, copies before switching, records hashes and target mappings in a
  manifest, validates reopen/query results, and never deletes the source.
- Map legacy Qdrant `code` and `doc` directories directly to the corresponding
  owner directories. For a legacy shared FalkorDB `.rdb`, inventory graphs
  first, copy the source into each required owner path, validate the expected
  graph set, and only offer pruning of non-owned graphs after a retained
  backup. Never split or move a live `.rdb` by assumption.
- Tests use temporary directories and must not read or modify the repository's
  real local database paths.

## Risks and Mitigations

| Risk | Mitigation |
| --- | --- |
| Two processes open one embedded store | Use instance/owner paths, application leases, and fail-fast ownership diagnostics |
| Cross-domain code expects a collection owned by the other role | Inventory collection ownership; route the operation to the owning MCP or graph link service rather than opening the locked path |
| A project name is reused across independent deployments | Keep project scope inside an explicit instance; never derive instance identity from project ID |
| Owner display names or ports change | Persist a stable owner ID in config; do not derive directory names from display names, ports, or PIDs |
| Legacy shared FalkorDB file contains code and doc graphs | Copy-and-verify into role files; retain the original until both stores pass acceptance |
| Qdrant REST and local APIs differ | Centralize translation and add parity tests for every used operation before deleting REST helpers |
| `falkordblite` requires Python 3.12 | Raise project/install/runtime minimum to 3.12 and add installer/doctor validation |
| FalkorDBLite package import differs from the supplied snippet | Pin a verified release and isolate its supported import behind the graph driver |
| macOS FalkorDBLite needs `libomp` | Update macOS installer and doctor diagnostics with a precise actionable check |
| Embedded storage is opened from varying working directories | Resolve paths from the active project root and test invocation from another directory |
| Remote data appears to vanish after cutover | Detect legacy URL-only configuration and stop with migration guidance; never silently create an empty replacement |

## Success Criteria

- A fresh supported Python environment installs the application without Docker
  and includes `qdrant-client` plus a pinned, verified `falkordblite` release.
- `dev init` writes an instance ID and optional data-root override and does not
  ask for database hosts, ports, credentials, TLS, or Docker settings.
- `storage layout` prints the resolved data root, instance, owners, physical
  paths, logical project targets, and current lease holder without exposing
  secrets.
- `dev doctor` validates paths, permissions, dependency imports, graph query,
  Qdrant collection round-trip, and platform prerequisites without invoking
  Docker or probing ports 6333/6379.
- Code ingest, document ingest, both MCP servers, query, reset, cleanup,
  backfill, and retrieval validation use local clients and persist across
  process restarts.
- Code and document MCP servers start together on different owner paths, while
  a duplicate owner fails before database open with an actionable message.
- At least two registered projects coexist in each code/doc owner store and
  remain isolated by graph, collection, and payload scope.
- Ten projects registered from ten unrelated source paths resolve to the same
  centralized instance/owner database paths and to ten distinct logical target
  sets. Moving one source path changes only registry metadata.
- With no override, every launcher resolves the data root from the current
  account's home directory. Exact-source validation finds no hardcoded account
  name or absolute user-home path in runtime defaults.
- Two named harness instances can run concurrently without sharing any
  physical database path.
- All active Qdrant runtime paths use the shared client adapter; an exact search
  finds no operational `/collections/...` or `/points/...` REST construction.
- All active FalkorDB runtime paths obtain the embedded backend from the
  role-derived `FALKORDB_PATH`; local startup does not require `FALKORDB_URI`,
  host, port, password, TLS, or a running Redis-compatible service.
- No lifecycle script invokes Docker, creates containers/volumes, or checks the
  Docker daemon.
- Local data directories are ignored by Git, tests use temporary paths, and an
  explicit restart test proves persistence for both stores.
- A migration test copies the current repository-local layout to the canonical
  data root, verifies collection/graph inventories, switches config only after
  validation, and leaves the source recoverable.
- The full relevant test suite passes on macOS, Windows, and Linux, or any
  platform limitation from the pinned FalkorDBLite release is reported as a
  blocking compatibility result rather than hidden.

## Sources

- Qdrant client local mode: https://github.com/qdrant/qdrant-client
- Qdrant local persistent-path locking:
  https://github.com/qdrant/qdrant-client/blob/master/qdrant_client/local/qdrant_local.py
- FalkorDBLite official Python documentation:
  https://docs.falkordb.com/operations/falkordblite/falkordblite-py.html
- FalkorDBLite Python repository: https://github.com/FalkorDB/falkordblite

## Implementation Handoff

After review, implement with:

```text
/hi-craft plans/260806-1648-local-file-storage/plan.md
```
