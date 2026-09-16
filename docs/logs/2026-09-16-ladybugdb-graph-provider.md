# LadybugDB embedded graph provider — 2026-09-16

## Context
Plan `docs/plans/260916-ladybugdb-graph-provider/plan.md` (--full mode): add
LadybugDB (`pip install ladybug`, the archived KuzuDB successor) as a third
graph provider next to FalkorDB (default) and Neo4j, in embedded mode only.
The plan's red team had flagged 3 critical blockers (package identity,
schema-first writer layer, CALL-subquery ingest paths) requiring a Phase 0
spike before implementation.

## Change
Committed as `a747d2f`:

- **Phase 0 spike (GO)** — `docs/plans/260916-ladybugdb-graph-provider/phase-00-spike-report.md`:
  verified `ladybug==0.20.4` API, full Cypher compat matrix, and benchmark
  (node ingest 3×, edge ingest 13.6× faster than FalkorDBLite; traversals
  single-digit ms slower — acceptable). Key discovery: the standard
  project-scope predicate `($project_id IS NULL OR n.project_id_normalized
  STARTS WITH ...)` runs natively; no rewrite needed.
- **Driver** — `code-tiny/tools/graph/driver/ladybug_driver.py` (new): embedded
  one-catalog-per-`.lbdb` driver with StorageLease + BoundedLane +
  daemon-thread executor parity with FalkorDBDriver; dialect rewrites for
  `SET x += row/props` (typed columns + `_properties` JSON spill), `CALL {
  WITH row ... }` guards, `FOREACH`, `datetime()`, list comprehensions,
  reserved-word params (`$end` breaks the LadybugDB parser), `labels(n)[0]`,
  and the doc-tiny Paragraph composite merge key (synthetic `__pk`).
  Self-healing DDL for missing node/rel tables plus prepared-cache
  invalidation (LadybugDB caches failed prepares keyed by (query, param
  signature) — a stale entry fails forever without this).
- **Schema compiler** — `code-tiny/tools/graph/schema/ladybug_schema.py` (new):
  writer property inventory → `CREATE NODE/REL TABLE IF NOT EXISTS` DDL,
  hybrid typed columns + spill, PK overrides per the manifest identity
  registry (Project→project_id, Workflow→workflow_id, CallSite→site_id, …),
  multi-endpoint rel tables.
- **Registration + wiring** — `GraphProvider.LADYBUG` enum, aliases,
  fail-closed normalization and env isolation in
  `provider_contract.py:114-136`, `dev.py`, `mcp_runtime_config.py`,
  `mcp-lifecycle.ps1`; graph CLI `--graph-provider ladybug --ladybug-path`;
  doc-tiny `LadybugDBGraphStore`; storage paths
  `v1/instances/<id>/ladybug/{code,doc}/data.lbdb` with
  `LADYBUG_PATH/CODE/DOC` overrides; `StorageFactory.get_ladybug_driver`;
  4 MCP backends + explore/impact services + `ladybug_discovery.py`
  read-only sibling fan-out; embedded-lease MCP pause in `dev.py`
  `_pause_mcp_for_sync` extended to ladybug.
- **Docs** — DATABASE_INTEGRATION LadybugDB section; PROJECT_ID_QUERY_RULES /
  UNIFIED_INGEST_QUERY_CONTRACT / PROJECT_REGISTRY provider lists updated.
- **Tests** — `tests/test_ladybug_driver.py` (new): 37 tests (pure rewrites,
  live-DB ingest/scoping/spill round-trip, doc store, storage paths).

## Impact
All consumers of GRAPH_PROVIDER gain a third option; FalkorDB default
behavior unchanged. Risk: medium — new provider, dialect adaptation via
regex rewrites over writer Cypher; mitigated by writer-template-verified
rewrites, live E2E, adversarial review, and full-suite regression parity
(36 pre-existing failures before and after, zero new).

## Decision
- **New `LADYBUG` enum** instead of reusing `KUZU`: fork with breaking
  changes; reuse would misidentify the dialect.
- **Hybrid typed columns + `_properties` JSON spill** (plan Option C): keeps
  queryable identity/routing columns while absorbing heterogeneous writer
  payloads without a 100%-complete property registry.
- **Driver-level rewrites** (not writer-layer forks): one adaptation point;
  writer Cypher stays provider-portable.
- **Graph names stay in FALKORDB_GRAPH/DOC_FALKORDB_GRAPH carriers** for
  ladybug (isolation exempts them): provider-neutral metadata; introducing a
  parallel carrier would have touched every runtime surface for no gain.
- Review findings fixed post-score: rel `id` merge-key column, rel typed
  assignments in `SET r += row`, temp-marker leak into caller rows,
  sibling-catalog DDL guard, unlocked native calls in inspect_indexes,
  cache-invalidation warning, graph-name carrier survival.

## References
- plan: ./docs/plans/260916-ladybugdb-graph-provider/plan.md
- spike: ./docs/plans/260916-ladybugdb-graph-provider/phase-00-spike-report.md
- commit: a747d2f
- tests: tests/test_ladybug_driver.py
