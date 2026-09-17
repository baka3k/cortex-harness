---
type: research
date: 2026-08-07
---
# Research: recurring graph-ingestion stall

## Summary

The run did not stop at `relations:READS_FROM 87/87`. That line was the last
completed batch. The analyzer then waited on the embedded database socket while
FalkorDB used approximately one CPU core on a later relationship query. The
recurring behavior is caused by an unlabeled endpoint query whose work grows
with the entire graph, compounded by indexes being created after streaming and
progress being emitted only after query completion.

## Live runtime evidence

- The Python analyzer sampled mostly at 0% CPU in a socket wait.
- Its private Redis/FalkorDB process sampled around 98-100% CPU.
- During a 30-second sample the graph remained at approximately 100,432 nodes,
  420,511 relationships, and 3,000 `File` nodes while Redis command activity
  advanced. This is slow execution, not a dead process.
- FalkorDB slowlog contained generic relationship batches around 18.6-34.1
  seconds at this graph size.
- Live schema inspection found only `Project.project_id` and `Repository.name`
  indexes.
- `GRAPH.EXPLAIN` for the generic relationship write reported:

  ```text
  Aggregate
    Update
      Merge
        Apply
          Unwind
          Cartesian Product
            Filter
              All Node Scan | (a)
            Filter
              All Node Scan | (b)
  ```

## Code-path evidence

- `code-tiny/tools/graph/writer/language_writer.py` documents grouping typed
  relationships by `(source_label, target_label, rel_type)` but implements only
  `rel_type` grouping. Its query uses
  `MATCH (a {id: row.source_id}), (b {id: row.target_id})` without labels.
  Git history dates this contradiction to commit `af57bb08` on 2026-05-09,
  confirming a latent shared-writer defect rather than a Pro*C regression.
- The same file logs progress after the awaited query and sends it through both
  logger and `print`, explaining duplicate output and the stale last line.
- `code-tiny/tools/cplus/cplus_analyzer.py` flushes 500-file node and
  relationship buffers, then creates its local index list only after the entire
  streaming loop. That point is unreachable while an earlier relationship
  batch remains slow.
- The analyzer-local list also omits multiple emitted labels, including several
  C++ type/member labels and Pro*C SQL labels.
- `code-tiny/tools/graph/driver/falkordb_driver.py` serializes local graph work
  on one execution lane and has no effective local embedded query deadline.
  `create_indexes()` catches and logs individual failures, so required schema
  is not a startup invariant.
- `code-tiny/tools/sync/incremental_sync.py` only eagerly ensures the Project
  and Repository lookup indexes.
- `code-tiny/scripts/setup_constraints.py` contains broader schema knowledge,
  including Pro*C labels, but is a standalone path and is not invoked by normal
  sync. Schema ownership is therefore fragmented.
- `spring_writer.py`, `mybatis_writer.py`, the older language relationship
  helper, and some topology paths also perform generic/unlabeled endpoint
  lookup. A C++-only change would leave the defect class active.
- Direct occurrences also exist in Android Java relation writes, Android Kotlin
  unlabeled node upserts, TypeScript backend `symbol_id` matches, and
  `cross_edge_ops.py`. Some graph-expander and MCP read paths use unlabeled
  identity lookup and require either concrete labels or an explicit designed
  polymorphic lookup contract.
- C++ computes/imports a state path but the streaming flush does not pass state
  through writer calls. Existing offsets are list-local, so reusing them across
  repeated 500-file buffers could skip later work. Other generic call writes
  increment relationship counters on rerun, so current recovery is not fully
  idempotent.
- Existing tests do not assert schema-before-write ordering, endpoint index
  scans, absence of Cartesian products, or fixed-batch scaling with total graph
  size.
- `test_incremental_sync_graph_setup.py` currently expects only the Project and
  Repository indexes, encoding the incomplete preflight as accepted behavior.

## Parser evidence

- `6673/20186` means roughly 33.1% of scanned files had a Tree-sitter root
  error flag or explicit `ERROR` node. The printed `2650 ERROR nodes` counts
  only explicit error nodes, so the two values use different definitions.
- Direct sampling of legacy CP932 files found cases with `MISSING` nodes and no
  explicit `ERROR` node. AST traversal continues; this warning is not the
  database CPU stall.
- Header alternate-parser retry applies only to headers and selected an
  alternate parser for five files.
- The repository had approximately 3,281 compile-command entries for 20,186
  scanned files. Tree-sitter does not consume those flags except for language
  selection, and the libclang fallback threshold is high enough that many
  warned files do not fall back.

## Official FalkorDB guidance

- FalkorDB range indexes are label/property pairs. Queries must reference the
  indexed label and property for the planner to introduce an index scan; the
  documentation also notes index write/storage overhead:
  [Range index](https://docs.falkordb.com/cypher/indexing/range-index.html).
- `GRAPH.EXPLAIN` constructs a query plan without executing it and is the
  appropriate regression gate for `Index Scan` versus node scans:
  [GRAPH.EXPLAIN](https://docs.falkordb.com/commands/graph.explain.html).
- `CALL db.indexes()` exposes label, property, type, status, and informational
  state, allowing preflight to verify readiness rather than assuming DDL
  completion:
  [Procedures](https://docs.falkordb.com/cypher/procedures.html).
- Current FalkorDB configuration supports default/maximum query timeouts and
  memory/queue controls, but timed-out writes can require additional rollback
  time and older releases differ. These settings are circuit breakers, not a
  substitute for indexed queries:
  [Configuration](https://docs.falkordb.com/getting-started/configuration.html).

## Conclusions

1. Creating indexes manually is neither necessary nor sufficient. The tool
   must create and verify them automatically before streaming, and queries must
   name the indexed labels.
2. The durable abstraction is a canonical schema/identity manifest shared by
   setup, drivers, writers, and analyzers.
3. Query-plan structure, endpoint integrity, idempotent recovery, and truthful
   in-flight progress need automated gates so the failure cannot silently
   recur.
4. Parser-quality remediation should be tracked independently; this plan only
   makes its metrics internally consistent and actionable.
