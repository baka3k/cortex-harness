# Local Database Integration

Cortex Harness uses embedded, persistent storage by default:

- Qdrant runs through `qdrant-client==1.18.0` local mode.
- FalkorDB runs through `falkordblite==0.10.0` and an owner-specific `.rdb` file.
- Python 3.12 or newer is required.
- No database container, daemon, host, port, credential, or TLS setting is part of the local contract.

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

Remote endpoint keys such as `QDRANT_URL`, `QDRANT_HOST`, `FALKORDB_URI`, and `FALKORDB_HOST` are legacy configuration. Remove them before local startup and explicitly export/re-ingest remote data; Cortex Harness will not silently substitute an empty local store.

## Initialize and inspect

```bash
make build
make storage-init
make storage-layout
make doctor
```

Equivalent global commands are `dev storage-init`, `dev storage-layout`, and `dev doctor`.

`storage-init` creates the versioned tree and an idempotent manifest. `storage-layout` reports resolved paths and current lease metadata. `doctor` performs isolated temporary-store round trips and does not write probe collections or graphs into production owner stores.

## Concurrency

Qdrant local directories and FalkorDBLite files are single-owner resources. Code and document MCPs use distinct paths so they can run simultaneously. A second process must not directly open an owner path while its MCP is running.

Before ingest, reset, migration, or backup that directly opens an owner store:

```bash
dev stop
```

If arbitrary concurrent clients are required, use an explicitly designed remote backend outside this local-runtime contract.

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
- **Remote-only configuration rejected:** export or re-ingest the remote dataset, remove endpoint/credential fields, and run `storage-init` again.
