# Phase 01 — Remote FalkorDB plumbing through the sync chain

## Objective

Let `dev sync code` honor `storage_backend: "remote"` + `remote.falkordb_uri`
end-to-end: dev.py args → incremental_sync → analyzers → driver, without
touching the local-embedded default.

## Changes

### 1. `cortex_harness/storage/config.py` — `storage_overlay`

- When `resolved.backend_mode == REMOTE` and `resolved.remote.falkordb_uri`:
  - emit `FALKORDB_URI` (normalized to `redis://`/`falkors://` form the driver
    accepts), plus `FALKORDB_SSL` / `FALKORDB_PASSWORD` when set;
  - DO NOT emit `FALKORDB_PATH` / `FALKORDB_CODE_PATH` / `FALKORDB_DOC_PATH`
    (children must not fall back to embedded).
- When `resolved.remote.qdrant_url` is set: emit `QDRANT_URL` (+`QDRANT_API_KEY`);
  otherwise keep the local Qdrant path overlay unchanged.
- Local mode: byte-identical output to today (guard with an explicit
  `backend_mode` check so no local key set changes).

### 2. `cortex_harness/dev.py`

- `_storage_env_for_process`: build the resolve_storage config dict from
  `cfg["code"|"doc"].env` PLUS top-level `storage_backend` and `remote` section
  so `backend_mode`/`remote_config` survive resolution.
- `_neo4j_args_code` and `_env_to_neo4j_args`: when `env["FALKORDB_URI"]` is
  present → emit `--falkordb-uri` (+ `--falkordb-password`, `--falkordb-ssl`
  when set) and skip `--falkordb-path`; otherwise unchanged.
- `_pause_mcp_for_sync`: when `FALKORDB_PATH` is absent (remote graph), skip the
  MCP pause + embedded-stop dance entirely (a server handles concurrency) —
  `yield` straight through. Keep local-mode logic identical.

### 3. `code-tiny/tools/sync/incremental_sync.py`

- `parse_args`: add `--falkordb-uri`, `--falkordb-password`, `--falkordb-ssl`.
- FalkorDB config builders (`_query_impacted_files`,
  `_project_topology_bootstrap_needed`, `_resume_configured_journal`, and the
  main writer-config site): include `uri`/`password`/`ssl` and set
  `path=None` when `args.falkordb_uri` is present; path branch untouched.
- `_build_analyzer_env`: propagate `FALKORDB_URI`/`FALKORDB_PASSWORD`/
  `FALKORDB_SSL` env when set; do not set `FALKORDB_PATH`.

### 4. `code-tiny/tools/graph/cli.py` + `core/factory.py`

- `prepare_graph_args`: if `args.falkordb_uri` or env `FALKORDB_URI` is set →
  skip the local-path synthesis (`resolve_storage(...).falkordb_code_path`)
  and the `FALKORDB_PATH` fallback; still resolve graph/db names as today.
- `create_graph_driver_from_args`: pass `uri`/`password`/`ssl` config to the
  factory when present (and `path=None`), with `_suppress_deprecation=True`
  so remote is not flagged as deprecated network usage.
- `GraphDriverFactory.create_from_env` (FALKORDB branch): prefer
  `FALKORDB_URI` over path synthesis; only fall back to
  `resolve_storage(...).falkordb_code_path` when neither URI nor path is set.

### 5. `code-tiny/tools/common/harness_config.py`

- `apply_harness_config`: pass top-level `storage_backend`/`remote` from
  dev.json into its `resolve_storage` call so child-side overlays agree with
  dev.py (defense in depth for directly-invoked analyzers).

## Contract

```text
local  (macOS today):  --falkordb-path <rdb>          → embedded falkordblite
remote (Windows):      --falkordb-uri redis://127.0.0.1:6379 → falkordb client
```

`FALKORDB_PATH` and `FALKORDB_URI` are mutually exclusive at every layer; when
both could exist, URI wins only in remote mode (local mode never sets URI).

## Verification

- Unit tests (Phase 04) assert arg/env construction for both modes.
- On Windows, `dev sync code --dry-run` prints `--falkordb-uri` (after Phase 03
  config), and no `--falkordb-path` anywhere in the child command line.
- On macOS CI/locally: existing local-mode tests unchanged.
