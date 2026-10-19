# LINK_CONTRACT.md — link-tiny doc↔code traceability

Source plan: `docs/plans/261019-1126-link-tiny-doc-code-traceability/plan.md`
(Phase 1: exact-name linking, read-only facade). This document is the
contract future plans cite when they touch link data.

## 1. Data contract

### 1.1 Storage layout

```
{instance_root}/links/{project_id}/
├── links.jsonl   # append-only link records
└── state.json    # SHA snapshot (doc+code), counts, coverage telemetry
```

`{instance_root}` is the storage instance directory (beside
`manifest.json`). The `links/` tree is deliberately **not** a storage
owner: `layout.py` validates only the declared `code`/`doc` owners and is
never modified by link-tiny.

### 1.2 Record schema (v1, `schema_version: 1`)

```json
{
  "project_id": "erp",
  "relation": "implements",
  "doc":  {"doc_id": "SPEC-auth.pdf", "paragraph_id": 12, "source_sha": "…"},
  "code": {"target_type": "symbol", "parser_type": "python",
           "path": "src/auth/login.py", "name": "login_handler",
           "lines": [88, 140], "source_sha": "…"},
  "score": 1.0,
  "provenance": "exact-name",
  "status": "active",
  "created_at": "2026-10-19T02:25:32Z"
}
```

Rules:

- **Append-only.** Records are never rewritten or removed in place. Drift
  appends a copy with `status: "stale"` + `staled_at`; deletes are logical.
  The canonical read view is *latest record per (doc ref, code ref)* —
  `link_store.latest_links`.
- **Provenance + score are mandatory** (validated at the store boundary).
  Phase-1 provenance is always `"exact-name"` with `score 1.0`; no other
  values exist yet.
- **Identity is stable, never graph-internal.** Code side:
  `(path, name, start_line, end_line, parser_type, source_sha)`; doc side:
  `(doc_id, paragraph_id, source_sha)`. `_graph_id` never appears.
- **Paths are POSIX-normalized** at the store boundary (RT-6).
- **Code SHAs** come from the code-sync inventory chain
  (`.cache/incremental_sync/*.json` → `inventory_path`); **doc SHAs** come
  from the doc sync baseline (`.cortext-harness/sync-state/*.json`
  `file_hashes`). The graphs persist no per-document SHA.
- Doc `source_id` spellings: folder-mode relpath (`docs/auth.md`) and
  single-file stem (`auth`); the engine resolves both against the baseline.

### 1.3 Stale semantics

A link's latest record is **stale** when any of these hold:

1. the code identity is absent from the current graph (deleted/renamed) —
   no auto re-match (plan risk R5);
2. the doc anchor (doc_id, paragraph_id) is absent from the current doc
   graph (deleted);
3. the doc or code file SHA changed relative to the record's SHA.

Changed sides are re-matched by recomputing the exact-name candidate set
from the graphs; unchanged pairs copy forward (delta no-op — a second run
appends nothing). `--reconcile` supersedes every existing record and
re-appends the authoritative set.

### 1.4 Coverage telemetry (the Phase-3 gate)

`state.json.coverage` reports: `active_links`, `stale_links`,
`docs_with_active_links`, `doc_coverage`, `symbols_referenced`,
`symbol_coverage`, `provenance_distribution`.

**Gate:** semantic/fuzzy matching (Qdrant code collections, threshold
≥ 0.8) is justified only when telemetry shows exact-name coverage is
materially sparse *and* noisy-free. The go/no-go decision reads this file,
not intuition.

## 2. Isolation contract

1. **One-way dependencies.** `link-tiny` imports the code-side registry
   through `registry_facade.py` (guarded try/except,
   `doc-tiny/project_contract.py` pattern) and the v2 envelope producer
   from `cortex_harness.mcp_contract` (shared harness ground). **No tree
   outside link-tiny imports link-tiny** — pinned by
   `tests/test_link_acceptance.py::test_acceptance_isolation`.
2. **Write-path / read-path split.** Only `dev sync link` (engine →
   `link_store`) writes link data. `link_mcp` and both existing servers
   never write it.
3. **Zero behavioral change** to `dev sync code`, `dev sync doc`,
   graph_mcp, mind_mcp. Registration touchpoints are additive only:
   `MCP_SERVICES` entry, `--server` choice, `sync` group,
   `_is_link_worker` + `SyncOwner` widening, lifecycle `SERVERS` +
   `requirement_files`, and one additive read-only opener in
   `code-tiny/tools/graph/read_only_opener.py` (the sanctioned RT-3
   bounded exception, pinned lease-free by
   `tests/test_read_only_opener_no_lease.py`).
4. **Lease-free reads.** The engine opens both graphs with zero
   `StorageLease` (FalkorDBLite via `_open_local_falkordb`, LadybugDB via
   `read_only=True`); no MCP pause is required for link syncs, and none is
   performed.
5. **Opt-in launch.** `dev start --server all` = code + doc only in
   Phase 1. `link_mcp` (:8790) starts with `dev start --server link`.

## 3. Tool contracts (`link_mcp`, port 8790, `/mcp`)

All three tools emit the shared v2 envelope
(`contract cortex.mcp.tool-result`, keys `contract/contractVersion/tool/
message/ok/data/error/truncation`) and resolve `project_id` through the
registry (case-insensitive; unregistered → `project_not_registered`
error envelope).

| Tool | Input | Returns |
| --- | --- | --- |
| `find_code_for_doc` | `project_id`, optional `doc_id` \| `query`, `top_k` (default 20), `min_score` | Ranked **active** code refs (+ provenance, score, filtered snippet when mind_mcp is reachable) |
| `find_doc_for_code` | `project_id`, `path` and/or `name` (identity required — no unscoped dumps), `top_k`, `min_score` | Linked doc anchors |
| `trace_status` | `project_id` | `built_at`, counts, coverage, stale count, staleness verdict |

Response rules (R2 boundary + degrade):

- Refs + score + provenance + **short snippets only** (240 chars); full
  content is fetched by the agent via `mind_mcp`/`graph_mcp`.
- Snippets pass the `.sensitive-terms` filter (link-tiny implementation:
  plain terms word-wise case-insensitive, `case:` prefix, `*` wildcards →
  `▮`) before leaving the server.
- mind_mcp/graph_mcp down → results keep refs, note `content unavailable`,
  `ok: true`. Never raise to the caller for upstream failures.
- No links built → empty `results` + note `run 'dev sync link'`.
- `state.json` older than the newest sync-state file → note
  `trace data stale — run 'dev sync link'` (staleness is annotated, never
  rebuilt at query time).
- `top_k` cap exceeded → `data.truncation` + note.

## 4. CLI contract

```
dev sync link                     # incremental build (active project)
dev sync link --project <id>      # scope one project
dev sync link --dry-run           # print would-be links, write nothing
dev sync link --reconcile         # full rebuild, supersede everything
dev sync link stop                # stop running link workers
```

Standalone by design (scope challenge #3): linking requires both sides
fresh, so it never chains into `dev sync code`/`doc` in Phase 1. The
engine runs as a worker subprocess (`link-tiny/link_worker.py`) classified
by `sync_processes._is_link_worker`, serialized per project by the
`sync-link.lock` advisory lock.
