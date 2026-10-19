# link-tiny — doc↔code traceability

Third pipeline tree: links the doc graph (`{project_id}_doc`) to the code
graph (`{project_id}`) with exact-name matching, stores append-only link
records, and serves them through a read-only MCP facade.

## Usage — 3 steps

Link matching reads **both** graphs, so it always runs *after* a fresh
`dev sync code` **and** `dev sync doc`. It never chains automatically
into either sync (Phase-1 decision): running it at either sync's moment
would see a half-fresh world.

### 1. Sync both sides

```bash
dev sync code
dev sync doc
```

### 2. Build links

```bash
dev sync link                    # incremental build (the active project)
dev sync link --dry-run          # print would-be links, write nothing
dev sync link --project cortext  # scope one project id
dev sync link --reconcile        # full rebuild, supersede all existing links
dev sync link stop               # stop a running link worker
```

Only exact name matches link (`login_handler` = `loginHandler`, score
1.0, provenance `exact-name`); a name appearing in N files yields N links
— nothing is auto-selected. Symbol labels cover functions, types,
properties, variables **and designer `Field` controls**; label reads are
paginated, so the graph server's `resultset_size` never silently drops
symbols (`state.json["symbol_universe_by_label"]` accounts every label —
a round-10000 label there is the regression signature of a broken read).
Records live in
`{instance}/links/{project_id}/links.jsonl` (append-only) with a
`state.json` snapshot carrying counts + coverage + sha-join telemetry
(`sha_join`: per-record doc/code SHA match, missing and ambiguous
counters).

### 3. Query via link_mcp (:8790)

```bash
dev start --server link          # opt-in — see note below
```

| Tool | Asks |
| --- | --- |
| `find_code_for_doc(project_id, doc_id)` | Which code implements this document? |
| `find_doc_for_code(project_id, path, name)` | Which documents reference this symbol? |
| `trace_status(project_id)` | built_at, counts, coverage, stale verdict |

Results carry refs + score + a short filtered snippet; fetch full
paragraph/symbol content through `mind_mcp` / `graph_mcp` as usual.

> `dev start` / `dev start --server all` do **not** start link_mcp in
> Phase 1 — it is opt-in via `--server link`.

## Stale behavior

Link data never rebuilds at query time. When code/docs changed after the
last `dev sync link`, tools annotate results with
`trace data stale — run 'dev sync link'` (`trace_status` reports
`stale: true`); superseded links are appended as `status: "stale"`, never
deleted.

**Content edits are detected.** Records carry the doc/code `source_sha`
joined from the sync baselines at build time (doc ids resolve through the
`__`-flattened spelling dev.py writes into the graph; code paths join via
the graph's `repo` + folder-relative path — see `LINK_CONTRACT.md`
§1.5). When a file's SHA changes in the baseline, its links go stale and
fresh copies are re-appended on the next run — deletions *and* edits
stale-propagate.

## Components

| Module | Role |
| --- | --- |
| `registry_facade.py` | `project_id` → graph/collection/provider targets + the instance `links/` directory. Delegates to `code-tiny/tools/common/project_registry.py`. |
| `normalizers.py` | Pure matching logic: casefold, qualified-name segments, identifier-token extraction, exact-name candidate matching. |
| `link_store.py` | The only writer. Append-only `links.jsonl` + `state.json` snapshot + stale propagation + coverage stats. |
| `link_engine.py` | 5-step pipeline (delta → candidates → score → stale → write). Opens both graphs read-only, no lease. Knows nothing about MCP or CLI. |
| `mcp_server.py` | `link_mcp` on :8790. Read-only facade: `find_code_for_doc`, `find_doc_for_code`, `trace_status`. Envelope v2. Never imports the engine. |

Full contract (record schema, identity rules, isolation, Phase-3 gate):
`docs/LINK_CONTRACT.md`.

## Tests

```bash
.venv/bin/python -m pytest link-tiny/tests -q
```
