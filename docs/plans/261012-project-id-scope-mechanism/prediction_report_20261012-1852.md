# Prediction Report — project_id scope mechanism for mind_mcp semantic_graph_search

- **Date**: 2026-10-12
- **Depth**: deep
- **Verdict**: **CAUTION** (0 Critical, 3 High — all with concrete mitigations)
- **Evidence base**: direct code reading of the restored tree (`doc-tiny/mcp_graph_rag.py`, `doc-tiny/project_contract.py`, `doc-tiny/doc_local_qdrant.py`, `code-tiny/tools/common/project_registry.py`, `.cortext-harness/config/dev.json`) + live reproduction against the running mind_mcp server and local Qdrant/FalkorDB. Graph-MCP index holds the *target-repo* graph (REDACTED_PROJECT), not the harness repo, so architecture context came from code reading (fallback mode, high confidence — code was read in full).

## Proposal under evaluation

Make `project_id` a **pure scope selector** with the registry as a **hint map**, per the operator's requirements:

| Requirement | Proposed semantics |
|---|---|
| id passed | Scope = that id. Registered → pinned targets from registry. Unregistered → naming-convention shard `{id}_doc`, **existence-checked before touching** (no blind probing). |
| id not passed | Scope = all: registry fan-out (every registered project's declared collections) **plus discovery** (list collections on each probed store, search every `*_doc` shard) plus the instance-store tail. |
| wrong / unknown id | **(Amended 2026-10-12 — operator decision):** never creates anything (existence-checked); id not found anywhere → **`warnings[]` + automatic fallback to search-all** in the same response. `strict_scope=true` opts back into fail-loud (LookupError) for parity/TC runs. Collection found but 0 hits for the scope filter → `ok:true` with 0 passages + `scope.warnings[]` explaining exactly that. |
| dev.json mapping | dev.json (one JSON per project under `.cortext-harness/config/`) stays the source of truth: `doc.env.QDRANT_COLLECTION_DOC` → doc collection (compat alias: `QDRANT_COLLECTION`), `doc.env.FALKORDB_GRAPH` → doc graph, fallback `{id}_doc`. **One shared parser**: doc-side `project_contract` delegates parsing to code-side `project_registry` instead of re-parsing. |
| dev.json ↔ passed id mismatch (info drift) | Read path: declared collection first; if missing/empty on all stores while the convention shard exists → **use the convention shard + `scope.warnings[]` drift notice**. Never mutate the registry on the read path. Write path: a `dev doctor` report (optional `--fix`) reconciles registry↔store drift. |
| observability | Every response carries a `scope` block: `{mode, resolved_project_id, registry_hit, collections_searched, per_collection_hits, warnings[]}` — makes a silent zero impossible. |
| prefix matching | Scoped fan-out = exact casefold only; drop the `startswith` expansion from the query contract. |
| side effects | Never query a convention graph/collection without an existence check first (FalkorDB auto-materializes on first query — the `cortext_doc` junk graph was created exactly this way). |

## Executive summary

The mechanism the operator describes is already 80% present in the restored tree; the failure they experienced came from the remaining 20%: the unscoped path fans out over **registered projects only**, and `REDACTED_PROJECT` was never registered, so queries landed on `cortext_doc` (a junk graph/collection) and returned zero silently. The proposed design closes that gap with store-side discovery, a mandatory `scope` block, and a shared registry parser — but it touches the query contract, two registries, and cross-project visibility, so it proceeds under CAUTION with the three High risks mitigated first.

## Code context (evidence package)

- `_qdrant_search_targets(project_id, collection)` (`doc-tiny/mcp_graph_rag.py:324`): registered id → pinned store; unregistered → fallback stores (remote `QDRANT_URL`, then instance store) with convention names; unscoped → registered projects + instance store tail. `LookupError` only when scoped AND no target collection exists; unscoped zero is silent.
- **Two registries parse the same config**: code-side `tools/common/project_registry.py` (doc collection key `QDRANT_COLLECTION_DOC` ✓ matches dev.json) vs doc-side `doc-tiny/project_contract.py` (`doc_env.get("QDRANT_COLLECTION")` — **ignores the key dev.json actually writes**; currently saved by the `{id}_doc` convention coincidence for `cortext`).
- `_resolve_doc_collections` exact-then-prefix (`casefold().startswith`) fan-out over registered ids; `_resolve_doc_targets` canonicalizes case variants.
- `get_document_qdrant_store(project_id=...)` already routes through the code-side registry with a doc-side error shim — the delegation precedent exists.
- Live incident: unscoped query → `collections_searched: ["cortext_doc"]` → 0; scoped `REDACTED_PROJECT` → 5 passages, scores 0.66–0.70 against real `1-2-3_店舗ブロックマスタ` design docs (1744 points in `REDACTED_PROJECT_doc`, 1271 entities in the FalkorDB graph). Junk graph `cortext_doc` (0 entities) exists in FalkorDB — materialized by mis-scoped queries.
- Kept commit `807f0675` added `FalkorDBDriver.list_graphs()` (GRAPH.LIST, side-effect-free) — the primitive the existence-check needs.

## Agreements (4+ personas align)

1. The silent zero is the bug class to eliminate; a `scope` block + structured `warnings[]` is the right primitive (all five).
2. Scoped-with-nothing-found must stay a **raised error** with a self-describing message (Architect, UX, Security, Devil's Advocate; Performance neutral).
3. dev.json remains the source of truth; the read path may fall back to the convention shard on drift but must **never write** the registry as a side effect (Architect, Security, Devil's Advocate).
4. One shared registry parser; doc-side delegating to code-side (Architect, Devil's Advocate; others no objection).
5. Existence-check before probing convention names — reuses `list_graphs()` from the kept `807f0675` (all five).

## Conflicts

| Topic | Architect | Security | Performance | UX | Devil's Advocate | Resolution (rationale) |
|---|---|---|---|---|---|---|
| Unscoped scope: discovery (`*_doc`) vs registry-only | Discovery ok; cache listings | Cross-project exposure if deployment is shared | +1 list RPC per store per query | Discovery fixes the exact trap | **Registry-only + fix operator workflow (register projects)** is simpler | **Discovery wins.** The silent-zero trap recurs every time someone ingests before registering; requiring registration moves the burden to the one step people skip. Mitigations: unscoped documented as full-visibility, `dev doctor` drift report as companion (DA's unvalidated assumption → CAUTION). |
| Found-but-empty: error vs ok+warnings | Structured warnings | — | — | Warnings; only nothing-found raises | n8n-style clients prefer `ok:true` shapes | **Split by situation**: nothing-found → raise; found-but-empty → `ok:true` + `warnings[]`. Rationale: distinct failure causes need distinct signals. |
| Prefix (`startswith`) fan-out | Drop — surprising semantics | Drop — prefix collisions leak projects | Fewer targets | Must be documented if kept | Someone may depend on it | **Drop for query scope** (exact casefold only). Grep tests/usages first; rationale: silent multi-project answers are worse than an explicit re-query. |
| Registry drift fix: read-path auto-heal vs doctor-only | Read-path fallback + warn; no silent writes | No auto-mutation of config | — | The drift must be *visible* | Who actually runs the doctor? | **Read-path fallback + warnings; writes only in `dev doctor`.** Rationale: self-healing config writes from a read path are unreproducible. |

## Risk summary

| Risk | Severity | Persona | Mitigation |
|---|---|---|---|
| R1 — Registry env-key split-brain: doc-side parser ignores `QDRANT_COLLECTION_DOC`, code-side reads it → the two sides can target different collections for the same id, silently | **High** | Architect | Shared parser (doc-side delegates to code-side registry); compat alias reads both keys; contract test asserting both registries resolve identical `ProjectTargets` on fixture configs |
| R2 — Silent zero recurs for every out-of-band ingest (the REDACTED_PROJECT incident) | **High** | UX | Discovery fan-out over `*_doc` shards; `scope` block with `per_collection_hits`; `warnings[]` for found-but-empty; `LookupError` unchanged for scoped nothing-found |
| R3 — Convention-name probing materializes junk graphs/collections (`cortext_doc` evidence) — db-rác regression vector | **High (mitigated)** | Security | Existence check via `GRAPH.LIST` / `list_collection_names` before any convention-name touch; wrong/typo'd ids never create anything, they raise |
| R4 — Cross-project data exposure via unscoped discovery or prefix collisions | Medium | Security | Unscoped documented as full-visibility/admin operation; prefix fan-out dropped; per-collection attribution in `scope`; deployment already binds 127.0.0.1 |
| R5 — Unscoped latency grows with store × collection count | Medium | Performance | Short-TTL listing cache (registry mtime-keyed); sequential fan-out with a cap + warning when exceeded; single query encode (already the case) |
| R6 — Malformed/foreign JSON in config dir silently skipped by the registry | Low | UX | `warnings[]` entry + `dev doctor` report |

## Per-persona detail

```yaml
architect:
  concerns:
    - "Two registry modules parse one config format and already disagree on the doc-collection env key (QDRANT_COLLECTION vs QDRANT_COLLECTION_DOC) — the drift is live, not hypothetical"
    - "Discovery couples the query layer to store listing capabilities; RemoteQdrantStore and the instance store both expose list_collection_names but semantics differ (remote = server truth, instance = path store)"
    - "LookupError carries storage topology in its message; it is the right fail-loud vehicle but must be built from resolution state, not ad-hoc strings"
  recommendations:
    - "Doc-side project_contract delegates entry parsing/target resolution to code-side project_registry (precedent: get_document_qdrant_store already does this with an error shim)"
    - "Introduce ScopeResolution as a first-class result object consumed by both the Qdrant and graph candidate builders; scope block in the response is its serialization"
    - "Keep the convention shard reachable but existence-gated; document it as part of the contract (docs/PROJECT_ID_QUERY_RULES.md)"
  confidence: high

security:
  threats:
    - "Unscoped discovery returns cross-project passages — on a shared deployment one client sees all teams' shards"
    - "Prefix (startswith) fan-out silently widens scope on id collisions ('bank' → 'bank_android')"
    - "Convention-name probing materializes junk graphs on typo'd ids (auto-materialize on first GRAPH.QUERY) — silent storage-side-effect channel"
  severity: medium   # deployment binds 127.0.0.1; no remote multi-tenant exposure today
  mitigations:
    - "Document unscoped as full-visibility; automated flows (TC/parity) must pass project_id"
    - "Drop prefix fan-out from the query contract"
    - "Existence-check (GRAPH.LIST) before touching any convention graph; nothing-found raises instead of probing"
    - "scope block attributes every passage to its collection/project so contamination is visible in the response"

performance:
  bottlenecks:
    - "Unscoped fan-out = N projects × M collections sequential searches + 1 list RPC per store"
    - "Registry file re-read and re-parsed per request (both registries)"
  metrics_impact: "unscoped +1 list RPC per store (~5–20ms remote, sub-ms local, cacheable); scoped path unchanged; single query encode already amortized by the process-wide embedder"
  alternatives:
    - "Short-TTL cache for listings and registry entries keyed by file mtime"
    - "Cap the fan-out (e.g. 12 targets) and set warnings[] when truncated instead of fanning out unbounded"
    - "A single merged all-docs collection was rejected: it reintroduces the model-marker/project-filter coupling this design removes"

ux:
  issues:
    - "Today's zero answers carry no signal about which collections were searched or why — the exact confusion reported (cortext_doc vs REDACTED_PROJECT_doc)"
    - "Error vs empty distinction is invisible to MCP clients unless the response shape separates them"
  edge_cases:
    - "Case/whitespace variants ('REDACTED_PROJECT ', 'REDACTED_PROJECT') — normalize strip+casefold and canonicalize to the registered form (already implemented in _resolve_doc_targets; keep for the convention path)"
    - "collection param overrides names on every probed store — keep, and surface it in scope.mode so overrides are explicit"
    - "Blank string vs null project_id must behave identically (unscoped)"
  a11y_concerns: []

devils_advocate:
  assumptions_challenged:
    - "Discovery (unscoped = all *_doc) assumes operators want breadth by default; the simpler alternative is registry-only plus fixing the workflow (register REDACTED_PROJECT) — rejected because the trap recurs at every unregistered ingest, but this assumption is only validated by shipping the doctor companion"
    - "Verified-in-code: the dual-registry key drift is real (doc-side reads QDRANT_COLLECTION; dev.json writes QDRANT_COLLECTION_DOC; code-side reads the latter) — this alone can silently target the wrong collection for any project with a non-convention collection name"
    - "The user's premise that the DB lacks 店舗ブロックマスタ was itself wrong — the corpus contains it (source 1-2-3_店舗ブロックマスタ_02_画面動作・データ処理.xlsx). Ground truth for parity expected outputs can be built from real data today using project_id=REDACTED_PROJECT"
  simpler_alternatives:
    - "Do nothing structurally: document 'always pass project_id' and register projects — rejected as the mechanism (but shipping it as the short-term operating rule costs nothing)"
  worst_case: "Unscoped discovery over a shared remote Qdrant mixes shards from several projects into one answer; a TC expected-output is authored from contaminated context and the parity gate fails inexplicably — mitigated by per-collection attribution in scope and the documented rule that automated flows pass project_id"
```

## Recommendations (numbered, with rationale)

1. **Add the `scope` block to every response first** (mode, resolved id, registry hit, collections searched, per-collection hits, warnings). Cheapest change, immediately kills the silent-zero class, and unblocks the pending parity/TC work on truthful data.
2. **Unify the registry parser** — doc-side delegates to code-side `project_registry`; read both doc-collection env keys during migration (`QDRANT_COLLECTION_DOC` primary, `QDRANT_COLLECTION` compat); add a contract test asserting both code paths resolve identical targets on fixture configs. Kills R1.
3. **Existence-gate every convention-name probe** — FalkorDB: `GRAPH.LIST` via the kept `list_graphs()`; Qdrant: `collection_exists`/`list_collection_names`. Nothing-found raises; nothing is ever materialized by a query. Kills R3 and honors the db-rác fixes.
4. **Unscoped = registry fan-out + `*_doc` discovery + instance-store tail**, with per-collection counts and a fan-out cap that sets a warning when truncated. Fixes R2 for the REDACTED_PROJECT case without requiring registration.
5. **Keep `LookupError` for scoped nothing-found**, enriching the message with known registered ids and the collections each probed store actually has; **found-but-empty → `ok:true` + `warnings[]`**.
6. **`dev doctor`** (report; optional `--fix`): unregistered shards, declared-but-absent collections, empty junk graphs/collections, malformed config files. The write-side companion that keeps discovery honest.
7. **Drop prefix fan-out from the query contract** after grepping tests/usages; exact casefold only, canonicalized to the registered id.
8. **Update the contract docs** (`docs/PROJECT_ID_QUERY_RULES.md`, `docs/UNIFIED_INGEST_QUERY_CONTRACT.md`) so the rules and the code stop drifting apart — the doc-side env-key drift is exactly this failure mode.

## Next steps (verdict: CAUTION)

1. Address the three High mitigations in order: shared parser (R1) → existence gating (R3) → discovery + scope block (R2).
2. Short-term operating rule until shipped: always pass `project_id` (verified working against live data today) and run the REDACTED_PROJECT registration when convenient.
3. Then run `hi-plan` for the implementation, phased: (a) scope block + warnings, (b) registry unification + contract tests, (c) existence gating, (d) unscoped discovery + cap, (e) `dev doctor`, (f) docs sweep.
4. Restart both running `mcp_graph_rag.py` servers after any of this lands — they currently run pre-restore code (document-expansion features that no longer exist in the tree).

## Amendments (2026-10-12, operator feedback — supersede conflicting text above)

### A1. Many projects, many directories, many servers — registry is per-directory, stores are shared

Operator reality: each project lives in its own directory with its **own** `.cortext-harness/config/dev.json`, and MCP servers are started from those different directories. Consequences for the design:

- `_default_config_dir()` walks up from the server's CWD, so **each server only sees its own directory's registry** — the registry is a *local hint map*, not a global one. This is accepted as-is (it also preserves per-project collection-name overrides).
- The **stores (Qdrant server / FalkorDB) are shared across all servers** (same `QDRANT_URL` / FalkorDB endpoints). Therefore "search all" MUST be discovery-driven (list what actually exists on the shared stores), exactly as proposed — registry-only fan-out would silently return different results depending on which directory the server was started in. With discovery, unscoped results are **directory-independent**: every server sees the same shards.
- Rule 1 (scoped id) is unaffected: a passed id resolves against the local registry first, then the convention shard on the shared stores — both reachable from any directory.
- `CORTEX_HARNESS_CONFIG_PATH` remains the existing escape hatch to point one server at a central registry directory if a global view is ever wanted; `dev doctor` gains one report: shards discovered on shared stores but absent from *this* directory's registry (expected cross-directory state, surfaced as info, not drift).

### A2. Wrong id → warn + fall back to search-all (operator decision)

Rule 3 is amended: a passed `project_id` that exists **nowhere** (no registry entry, no convention shard on any probed store) must:

1. create nothing (existence-check still applies — the junk-prevention rule is unchanged),
2. record a **warning** in `scope.warnings[]` (`"project 'xyz' not found in registry or stores; fell back to all projects"`), and
3. **degrade to unscoped search-all** and return those results in the same response (`ok:true`).

A hard failure would break interactive operator flows; the contamination risk this introduces for automated flows is handled by: the warning + per-collection attribution in `scope` (always present), and a new `strict_scope` boolean parameter (default `false`) that restores fail-loud `LookupError` for parity/TC runs where a wrong id must abort instead of silently widening scope.

The previous "raise LookupError for scoped nothing-found" recommendation and the corresponding conflicts-table resolution are superseded by this amendment; the found-but-empty warning behavior is unchanged.
