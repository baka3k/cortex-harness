---
title: "Docker-Free Local Qdrant and FalkorDBLite Storage"
status: pending
created: 2026-08-06
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
project-local persistent storage:

- Qdrant uses `qdrant-client` local mode with data under
  `./local_qdrant_db`.
- FalkorDB uses the `falkordblite` package with a configurable `.rdb` file,
  defaulting to `./local_falkordb_db/cortex.rdb`.
- `dev`, Make, MCP launchers, analyzers, document ingestion, reset/validation
  scripts, and tests start without Docker and without services listening on
  ports 6333 or 6379.
- Project and collection naming semantics remain unchanged; only the physical
  storage transport changes.

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

## Target Storage Contract

### Paths

All paths are resolved against the active project root, never the caller's
incidental current working directory.

| Setting | Default | Purpose |
| --- | --- | --- |
| `QDRANT_PATH` | `./local_qdrant_db` | Required base directory requested for local vector data |
| `QDRANT_CODE_PATH` | `${QDRANT_PATH}/code` | Code MCP/analyzer collections; avoids cross-process lock contention |
| `QDRANT_DOC_PATH` | `${QDRANT_PATH}/doc` | Document MCP/ingest collections; avoids cross-process lock contention |
| `FALKORDB_PATH` | `./local_falkordb_db/cortex.rdb` | Embedded graph database file |
| `FALKORDB_GRAPH` | Existing registry-resolved graph name | Logical graph name inside the `.rdb` file |

`QDRANT_URL`, `QDRANT_HOST`, `QDRANT_PORT`, `FALKORDB_URI`,
`FALKORDB_HOST`, `FALKORDB_PORT`, credentials, TLS, and browser-port settings
are removed from the canonical local contract. If transitional aliases are
accepted for one release, they must emit a clear error or deprecation message;
they must never silently start or target a network service.

### Shared client boundary

Add a single application-owned Qdrant adapter, tentatively under
`cortex_harness/storage/`, that:

- creates `QdrantClient(path=...)` instances;
- selects the code or document path from an explicit storage role;
- exposes collection, query, scroll, upsert, delete, payload, and index
  operations used by current REST helpers;
- owns one client per resolved path per process and closes it on shutdown;
- converts existing dictionary payloads to `qdrant_client.models` at one
  boundary;
- provides test injection so unit tests never touch real project data.

No migrated runtime module should assemble `/collections/...` or `/points/...`
URLs after this boundary lands.

### Embedded graph boundary

Retain the existing `GraphDriver` and `FalkorDBDriver` public result contract,
but construct the backend from `FALKORDB_PATH` and select the logical graph via
`select_graph`. Remove connection retry and Docker/port assumptions that only
apply to a remote Redis server.

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
  port 6379 to `falkordblite` backed by an `.rdb` file. Both plan files are
  updated bidirectionally.
- `260728-0000-unified-ingest-query-contract` owns project/graph/collection
  naming and launcher environment parity. This plan preserves those logical
  names while replacing URL/host/port resolution with role-specific local
  paths. Both plan files are updated bidirectionally; either implementation
  sequence must keep `ProjectRegistry` as the naming authority.
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
- Create local directories lazily and atomically; fail with the resolved path
  when permissions or locking prevent startup.
- Add `local_qdrant_db/` and `local_falkordb_db/` to `.gitignore` before any
  local database is created.
- Tests use temporary directories and must not read or modify the repository's
  real local database paths.

## Risks and Mitigations

| Risk | Mitigation |
| --- | --- |
| Two MCP processes open one Qdrant local path | Use role-specific subdirectories and add a simultaneous code+doc MCP startup test |
| Cross-domain code expects a collection owned by the other role | Inventory collection ownership; route the operation to the owning MCP or graph link service rather than opening the locked path |
| Qdrant REST and local APIs differ | Centralize translation and add parity tests for every used operation before deleting REST helpers |
| `falkordblite` requires Python 3.12 | Raise project/install/runtime minimum to 3.12 and add installer/doctor validation |
| FalkorDBLite package import differs from the supplied snippet | Pin a verified release and isolate its supported import behind the graph driver |
| macOS FalkorDBLite needs `libomp` | Update macOS installer and doctor diagnostics with a precise actionable check |
| Embedded storage is opened from varying working directories | Resolve paths from the active project root and test invocation from another directory |
| Remote data appears to vanish after cutover | Detect legacy URL-only configuration and stop with migration guidance; never silently create an empty replacement |

## Success Criteria

- A fresh supported Python environment installs the application without Docker
  and includes `qdrant-client` plus a pinned, verified `falkordblite` release.
- `dev init` writes local path configuration and does not ask for database
  hosts, ports, credentials, TLS, or Docker settings.
- `dev doctor` validates paths, permissions, dependency imports, graph query,
  Qdrant collection round-trip, and platform prerequisites without invoking
  Docker or probing ports 6333/6379.
- Code ingest, document ingest, both MCP servers, query, reset, cleanup,
  backfill, and retrieval validation use local clients and persist across
  process restarts.
- Code and document MCP servers start together without Qdrant lock failures.
- All active Qdrant runtime paths use the shared client adapter; an exact search
  finds no operational `/collections/...` or `/points/...` REST construction.
- All active FalkorDB runtime paths obtain the embedded backend from
  `FALKORDB_PATH`; local startup does not require `FALKORDB_URI`, host, port,
  password, TLS, or a running Redis-compatible service.
- No lifecycle script invokes Docker, creates containers/volumes, or checks the
  Docker daemon.
- Local data directories are ignored by Git, tests use temporary paths, and an
  explicit restart test proves persistence for both stores.
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
