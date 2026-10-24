# LINK_CONTRACT.md — link-tiny doc↔code traceability

Source plan: `docs/plans/261019-1126-link-tiny-doc-code-traceability/plan.md`
(Phase 1: exact-name linking, read-only facade). This document is the
contract future plans cite when they touch link data.

## 1. Data contract

### 1.1 Storage layout

```
{instance_root}/links/{project_id}/
├── links.db      # SQLite WAL indexed store (docs, paragraphs, symbols, links)
├── links.jsonl   # append-only link records (compacted automatically on oversized --reconcile)
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

- **Append-only + SQLite WAL indexed.** Records are appended to `links.jsonl`
  and upserted into the co-located `links.db` SQLite WAL database in the same
  lock scope. Drift appends a copy with `status: "stale"` + `staled_at`;
  deletes are logical. The canonical read view is *latest record per
  (doc ref, code ref)* — `link_store.latest_links` (and B-Tree indexed
  `links.db` tables `docs`, `paragraphs`, `symbols`, `links` for `< 10ms`
  MCP queries).
- **Provenance + score are mandatory** (validated at the store boundary).
  Provenance is `"exact-name"` with `score = 1.0` for low-DF symbols
  (`file_df <= 2`) and IDF-weighted `(0, 1.0]` with file-affinity bonus for
  higher-DF symbols (`file_df > 2`). High-DF symbols (`file_df > 8`) require
  file-context co-occurrence affinity so generic UI/framework identifiers do
  not cause combinatorial explosion.
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

### 1.5 SHA join (how records get their `source_sha`)

Record SHAs never come from the graphs — they are joined from the sync
baselines at build time. The two sides spell paths differently, and the
join contract below is the only sanctioned mapping. Evidence in this
section is measured on the live deployment (probe:
`link-tiny/tests/probe_sha_namespaces.py`, 2026-10-19).

**Doc side.** The doc graph's `Paragraph.source_id` is dev.py's
`_safe_source_id` spelling: the POSIX rel path with **both separators
replaced by `__`** (`a/b.md` → `a__b.md`; single-file mode uses the bare
stem). The sync-state baseline holds POSIX rel paths. Lookup order for a
`doc_id`, resolved against an index built once per build:

1. direct: baseline key == the `doc_id` (POSIX-normalized);
2. flattened: `flatten_doc_rel(key) == doc_id` (probe hit-rate on the live project:
   67/67 source_ids);
3. stem: unique baseline file whose stem equals the `doc_id`
   (single-file mode). An ambiguous flattened id or stem — several
   baseline keys collapsing to one id with *different* SHAs — resolves
   to **nothing**; a wrong document's SHA must never be guessed.

`doc_id` is **never un-flattened**: file names may legitimately contain
`__`, so the flattened index is built from baseline keys, not by
splitting ids.

**Code side.** Graph symbols carry `file_path`/`source_file` as
**folder-relative** paths plus a `repo` property; vb/csharp parsers stamp
`repo` with the absolute scan-folder path (probe: every symbol label on
the live project). Framework endpoint labels (`HttpEndpoint`, `Route`, …) carry
`repo = null`. Inventories hold **folder-relative** keys per scan folder.
The join resolves `(repo, path)` against a repo-aware index:

1. **`(repo, path)` exact** — the node's `repo` normalizes to a known
   scan folder (resolved absolute POSIX, casefolded on lookup only) and
   that folder's map contains `path` exactly;
2. **a known-folder `repo` pin is authoritative**: if the pinned
   folder's map lacks `path`, the result is `missing` — another folder's
   same-spelled file is a *different file*, never a borrowed SHA (this
   is the 1-collision/152-shadow data again);
3. **unique cross-folder fallback** — only when `repo` is null/foreign
   (endpoint labels; parsers that stamp the project root) and *exactly
   one* known folder contains `path` as a folder-relative key. With a
   flat project-relative map, the same rule applies over
   component-boundary suffixes (a key whose project-relative form ends
   with `/<path>`);
4. otherwise `source_sha = null` (`missing`), and when more than one
   folder matches, `null` with the `ambiguous` outcome.

The join resolves **once per graph identity** and the write and
drift-detection paths share that result — duplicate identities (same
path/name/lines stamped in two scan folders) must not get different
SHAs on the two paths, or identical builds would oscillate and grow the
append-only log forever.

**Naive suffix matching is forbidden.** Live the live s1+s3 scan folders: 1 exact
folder-relative collision (`src/shared/_shared_widget.vb` exists
in both scan folders) and 152 cross-folder suffix shadows — a
"any key that ends with the graph path" rule would assign wrong SHAs
there. Ambiguity always degrades to `null`, never to a guess.

**Telemetry.** `state.json["sha_join"]` and `BuildReport.sha_join`
report, counted per fresh record:
`{doc_matched, doc_missing, code_matched, code_missing, code_ambiguous}`.
Every `missing`/`ambiguous` counter above zero has a known cause (file
absent from the inventory, collision between scan folders, unreadable
baseline); unexplained nulls in records are a bug, not a norm.

### 1.6 Full symbol universe (pagination invariant + label list)

The engine reads every symbol label — and the doc side's `Paragraph` +
`HAS_ENTITY` reads — with **deterministic keyset pagination**
(`ORDER BY id(...) SKIP/LIMIT`, batch 5000): a server's `resultset_size`
(observed FalkorDB default 10000) caps a single query's result set,
which silently truncated unpaginated reads — on the live project, Function
(50437), Variable (38619) and Field (27941) each yielded exactly 10000
rows, so ~78% of symbols never entered the match index. Paged reads stay
under the cap per query. A doc-side silent cut would be worse than
under-matching — missing paragraphs would stale their own links — so
doc reads carry the same invariant.

Invariants (Phase-03 gate):

- **rows read == the server's count per read**; each row enters the
  index exactly once (internal ids dedup across batch boundaries);
- a shortfall is a **loud warning** on stderr — never a silent cut. The
  engine never writes server config (`GRAPH.CONFIG SET` is out of
  contract);
- a read that **collapses to zero** against a non-empty prior build
  aborts the build instead of stale-marking every link (destructive-read
  guard; `--reconcile` states operator intent and bypasses it);
- `state.json["symbol_universe_by_label"]` carries
  `{label: {rows_read, expected, kept}}` per label (an unreadable label
  stays visible with `error: 1`, it never vanishes);
  `symbol_universe` (= Σ `kept`) is the matched-index size and
  `symbol_universe_expected` (= Σ server counts) is the completeness
  gate — they are equal on clean reads, and any gap is explained per
  label. A universe showing a round 10000 signature is the regression
  signature of a broken read.

Symbol labels linked (identity column `file_path`): `Function`, `Class`,
`Type`, `Interface`, `Enum`, `Constant`, `Variable`, `Property`,
**`Field`** (designer controls — the artifacts screen/business-rule
documents actually name). Endpoint labels (identity `source_file`, no
line numbers): `ApiEndpoint`, `HttpEndpoint`, `Route`,
`ControllerAction`, `ServletEndpoint`. Designer Field names can be
generic (`label1`); links still require a doc to name the token exactly
and the normalizers' stoplist / minimum-length rules apply unchanged —
no engine-side filtering beyond exact-name (pair growth is measured, not
guessed).

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
