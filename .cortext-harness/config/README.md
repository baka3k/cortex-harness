# Project Storage Configuration

Each JSON file in this directory registers one project. Local storage is the
backward-compatible default:

```json
{
  "project": {"code": "my_project", "name": "My Project"},
  "storage_backend": "local",
  "code": {"env": {"GRAPH_PROVIDER": "falkordb"}},
  "doc": {"env": {}}
}
```

Use the top-level `remote` object for a self-hosted Qdrant and/or FalkorDB
server:

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

Rules:

- `storage_backend` is `local` or `remote`; omission means `local`.
- Remote mode requires at least `qdrant_url` or `falkordb_uri`.
- A missing remote component resolves to the corresponding local owner store.
- Use this `remote` object, not legacy endpoint keys inside `code.env` or
  `doc.env`.
- Populated configs can contain plaintext secrets. Do not commit or share them.
- Switching a backend or endpoint requires source re-ingest; journals are not
  replayed across effective targets.
- `CORTEX_STORAGE_BACKEND_FORCE_LOCAL=1` is an emergency process override and
  resolves a distinct local topology.

Run `dev doctor` after changing endpoints. For localhost defaults,
`dev infra-up --provision` starts and provisions the managed containers.
