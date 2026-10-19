---
title: "link-tiny: doc↔code traceability — exact-name linking + read-only link_mcp facade (Phase 1)"
status: pending
created: 2026-10-19
mode: hi-plan --full
scope: "new link-tiny/ tree (registry facade, normalizers, link engine, link store, FastMCP facade :8790), 'dev sync link' CLI group, links/{project_id} instance data layout, cross-tree acceptance tests; zero behavioral changes to code-tiny/doc-tiny pipelines"
blockedBy: []
blocks: []
relatedPlans:
  - 260728-0000-unified-ingest-query-contract (naming contract + ProjectRegistry — link-tiny reuses resolve_project_targets as the only join key)
  - 260828-1508-multi-instance-fanout-default (lease semantics — link engine opens both graphs read-only, no writer lease)
  - 261013-0000-mcp-single-channel-json-contract (envelope v2 — link_mcp emits the same tool-result envelope)
  - 260916-ladybugdb-graph-provider (provider-neutral read surface for embedded graphs)
  - 260807-0929-mcp-ingest-query-concurrency (link worker obeys per-instance locks; no overlap on ingest write path)
  - 260719-0100-mcp-query-capability-hardening (tool-result contract conventions)
prediction: prediction_report_20261019_1015.md (hi-predict verdict: CAUTION — mitigations R1 drift, R2 injection, R3 noise are binding constraints on this plan)
---

# link-tiny: doc↔code traceability — exact-name linking + read-only link_mcp facade

## Overview

CortexHarness maintains two disjoint knowledge stores per project: the code
graph `{project_id}` (functions, modules, endpoints; Qdrant
`{project}_{hash}__{lang}_functions`) and the doc graph `{project_id}_doc`
(`Document`/`Paragraph`/`Entity` — digest §1; the `Chunk` label exists only
in an index registry and is never written; Qdrant `{project_id}_doc`). The
only shared key is `project_id` via `ProjectRegistry`. No edge, link, or tool connects a
document to the code that implements it — agents must guess symbol names from
prose and query both servers manually.

This plan adds a **third pipeline tree, `link-tiny/`**, parallel to
`code-tiny/` and `doc-tiny/`, that:

1. **Builds links** — `dev sync link` reads both graphs read-only, matches
   doc entity names / identifier-like tokens against code symbol names and
   route paths (exact-name matching, Phase 1), and writes an append-only
   `links.jsonl` + a `state.json` snapshot per project under
   `instances/{instance}/links/{project_id}/`.
2. **Serves links** — a read-only FastMCP facade `link_mcp` on port 8790
   exposes `find_code_for_doc`, `find_doc_for_code`, and `trace_status`,
   delegating content retrieval to the existing `graph_mcp`/`mind_mcp`
   servers over HTTP.

Isolation contract (from the approved hi-predict design):

- **Dependency direction is one-way**: `link-tiny` imports the code-side
  registry through a facade (the `doc-tiny/project_contract.py` pattern) and
  reads storage; **no existing tree imports link-tiny**.
- **Write-path / read-path split**: only `dev sync link` writes; `link_mcp`
  and both existing servers never write link data.
- **Zero behavioral change** to `dev sync code`, `dev sync doc`, and both
  existing MCP servers. Touchpoints in old files are limited to two
  registrations (dev.py CLI/server table, sync_processes worker classifier)
  plus additive tests/docs.
- **Envelope + scoping reuse**: link_mcp emits `cortex.mcp.tool-result` v2
  and scopes every call through `resolve_project_targets`.

## Scope challenge decisions

### 1. Does Phase 1 include semantic/fuzzy matching?

**Decision: No — exact-name only.** Doc `Entity` names and identifier-like
tokens extracted from `Chunk` text are matched against code symbol names,
qualified names, and route paths with casefold + qualified-segment
normalization. Rationale: hi-predict flagged low-threshold fuzzy links as
risk R3 (garbage floods results) and Devil's Advocate warned exact coverage
may be sparse — so Phase 1 ships with **coverage telemetry** in `state.json`
(% docs with ≥1 link, % symbols referenced, provenance distribution). The
Phase-3 decision (fuzzy via Qdrant code collections, threshold ≥ 0.8) is
gated on this telemetry, not guessed now.

### 2. Does the link engine read graphs via MCP or directly?

**Decision: directly, read-only, no writer lease.** Opening graphs through
the MCP servers would make `dev sync link` depend on servers running — the
wrong direction (sync runs in CI/offline; servers may be down). This mirrors
the sibling-store read pattern from plan `260828-1508`: readers open without
acquiring the writer's `StorageLease`. At query time the facade inverts the
direction: it reads `links.jsonl` from disk (cheap, local) and delegates
content retrieval over HTTP to `graph_mcp`/`mind_mcp`.

### 3. Does `dev sync link` hook automatically into `dev sync code` / `dev sync doc`?

**Decision: No — standalone command in Phase 1; opt-in chaining later.**
Linking requires both sides to be fresh, so hooking into either sync alone
links at the wrong time; auto-running in both doubles the work and expands
the regression surface of two battle-tested commands. Degradation is
explicit: `link_mcp` tools detect a stale `state.json` and annotate results
`"trace data stale — run 'dev sync link'"` instead of building at query time.

## Architecture invariants

1. **Join key** — `project_id` through `resolve_project_targets` only. No new
   identity mechanism; link-tiny must work for any registered project
   without config additions.
2. **Stable identity** — links reference code by
   `(path, name, start_line, end_line, parser_type, source_sha)` and docs by
   `(doc_id, paragraph_id, source_sha)`. Never graph-internal ids
   (`_graph_id`) — they change on reindex.
3. **Append-only** — `links.jsonl` records are never rewritten; drift marks
   `status: "stale"` and appends. Deletes are logical.
4. **Provenance is mandatory** — every record carries `provenance`
   (`exact-name` in Phase 1) and `score` (`1.0`). Consumers (and the R2
   injection mitigation) depend on this being present from day one.
5. **Untrusted content boundary (R2)** — link_mcp returns references +
   score + short snippets only; full document/paragraph content is fetched
   by the agent via `mind_mcp` tools. Snippets pass the `.sensitive-terms`
   filter before leaving the server.
6. **Manifest untouched** — `links/` lives under the instance directory but
   outside `manifest.json` owner validation (`layout.py` validates only the
   declared `code`/`doc` owners; unknown subdirectories are invisible to it).

## Deliverable layout

```
cortex-harness/link-tiny/
├── CLAUDE.md                  # tree rules: engine≠server split, one-way deps, append-only
├── README.md
├── requirements.txt           # fastmcp only
├── registry_facade.py         # delegates to code-tiny/tools/common/project_registry.py
├── normalizers.py             # casefold, qualified-name segments, identifier token rules
├── link_store.py              # links.jsonl append-only + state.json, stale propagation
├── link_engine.py             # delta → candidates → score → stale → write; reads graphs
├── mcp_server.py              # link_mcp :8790 — read-only facade, envelope v2
└── tests/
    ├── test_normalizers.py
    ├── test_link_store.py
    ├── test_link_engine.py
    └── test_mcp_tools.py
```

Runtime data:

```
~/.cortext-harness/v1/instances/{instance}/links/{project_id}/
├── links.jsonl                # append-only link records
└── state.json                 # SHA snapshot (doc+code), counts, coverage telemetry
```

`links.jsonl` record (v1):

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

Registration touchpoints in existing files (complete list):

| File | Change | Nature |
| --- | --- | --- |
| `cortex_harness/dev.py` | `link` entry in `MCP_SERVICES` (~196–215), `--server` Choice extended with `link` at :2762 (default `all` unchanged = code+doc), `@sync.group("link")` | additive |
| `cortex_harness/sync_processes.py` | `_is_link_worker()` + `SyncOwner` widening so `dev stop` manages the link worker | additive |
| `scripts/mcp-lifecycle.py` | `SERVERS` entry for the link server (:68–81, startswith-matched) | additive |
| `link-tiny/mcp.sh` | launch script consumed by mcp-lifecycle (pattern: `code-tiny/mcp.sh`) | new file |
| `tests/` | `test_link_acceptance.py` | additive |
| `docs/` | `LINK_CONTRACT.md` + acceptance-matrix row | docs only |
| `layout.py`, `doc-tiny/**` | **no changes** | — |

**Bounded exception (red-team RT-3):** if the FalkorDB driver exposes no way
to open a store with *zero* primary lease (digest §4 flags that no existing
call site reads two stores lease-free), one additive read-only opener is
allowed in `code-tiny/tools/graph/` (new function, no behavior change to
existing call sites), pinned by a regression test mirroring
`tests/test_mcp_sibling_no_lease.py`. Anything beyond a single additive
helper reverts to plan review.

## Phases

| Phase | File | Deliverable |
| --- | --- | --- |
| 01 | [phase-01-scaffold-and-facade.md](phase-01-scaffold-and-facade.md) | link-tiny scaffold, registry facade, normalizers |
| 02 | [phase-02-link-store.md](phase-02-link-store.md) | append-only store + state.json + stale propagation |
| 03 | [phase-03-link-engine.md](phase-03-link-engine.md) | read-only graph reads + exact-name pipeline + coverage telemetry |
| 04 | [phase-04-cli-sync-link.md](phase-04-cli-sync-link.md) | `dev sync link` group + worker classifier + dry-run/reconcile |
| 05 | [phase-05-link-mcp.md](phase-05-link-mcp.md) | link_mcp :8790, 3 tools, envelope v2, degrade behaviors |
| 06 | [phase-06-acceptance-and-docs.md](phase-06-acceptance-and-docs.md) | cross-tree acceptance tests + LINK_CONTRACT.md + matrix row |

## Research anchors

From `reports/research-digest.md` (spawn researcher, 2026-10-19):

- **Doc graph labels** (`doc-tiny/graphrag_ingest_langextract.py:421-502, 559-622`):
  `Document{id, name, project_id(_normalized)}`,
  `Paragraph{source_id, paragraph_id, text, short, …}`,
  `Entity{id=uuid5(proj::type::name_norm), name, type, name_norm}`.
  No per-document SHA inside the doc graph.
- **Doc/code SHAs** live in the sync baseline
  `{project_dir}/.cortext-harness/sync-state/{md5(folder)}.json`
  (`file_hashes`, dev.py:895-912, 1198-1203); incremental state chain:
  `.cache/incremental_sync/*.json` → `.cache/inventories/{snapshot_id}.json`
  (per-file size/mtime_ns/sha256) → `.cache/incremental_sync_manifests/…`
  (`{files:[...]}`, no SHA). Delta reads state→inventory, newest token.
- **Code symbols** (`code-tiny/tools/graph/schema/ladybug_schema.py:112-208`,
  `language_writer.py:1352-1390`): `Function/Class…{id, name,
  qualified_name, file_path, start_line, end_line, language,
  project_id(_normalized)}`; endpoints are framework labels
  (`ApiEndpoint`, `HttpEndpoint`, `Route`, `ControllerAction`) wired via
  `EXPOSES_API`/`HANDLED_BY`.
- **Lease semantics**: primary store opened exclusive (`LOCK_EX`);
  siblings via `additional_paths` read-only, no lease (contract pinned by
  `tests/test_mcp_sibling_no_lease.py`). Open risk: no existing call site
  opens two stores with zero primary lease → RT-3 bounded exception.
- **Envelope v2**: single producer `cortex_harness/mcp_contract.py:result_envelope_json`
  (keys contract/contractVersion/tool/message/ok/data/error/truncation).
  link-tiny imports it — the harness package is shared ground, not a
  sibling tree. Tool-registration copy template: doc server's
  `_standard_tool` decorator (`doc-tiny/mcp_graph_rag.py:1372-1433`).
- **Cross-tree import**: sys.path insert + try/except guard
  (`doc-tiny/project_contract.py:47-54`) — the registry_facade pattern.
- **Tests**: unittest/pytest + sys.path injection + direct envelope calls;
  no in-memory FastMCP client exists anywhere.
- **`.sensitive-terms`** has no Python consumer today (external commit
  guard only); snippet filtering is new code in link-tiny.
- **Packaging**: only root `cortex_harness` is packaged; per-tree
  `requirements.txt` files are consumed by `requirement_files()`
  (`scripts/mcp-lifecycle.py:293`) — link-tiny's requirements file is
  picked up automatically. Existing tree `CLAUDE.md` files are empty
  stubs; link-tiny's will be substantive.

## Red team (inline L3, disclosed)

| # | Finding | Decision |
| --- | --- | --- |
| RT-1 | Plan drafted against `Chunk` label; graph never writes it | Corrected to `Paragraph` everywhere (digest §1) |
| RT-2 | Doc-side delta cannot come from graph properties (no SHA stored) | Delta reads sync-state baseline + inventories (digest §3); phase-03 updated |
| RT-3 | Engine reading both graphs lease-free has no precedent; opening one as primary takes `LOCK_EX` and can deadlock against the owning MCP server | Bounded exception added to touchpoints: one additive read-only opener in `code-tiny/tools/graph`, pinned by a no-lease regression test; engine opens both stores through it |
| RT-4 | `mcp-lifecycle.py` matches servers by `startswith` — a bare `"link"` token is safe but entry order and the lifecycle script pair (`mcp.sh`) must be added or `dev start --server link` silently no-ops | phase-05 includes `SERVERS` entry + `link-tiny/mcp.sh`; smoke test boots via the lifecycle path |
| RT-5 | Ambiguity explosion: tokens like `init`/`setup` match hundreds of symbols | normalizer stoplist (common verbs + language keywords, min token length) — phase-03 |
| RT-6 | Windows/POSIX path divergence would split link identity | paths normalized to POSIX form at record creation — phase-02/03 |
| RT-7 | Envelope duplication risk (copy-paste drift from v2 contract) | resolved: import `result_envelope_json` from `cortex_harness.mcp_contract` |

## Validation (inline, disclosed)

- **Assumption: exact-name coverage is useful alone.** Not assumed —
  measured. Phase-1 telemetry + the Phase-3 gate are contractual
  (LINK_CONTRACT.md).
- **Assumption: port 8790 is free.** `dev doctor` reports port
  diagnostics; wiring the link port into doctor is an optional additive
  follow-up, not a Phase-1 blocker (documented open question).
- **Assumption: users remember the third command.** Accepted for Phase 1;
  opt-in chaining (`dev sync doc --link`) is the documented Phase-2
  follow-up. Stale annotations in link_mcp make forgetting visible, not
  fatal.
- **Assumption: refs-not-bodies is the right response shape.** Validated by
  hi-predict security persona (R2) — provenance + score + filtered snippet
  only.

## Receipts

```
[delegate] role=researcher mode=spawn scope=link-tiny technical anchors (schemas, SHAs, leases, envelope, registration, tests, conventions) status=done
```
