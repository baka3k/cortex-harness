# LadybugDB embedded graph provider (win32 default, POSIX opt-in) — 2026-09-13

## Context

`plans/260913-1538-ladybug-graph-provider/plan.md` — replace the "FalkorDBLite
not installable on win32" gap with LadybugDB (Kùzu successor, PyPI `ladybug`),
keeping falkordblite as the macOS/Linux default. Executed end-to-end via
`hi-craft --full` (spike → 6 phases → review → fix cycle → commit).

## Change

- **Driver**: new `code-tiny/tools/graph/driver/ladybug_driver.py` — embedded,
  local-only; one store **file** per named graph (`<root>/<owner>.lbug/<graph>`,
  canonical mapping in `cortex_harness/storage/layout.py:15`); FalkorDB driver
  contract parity (BoundedLane, daemon-thread executor, deferred close,
  `AmbiguousWriteTimeoutError` for mutation timeouts); schema bootstrap from
  `CODE_GRAPH_SCHEMA` (155 labels + 7 registry rel types) on every write-mode
  open; gated auto-DDL (`CORTEX_GRAPH_AUTO_DDL`, default on, loud logs)
  covering three binder error shapes incl. `Cannot bind X as a relationship
  pattern label`; prepared-statement cache invalidation after DDL (Ladybug
  caches plans per query string — stale plans resurrect pre-ALTER errors);
  ART (range→) + FTS (fulltext→) indexes; `COPY FROM` bulk fast path
  (`bulk_load`); portable `list_labels` / `list_relationship_types` /
  `fulltext_query_nodes`.
- **Shared extraction**: `core/query_normalize.py` (CALL-importing subquery
  rewrite + `datetime()`→`timestamp($param)` for ladybug) and `core/errors.py`
  (`AmbiguousWriteTimeoutError`); portable find/search methods lifted into
  `CypherGraphDriver` (`code-tiny/tools/graph/core/cypher_driver.py:24`).
- **Plumbing**: `GraphProvider.LADYBUG` (`kuzu` = deprecated alias routed onto
  it) across provider contract, graph factory, storage targets/overlay,
  `StorageFactory.get_ladybug_driver`, project registry, dev CLI (win32
  default flip in `cortex_harness/dev.py:466` — macOS/Linux untouched),
  scan CLI, doc-tiny graph store, MCP boot paths + sibling discovery
  (`code-tiny/mcp/ladybug_discovery.py` — other-instance stores only, opened
  read-only without a lease), migration inventory, doctor probe.
- **Deps/docs/CI**: `ladybug>=0.20.4,<0.21` core dependency (wheels: win_amd64/
  win_arm64, macOS 15+, manylinux); `falkordb-local` rollback extra; ReadMe
  quickstart + platform matrix + rollback; Design.md diagrams; ladybug CI job
  in `.github/workflows/lifecycle-macos.yml`.

## Impact

Windows gets an out-of-the-box local graph (no Docker/server); macOS/Linux
behavior is unchanged (falkordblite default, `GRAPH_PROVIDER=ladybug` opts in).
Risk: medium — new native dependency pinned `<0.21`; Ladybug's static schema
is masked by bootstrap + auto-DDL (every ALTER logged); FTS ranking differs
from FalkorDB (MCP search tools keep their CONTAINS fallback). Rollback per
project: `GRAPH_PROVIDER=falkordb` (+ remote URI on win32), no data migration.

## Decision

- `LADYBUG` as a new enum value instead of reusing `KUZU` (name matches the
  PyPI package; kuzu stays a deprecated alias).
- Dialect debt contained in drivers: MCP `db.relationshipTypes()`/`db.labels()`
  call sites rewired to driver APIs; the 8 raw `db.index.fulltext.queryNodes`
  MCP sites keep their existing CONTAINS fallbacks (documented deviation,
  driver API ready for adoption).
- Benchmark evidence (`scripts/benchmark_ladybug.py`): COPY FROM DataFrame is
  **26.8× faster** than MERGE-path ingest at 20k nodes → bulk recommended for
  initial generations, MERGE stays for incremental sync (COPY skips duplicate
  PKs, never updates). Results: `plans/.../phase-06-results.md`.
- Reviewer fix cycle: secondary stores now bootstrap (cache keyed by opened
  store path); read-intent queries on missing graphs raise
  `database does not exist` instead of silently creating stores; sibling
  connections never serve writes. Full suite: 1667 passed (1 pre-existing
  csharp acceptance-matrix subtest failure, verified on the clean tree).
- Dogfood (phase 06 daily-flow run on a real repo) still pending — gate for
  the future POSIX full-cutover plan; win32 cutover ships independently.

## References

- plan: ./plans/260913-1538-ladybug-graph-provider/plan.md (spike answers in phase-01.md; deviations in phase-04.md; benchmark JSON + go/no-go in phase-06-results.md)
- commit: f62aeff
- tests: code-tiny/tests/test_ladybug_driver_local.py (65 fake/integration), tests/test_ladybug_provider_plumbing.py, tests/test_parity_ladybug.py (`-m ladybug`)
