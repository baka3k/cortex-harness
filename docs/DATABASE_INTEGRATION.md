# Database Integration

Cortex Harness uses embedded, persistent storage by default and can select
self-hosted remote storage per project:

- Qdrant runs through `qdrant-client==1.18.0` local mode.
- FalkorDB runs through `falkordblite==0.10.0` and an owner-specific `.rdb` file.
- Python 3.12 or newer is required.
- Remote mode uses a Qdrant server URL and/or FalkorDB server URI with optional
  credentials and TLS.

## Storage identity

Physical ownership and logical project scope are intentionally separate:

| Identity | Purpose |
| --- | --- |
| Data root | Stable account-level application data; defaults to `Path.home() / ".cortext-harness"` |
| Schema | Versioned physical layout; currently `v1` |
| Instance | Independent deployment/profile, such as `default` or `team-a` |
| Owner | Exclusive embedded-store process, normally `code` or `doc` |
| Project | Registry-resolved graph/collection/payload scope inside an owner store |

Ten projects may therefore share one code owner and one document owner while retaining distinct graph and collection names. Moving a source checkout updates registry metadata; it does not move database files.

## Canonical paths

```text
~/.cortext-harness/
└── v1/instances/<instance>/
    ├── manifest.json
    ├── qdrant/
    │   ├── code/
    │   └── doc/
    ├── falkordb/
    │   ├── code/data.rdb
    │   └── doc/data.rdb
    └── backups/<UTC timestamp>/
```

The account home is resolved at runtime. Do not hardcode `/Users/...`, `/home/...`, or `C:\\Users\\...` in configuration.

Supported overrides:

```bash
export CORTEX_DATA_HOME=/path/to/portable-data
export CORTEX_STORAGE_INSTANCE=team-a
```

Launchers derive `QDRANT_CODE_PATH`, `QDRANT_DOC_PATH`, `FALKORDB_CODE_PATH`, and `FALKORDB_DOC_PATH`. `FALKORDB_PATH` is the selected owner's compatibility alias. Relative explicit path overrides are resolved against the active project root; defaults never are.

Environment keys such as `QDRANT_URL`, `QDRANT_HOST`, `FALKORDB_URI`, and
`FALKORDB_HOST` are legacy project configuration. Put endpoints in the
top-level `remote` object instead; launchers derive the necessary process
environment from that validated config.

## Per-project backend selection

Omitting `storage_backend` selects local storage. Remote mode is explicit:

```json
{
  "project": {"code": "my_project", "name": "My Project"},
  "storage_backend": "remote",
  "remote": {
    "qdrant_url": "http://127.0.0.1:6333",
    "qdrant_api_key": null,
    "falkordb_uri": "redis://127.0.0.1:6379",
    "falkordb_password": null,
    "falkordb_ssl": false
  },
  "code": {"env": {"GRAPH_PROVIDER": "falkordb"}},
  "doc": {"env": {}}
}
```

At least one remote endpoint is required. A missing component endpoint is an
explicit mixed topology: that component resolves to its owner-specific local
path before a run begins. A connection, authentication, or timeout failure
after resolution never triggers local fallback.

The storage factory exposes credential-free effective graph and vector target
descriptors. Their fingerprints include the effective mode, canonical
path/endpoint, graph or collection, owner role, and TLS state. Journal runs
therefore cannot resume across local/remote switches, endpoint changes, graph
changes, mixed-mode changes, or `CORTEX_STORAGE_BACKEND_FORCE_LOCAL=1`.

Credentials may be stored by `dev init` in the local project config. Keep
populated `.cortext-harness/config/*.json` files out of shared repositories.
Descriptors and logs contain only sanitized endpoints and one-way tenancy
fingerprints, never raw credentials or URI userinfo.

## Initialize and inspect

```bash
make build
make storage-init
make storage-layout
make doctor
```

Equivalent global commands are `dev storage-init`, `dev storage-layout`, and `dev doctor`.

`storage-init` creates the versioned tree and an idempotent manifest.
`storage-layout` reports resolved paths and current lease metadata. `doctor`
performs isolated local round trips and probes configured remote projects.
`dev infra-up --provision` can start and provision the default localhost
Qdrant/FalkorDB containers.

## Concurrency

Qdrant local directories and FalkorDBLite files are single-owner resources. Code and document MCPs use distinct paths so they can run simultaneously. A second process must not directly open an owner path while its MCP is running.

Before ingest, reset, migration, or backup that directly opens an owner store:

```bash
dev stop
```

Remote servers own their concurrency and do not use local `StorageLease`
files. The local graph-write journal remains an owner-local SQLite spool; it is
not moved into the server or shared over NFS.

## Switching backends

Backend migration is re-ingest based:

1. Stop active ingest/MCP owners for the project.
2. Change `storage_backend` and the `remote` object.
3. Run `dev doctor` and fix any connectivity errors.
4. Re-ingest the project from source.

Do not replay an incomplete journal onto the new target and do not dual-write.
The old target remains untouched until the operator removes it separately.
`CORTEX_STORAGE_BACKEND_FORCE_LOCAL=1` is an emergency override; it resolves a
new local topology and cannot resume or publish work created for the remote
topology.

## Legacy repository-local migration

The previous layout is recognized beneath a chosen legacy root:

```text
local_qdrant_db/{code,doc}/
local_falkordb_db/cortex.rdb
```

Preview first, then apply:

```bash
dev storage-migrate-layout --legacy-root /path/to/cortex-harness
dev storage-migrate-layout --legacy-root /path/to/cortex-harness --apply
```

Migration copies and hashes content, refuses to merge divergent targets, and leaves all source files intact. Existing remote services and volumes are not modified; export/re-ingest them separately.

## Backup

Stop the selected owner, then run:

```bash
dev storage-backup --owner code
dev storage-backup --owner doc
```

Each backup is written under the instance `backups/` directory with a manifest and SHA-256 verification. Keep the original owner data until restore validation succeeds.

## Troubleshooting

- **Unsupported Python:** install Python 3.12+ and rerun `make build`.
- **FalkorDBLite import failure on macOS:** install the platform OpenMP runtime, rebuild the virtual environment, and rerun `make doctor`.
- **Owner lease conflict:** stop the process named in the lease diagnostic or select another `CORTEX_STORAGE_INSTANCE`.
- **Permission failure:** choose a writable `CORTEX_DATA_HOME`; do not run the application with elevated privileges merely to access another account's data.
- **Remote config rejected:** set `storage_backend` to `remote`, add a `remote`
  object, and provide at least `qdrant_url` or `falkordb_uri`.
- **Remote backend unavailable:** verify DNS, port, TLS, and credentials with
  `dev doctor`; the runtime deliberately does not fall back to local storage.
- **Backend changed:** re-ingest from source. Existing journal/generation
  fingerprints are intentionally incompatible with the new target.
