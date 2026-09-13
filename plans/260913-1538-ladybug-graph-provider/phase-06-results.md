# Phase 06 results — LadybugDB dogfood + parity + benchmark

Date: 2026-09-13 · Machine: macOS arm64 (darwin 25.6.0) · ladybug 0.20.4 (PyPI) · Python 3.12.14

## Parity gate — PASS (harness)

`tests/test_parity_ladybug.py` (`-m ladybug`) ingests the same sample graph
(3 Functions, 2 Files, CONTAINS + CALLS edges) into falkordblite and ladybug
on separate storage roots and compares:

| Check | Result |
|---|---|
| Node count per label (Function/File) | ✅ match |
| Rel count per type (CONTAINS/CALLS) | ✅ match |
| by-id lookup payload (id + `_label` + props) | ✅ match |
| Traverse `CALLS*1..2` from `main` | ✅ identical sets |
| Portable CONTAINS search | ✅ identical sets |

Deviation notes found by the harness (both fixed during bring-up):

1. Ladybug surfaces missing rel tables in MERGE patterns as
   `Cannot bind X as a relationship pattern label` (not `Table X does not
   exist`) — the auto-DDL classifier now handles both and fails closed when
   endpoint labels cannot be resolved from the query.
2. The manifest label `Table` is a Ladybug reserved keyword: unquoted
   `MATCH (n:Table)` fails to parse. Count/introspection paths now quote
   identifiers. Analyzer queries that hardcode labels unquoted must quote
   this one label (no such call site found in the repo).

## Benchmark smoke (gate, not optimization)

`scripts/benchmark_ladybug.py --nodes 20000 --read-samples 25`
(raw JSON: [phase-06-benchmark.json](phase-06-benchmark.json)):

| Metric | Value |
|---|---|
| MERGE ingest path (row batches via BoundedLane, 20k nodes) | 1.61–1.63 s |
| `COPY FROM` DataFrame path (same 20k rows) | 0.037–0.060 s |
| **COPY speed-up vs MERGE** | **26.8×** (7.8× at 5k nodes) |
| Read by-id p50 / p95 | 0.05 ms / 0.08 ms |
| Read contains-scan p50 / p95 | 0.17 ms / 0.21 ms |
| Read traverse depth-2 p50 / p95 | 0.62 ms / 0.82 ms |

Verdict: **bulk `COPY FROM` is the recommended default for initial full
ingest** (≥5× threshold far exceeded; ignore duplicate-PK semantics via
pre-deduplication by PK). Incremental sync stays on MERGE upserts. Read
latency is flat at this scale.

## Dogfood status

**Pending** — the 1-day daily-flow dogfood (ingest a real medium repo with
`GRAPH_PROVIDER=ladybug`, run sync_processes + MCP queries + doc ingest,
track auto-DDL trigger counts and perceived latency) must run on a developer
machine before the full-cutover decision. This session validated the parity
gate and benchmark on synthetic data only; macOS default remains `falkordb`.

## Go/No-Go recommendation for full cutover (POSIX default flip)

**CONDITIONAL GO** — blocked only on the dogfood run above:

- Pro: parity harness green, dialect gaps handled inside the driver,
  win32 cutover (this plan's business goal) ships independently of POSIX.
- Pro: ingest fast path is dramatically faster; rollback per project is
  `GRAPH_PROVIDER=falkordb` with zero code change.
- Con: fork maturity (pin `ladybug>=0.20.4,<0.21` in place), prepared-statement
  cache invalidation relies on a private connection attribute (guarded with
  `getattr`), FTS ranking differs from FalkorDB fulltext (documented; MCP
  search tools fall back to portable CONTAINS when no FTS index exists).
- ETL `.rdb` → ladybug (`cortex migrate`), bundle v2 (`EXPORT/IMPORT
  DATABASE` verified working: `schema.cypher` + `copy.cypher` + parquet
  files), and the POSIX default flip belong to the follow-up full-cutover plan.
