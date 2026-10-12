# Phase 06 — Docs sweep + session log

## Objective

The contract docs stop describing the old behavior — the doc-side env-key drift proved "docs say X, code reads Y" is itself a failure mode. Sweep every doc the redesign touches, then log the session per repo convention.

## Changes

1. **`docs/PROJECT_ID_QUERY_RULES.md`** — the canonical rules doc; apply the three CONTRACT-CHANGEs from plan.md:
   - R1: unscoped = registry fan-out **+ live `*_doc` discovery on each probed store** + instance-store tail; results are directory-independent (amendment A1 rationale: many `dev.json`, many start dirs, shared stores).
   - R6 (read side): unknown id → warn + `fallback_all` (default) or `project_not_registered` error (`strict_scope=true`); convention shards are existence-gated (`GRAPH.LIST` / `collection_exists`) before any touch — a query can never materialize a shard.
   - Registry: doc-side `project_contract` = delegating facade over `project_registry`; doc-collection env keys `QDRANT_COLLECTION_DOC` (primary) / `QDRANT_COLLECTION` (legacy).
2. **`docs/UNIFIED_INGEST_QUERY_CONTRACT.md`** — update the read-path sections (query rules, `scope` block field spec) and the env-var table (`:204-214`) to the dual-key reality.
3. **`docs/PROJECT_REGISTRY.md`** — dual-key precedence, facade delegation, per-directory registry semantics (CWD walk-up + `CORTEX_HARNESS_CONFIG_PATH` escape hatch), cross-directory expectation ("shard visible but not in *this* directory's registry" is normal, `dev doctor` reports it).
4. **`docs/mcp-output-contract.md`** — new `scope` block + `warnings` field spec inside `data` (field table + one example); note `collections_searched` meaning is unchanged.
5. **Session log**: `docs/logs/2026-10-12-project-id-scope-mechanism.md` — context (REDACTED_PROJECT incident: mis-scoped query → `cortext_doc` junk → silent zero), decisions (amendments A1/A2, prefix deferral, doctor report-only), Impact/Decision/References per `hi-log` house format; add index entry.
6. **Ops notes** (inside the log + plan dir README is fine): restart both running `mcp_graph_rag.py` servers after landing; `strict_scope=true` recommended for parity/TC automation.

## Acceptance criteria

- AC1: no doc states the old unscoped rule ("registered projects only") or the single-key env reality; grep for `QDRANT_COLLECTION_DOC` in docs shows the dual-key explanation.
- AC2: `scope` block fields all documented with types + one worked example (scoped / fallback_all).
- AC3: session log exists, indexed, contains no sensitive tokens (repo redaction rule: customer project names never appear in repo text — use `REDACTED_PROJECT`).

## Test scope

- Docs-only + log: no code tests. Verification = grep gates (AC1, AC3) + link check for the index entry.
- **Size: S**
