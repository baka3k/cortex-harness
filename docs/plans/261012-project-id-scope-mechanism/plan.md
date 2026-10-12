# Plan: project_id scope mechanism for mind_mcp (semantic_graph_search)

- **Status:** planned
- **Created:** 2026-10-12
- **Mode:** hi-plan --full (1 researcher, spawn mode)
- **Inputs:**
  - Prediction report (5-persona, CAUTION): `prediction_report_20261012-1852.md` (this dir)
  - Research digest (spawn researcher, 74 tool calls): `research/research-digest.md` (this dir)
  - Operator amendments (2026-10-12, supersede conflicting report text):
    - A1: many projects / many `dev.json` / many server start dirs → registry stays a **per-directory hint map**; unscoped search-all MUST be discovery-driven on the shared stores (directory-independent results).
    - A2: unknown `project_id` → **no junk creation + warning + automatic fallback to search-all**; new `strict_scope=true` param restores fail-loud for parity/TC runs.
- **BlockedBy:** none (cross-plan scan: only active plan `260910-1400-csharp-roslyn-upgrade` — no file overlap; `261011-hybrid-ab-gate` / `261011-colbert-multivector` touch `embed_runtime`/`dev.py`, soft overlap at most, not blocking)
- **Delegation receipts:**
  - `[delegate] role=researcher mode=spawn scope=project_id scope surface (doctor/tests/prefix/storage/envelope/params/contract) status=done`

## Goal

Make `project_id` a pure scope selector for the doc-side MCP query path: correct id → correct shard; missing id → discovery-based search-all; wrong id → warn + fallback (or fail under `strict_scope`); never create junk; every response explains its own scope. All operator-visible facts from the REDACTED_PROJECT incident (query hit `cortext_doc` junk instead of the data shard) become impossible or self-explaining.

## Non-goals (deferred, documented)

- **Prefix/LIKE rule removal (R4)** — research shows ~10 code-side call sites + 4 Cypher `STARTS WITH` predicates + 4 breaking test files and a contracted rule. Deferred to its own future plan. This plan only makes resolved candidates visible via `scope`.
- `dev doctor --fix` auto-repair (report-only first).
- `scope` blocks on other mind tools (follow-up once the shape settles here).

## Contract changes (must be reflected in docs — Phase 06)

| Rule | Change |
|---|---|
| R1 unscoped = search-all | now discovery-based (`*_doc` shards listed from stores) + fan-out cap |
| R6 read-side fallback | existence-gated; unknown id → warn + fallback_all; `strict_scope=true` → error |
| Registry mirror | doc-side `project_contract` becomes a delegating facade over code-side `project_registry` |

## Phases

| Phase | File | Delivers | Size |
|---|---|---|---|
| 01 | [phase-01-scope-block.md](phase-01-scope-block.md) | `scope` block + `warnings[]` + `strict_scope` param (fallback_all mode) | M |
| 02 | [phase-02-registry-facade.md](phase-02-registry-facade.md) | registry facade delegation + dual-key fix + agreement test | M |
| 03 | [phase-03-existence-gating.md](phase-03-existence-gating.md) | anti-junk existence gates for convention shards | M |
| 04 | [phase-04-discovery-unscoped.md](phase-04-discovery-unscoped.md) | discovery-based unscoped search-all + per-collection hits + cap | L |
| 05 | [phase-05-doctor-drift.md](phase-05-doctor-drift.md) | doctor drift report (warn-only) in mcp-lifecycle | M |
| 06 | [phase-06-docs-sweep.md](phase-06-docs-sweep.md) | contract docs update + session log | S |

Dependency order: 01 → 03 → 04 (existence gating feeds fallback semantics); 02 independent (can parallel 01); 05 after 03 (reuses gates); 06 last.

## Shared conventions (from research-digest.md — follow verbatim)

- Response additions go **inside `data`**; the `{ok,data,error}` envelope is immutable (`docs/mcp-output-contract.md:81-84`).
- `collections_searched` keeps its current meaning (requested names, `mcp-output-contract.md:341` no-silent-semantic-change); new facts go in `scope.per_collection_hits`.
- Tool params: typed signature + `_coerce_bool` in-body (`mcp_graph_rag.py:116-128`).
- FalkorDB existence = `GRAPH.LIST` only (`FalkorDBDriver.list_graphs`, `falkordb_driver.py:627`); never a Cypher probe.
- Doctor checks: `doctor_check(..., required=False)` warn-only; keep `mcp-lifecycle.ps1` noted as divergent (win32) in output.
- Test invocation: single pytest root covers both trees (`pyproject.toml:40-42`); CI has no doc-tiny job → every phase's test scope lists the local merge-gate set (`docs/mcp-output-contract.md:363-370`) + touched files.

## Red-team notes (self, disclosed — --full optional red-team folded here)

- **Cache poisoning**: `_qdrant_stores`/`_graph_drivers` (`mcp_graph_rag.py:110-111`) are process-lifetime; existence gating (Phase 03) must not create a driver/store for an absent shard, else the junk is created *by us*.
- **mode mapping**: `_qdrant_search_targets` branches map 1:1 to scope modes (`:338-355`) — reuse, don't re-derive.
- **Explicit `collection=` path unchanged**: fallback_all only applies to `project_id` resolution, never to an explicit `collection` override (that path already raises `LookupError` when absent — keep).
- **Falling back must not mask the collection=+project_id combined case**: if `collection` is explicit, mode=collection and no fallback.
- **Windows**: doctor drift report is Python-only for now; ps1 twin divergence is surfaced, not silently ignored (Phase 05 prints a note when run on win32 — actually the ps1 runs instead; document in Phase 06 docs).
- **Negative-existence vs later ingest**: an unregistered shard created *after* server start must still be discovered (Phase 04 lists live each call — keep listing uncached; only store *handles* are cached).

## Open questions (validate — do not block implementation)

1. Doctor drift report: report-only confirmed for this plan; `--fix` backlog.
2. Fan-out cap value: propose `MAX_SCOPE_TARGETS = 12` (no existing precedent, `unknown` in digest) — constant + env override.
3. Should `semantic_search` (passage-only tool) share the same scope block? Propose: yes in a follow-up, not this plan.
