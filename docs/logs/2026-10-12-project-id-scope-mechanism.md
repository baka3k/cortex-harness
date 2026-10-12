# project_id scope mechanism for mind_mcp — 2026-10-12

## Context

Plan `docs/plans/261012-project-id-scope-mechanism/` (mode `hi-plan --full`,
executed via `hi-craft --full` same day). Driver: the REDACTED_PROJECT
incident — a mis-typed `project_id` on `semantic_graph_search` resolved
through the naming-convention fallback, the first query **materialized** a
junk FalkorDB graph + Qdrant collection (`cortext_doc`-style names) on the
shared stores, and the query then silently returned zero results. Two
operator amendments scoped the fix (supersede conflicting report text):

- **A1** — many projects / many `dev.json` / many server start dirs: the
  registry stays a **per-directory hint map**; unscoped search-all must be
  **discovery-driven** on the shared stores (directory-independent results).
- **A2** — unknown `project_id`: **no junk creation**, warn, and automatic
  fallback to search-all; a new `strict_scope=true` param restores
  fail-loud for parity/TC runs.

Non-goals (documented deferrals): prefix/LIKE rule removal (R4 — ~10
code-side call sites + 4 Cypher `STARTS WITH` predicates + contracted
rule), `dev doctor --fix` auto-repair (report-only first), `scope` block on
`semantic_search` (follow-up once the shape settles).

## Change

- **Scope block (Phase 01)** — `doc-tiny/mcp_graph_rag.py`:
  `qdrant_search_entity_payload` now returns `(rows, scope_info)`; the
  scope dict (mode `scoped`/`collection`/`unscoped`/`fallback_all`,
  `requested/resolved_project_id`, `registry_hit`,
  `collections_searched` — meaning unchanged, `collections_present`,
  `per_collection_hits`, `discovered_collections`,
  `targets_total/searched`, `graph_present`, `warnings[]`) is computed
  where probing happens and threaded through `_qdrant_search_targets`.
  `semantic_graph_search` exposes `data.scope` + top-level
  `data.warnings` (envelope `{ok,data,error}` untouched); passages now
  carry per-passage `collection` attribution. New `strict_scope: bool =
  False` tool param (typed signature + `_coerce_bool` in-body): unknown id
  + nothing exists → `project_not_registered` error; default stays
  warn-and-fallback. Found-but-empty scoped queries (collection present,
  0 hits under the project filter) emit a warning naming the collection so
  they are distinguishable from missing shards.
- **Registry facade + dual-key fix (Phase 02)** — the live drift where
  `dev.json` writes `doc.env.QDRANT_COLLECTION_DOC` and **neither**
  resolver read it is dead: `project_registry._doc_qdrant()` (and the
  doc-side standalone fallback) now read `QDRANT_COLLECTION_DOC` first,
  legacy `QDRANT_COLLECTION` second, naming rule last. Doc-side
  `project_contract.py` is a **delegating facade** over the code-side
  registry: it keeps its own config parser (`_read_project_entries` — the
  patchable registry source, which several existing tests patch) and its
  exact/prefix candidate matching, but matched entries resolve through
  `project_registry._resolve_targets`, so naming/key-precedence rules live
  in exactly one place. Facade resolution is registered-only (never
  env-seeds ad-hoc projects — read paths rely on `ProjectNotRegisteredError`
  as the convention-fallback signal). Both trees return equal
  `ProjectTargets`; all tests that patch doc-side symbols pass unmodified.
- **Existence gating (Phase 03)** — an unregistered id's convention shard
  is touched only after a side-effect-free existence check: FalkorDB via
  `GRAPH.LIST` (`list_graphs`; never a Cypher probe — the first
  GRAPH.QUERY would materialize the junk), Qdrant via the existing listing
  filter. `_graph_store_candidates` returns `[]` for a provably-absent
  convention graph (no `for_graph`, no Cypher, no driver cached); the
  combined verdict (no registry entry + no listed convention collection +
  graph gate not proving existence) feeds the Phase 01 fallback/strict
  behavior. Gate failures are fail-open (warning, never a false strict
  error). Listings are read live per call; negative results are never
  cached.
- **Discovery-based unscoped (Phase 04)** — search-all now means
  "everything the shared stores actually hold": registry pins first, then
  per-store live listing of `*_doc` shards (one target pair per shard, so
  the cap is shard-exact), then the instance-store tail (None semantics
  for non-convention collections; per-store `covered` bookkeeping prevents
  double search/hit-counting). Fan-out cap `MAX_SCOPE_TARGETS = 12`
  (env `SCOPE_MAX_TARGETS`), truncation always reported in
  `scope.warnings`. Graph side mirrors discovery (`GRAPH.LIST`-derived
  `*_doc` graphs, deduped against pins, same cap). Results are
  directory-independent (A1), pinned by a two-config-dir test.
- **Doctor drift report (Phase 05)** — `scripts/mcp-lifecycle.py` gains
  `doctor_scope_drift_checks()` (warn-only, `required=False`, wired into
  `invoke_doctor` after the remote checks): unregistered non-empty
  `*_doc` shards (with point/entity counts), declared-but-absent targets,
  empty junk shards (copy-pasteable drop hints, never executed), malformed
  config JSON (previously silently skipped by `_scan_project_backends`),
  and an informational note for legacy-key-only registry entries. Local
  embedded stores are listed only when their data dir exists (Qdrant) /
  an owner process is already running (FalkorDB — constructing an embedded
  driver on an idle store would start a server). The report never writes.
  Windows: `dev doctor` runs the PowerShell twin on win32, which has no
  drift group — documented divergence.
- **Docs sweep (Phase 06)** — `PROJECT_ID_QUERY_RULES.md` (R1 discovery
  semantics, new R6a existence gating + strict_scope, facade note,
  checklist), `UNIFIED_INGEST_QUERY_CONTRACT.md` (query precedence,
  registry facade, dual-key launcher note), `PROJECT_REGISTRY.md`
  (dual-key precedence, facade delegation, per-directory semantics),
  `mcp-output-contract.md` (`scope`/`warnings` field spec + worked
  fallback_all example).
- **Tests** — new: `tests/test_semantic_graph_scope_block.py` (AC1–AC6),
  `tests/test_registry_facade_agreement.py` (dual-key + cross-tree
  agreement incl. standalone fallback + malformed configs),
  `tests/test_scope_existence_gating.py` (no-creation guarantees, live
  negative re-check, gate-outage fail-open under strict, registered path
  untouched), `tests/test_unscoped_discovery.py` (directory independence,
  live discovery, cap, attribution, unreachable store, pinned filters,
  pin-store/tail distinct-object regression), `tests/test_doctor_scope_drift.py`
  (four warn classes, backend-bound declared-absent, clean-state ok line,
  read-only store assertions, force-local bypass). Updated in place:
  `tests/test_unified_contract_doc_paths.py` — tuple return of
  `qdrant_search_entity_payload` (`_patch_mind_query_backends` + 3 direct
  call sites) and `test_mind_qdrant_reports_missing_scoped_collection` now
  pins a **registered** id (the LookupError-on-missing contract it tests;
  the unregistered+absent case is now `fallback_all` per A2 and is covered
  by the new scope-block tests).
- **Review round (full mode, spawn reviewer, code lens — 1 fix cycle)** —
  four findings fixed: (1, High) `per_collection_hits` double-counted when
  the registry pin and the fallback tail were distinct objects over the
  same physical store — store identity is now stable (`url`/`path`-keyed
  listing cache and `covered` bookkeeping, `_store_key`); (2, Medium) the
  doctor's declared-but-absent check compared every project against every
  store (false alarms for local projects on remote backends) — each
  project is now bound to its own backend inventory; (3, Medium)
  `strict_scope` raised on a graph-gate outage (`graph_present=None`) —
  strict now raises only on definitive absence, gate outages fail open to
  `fallback_all` + warning; (4, Medium) an unrelated pre-existing
  `.gitignore` working-tree edit was excluded from this change. Doctor
  lease conflicts on locally-owned stores degrade to a partial-coverage
  note instead of aborting the group; the malformed-config message now
  says "not a valid JSON config".

## Impact

- A mis-typed `project_id` can no longer create junk shards from the query
  path, and every `semantic_graph_search` response explains its own scope
  (mode, per-collection hits, warnings). Operators get `dev doctor` scope
  drift as the companion sweep.
- `collections_searched` keeps its client-visible meaning (requested
  names); all additions are new fields inside `data`. Pre-existing ripple:
  `qdrant_search_entity_payload` returns a tuple (2 in-repo direct callers
  updated).
- Restart both running `mcp_graph_rag.py` servers after landing to pick up
  the new resolution. `strict_scope=true` is recommended for parity/TC
  automation. On win32, the scope-drift report does not run (ps1 doctor
  twin diverges) — disclosed.

## Decision

- Fallback-not-error (A2) for unknown ids by default; `strict_scope=true`
  restores the loud posture where automation needs it — the incident class
  (silent zero) is closed either way because the response must carry the
  warning/error.
- Delegating facade, not deletion: several tests patch doc-side registry
  symbols directly; keeping `_read_project_entries` as the parse hook lets
  the single-rule-resolution goal land without touching those tests
  (facade agreement test pins equality).
- Discovery listed live every call, no TTL cache (sub-millisecond at
  current scale; cap + cache is a measured follow-up if ever needed).
- Fan-out cap = 12 via `SCOPE_MAX_TARGETS` (plan open question resolved:
  no in-repo precedent, constant + env override + visible truncation
  warning).
- Prefix/LIKE (R4) stays; removal deferred to its own plan (blast radius
  documented in the research digest).

## References

- Plan: `docs/plans/261012-project-id-scope-mechanism/plan.md` (+ phase
  files, `research/research-digest.md`, prediction report)
- Rules: `docs/PROJECT_ID_QUERY_RULES.md` (R1, R6a), `docs/mcp-output-contract.md`
  (scope block spec), `docs/PROJECT_REGISTRY.md` (facade, dual-key,
  per-directory), `docs/UNIFIED_INGEST_QUERY_CONTRACT.md` (query
  precedence)
- Tests: `tests/test_semantic_graph_scope_block.py`,
  `tests/test_registry_facade_agreement.py`,
  `tests/test_scope_existence_gating.py`,
  `tests/test_unscoped_discovery.py`, `tests/test_doctor_scope_drift.py`
