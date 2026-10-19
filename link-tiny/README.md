# link-tiny — doc↔code traceability

Third pipeline tree: links the doc graph (`{project_id}_doc`) to the code
graph (`{project_id}`) with exact-name matching, stores append-only link
records, and serves them through a read-only MCP facade.

## Components

| Module | Role |
| --- | --- |
| `registry_facade.py` | `project_id` → graph/collection/provider targets + the instance `links/` directory. Delegates to `code-tiny/tools/common/project_registry.py`. |
| `normalizers.py` | Pure matching logic: casefold, qualified-name segments, identifier-token extraction, exact-name candidate matching. |
| `link_store.py` | The only writer. Append-only `links.jsonl` + `state.json` snapshot + stale propagation + coverage stats. |
| `link_engine.py` | 5-step pipeline (delta → candidates → score → stale → write). Opens both graphs read-only, no lease. Knows nothing about MCP or CLI. |
| `mcp_server.py` | `link_mcp` on :8790. Read-only facade: `find_code_for_doc`, `find_doc_for_code`, `trace_status`. Envelope v2. Never imports the engine. |

## Runtime data

```
{instance_root}/links/{project_id}/
├── links.jsonl   # append-only link records (schema_version 1)
└── state.json    # SHA snapshot (doc+code), counts, coverage telemetry
```

## Commands

```bash
dev sync link                    # incremental link build (the active project)
dev sync link --dry-run          # print would-be links, write nothing
dev sync link --project cortext  # scope one project id
dev sync link --reconcile        # full rebuild ignoring state.json
dev start --server link          # boot link_mcp on 127.0.0.1:8790/mcp (opt-in)
```

`dev start --server all` does **not** start link_mcp in Phase 1.

## Tests

```bash
.venv/bin/python -m pytest link-tiny/tests -q
```
