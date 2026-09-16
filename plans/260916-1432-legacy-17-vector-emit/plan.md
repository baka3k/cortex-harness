---
title: "Legacy 17 parsers — emit EmbeddingInputArtifact in primary pass, read in embedding pass"
status: phases 01-03 done; phase-04 deferred (root cause: graphless mode architecture)
created: 2026-09-16
branch: feat/change-b
target: >
  rust/crates/cortex-sync/src/orchestrator.rs (gate widened + re-launch short-circuit + vector_status semantics);
  rust/crates/cortex-sync/src/registry.rs (SHARED_VECTOR_CLI_PARSERS → EMITTING_VECTOR_CLI_PARSERS, 7 → 22 parsers);
  11 analyzer crates wired for primary-pass emission (cplus, java, sql, dart, kotlin, delphi, vb, python, js, ts, php)
  + capture-only for 4 (csharp, cobol, android, plsql-defer);
  1 framework analyzer shell uses existing embedding_categories().
blockedBy:
  - "260916-1154-native-vector-ingest-local"   # executor: local Qdrant lane done
  - "260915-analyzer-layer-rust-cutover"        # executor: all 24 parsers have Rust binaries
  - "260915-2300-sync-plane-rust-cutover"       # DONE — Python sync plane retired
blocks:
  - "260915-2230-python-legacy-cleanup"          # unblocks: Python analyzer scripts deletable per disposition
  - "260916-0936-presence-gating-parser-retirement"  # separate lane
relatedPlans:
  - "260915-2027-vector-lane-rust-port"          # precedent: shared-7 native embedding wiring
  - "260913-2130-rust-full-migration"            # umbrella
research: "plans/260916-1432-legacy-17-vector-emit/research/digest.md"
---

# Plan — Legacy 17 parsers → emit EmbeddingInputArtifact in primary pass, read in embedding pass

## Status (rev2 — 2026-09-16 15:10)

### Completed
- [x] **Phase 01** — spike + interface (audit + 5 unit tests + capability table rename)
- [x] **Phase 02** — wire 11 analyzer crates (cplus/java/sql/dart/kotlin/delphi/vb/python/js/ts/php)
      + capture-only for 4 (csharp/cobol/android/plsql-defer) + shell reuses existing fn
- [x] **Phase 03** — orchestrator widening (gate 7→22 parsers, re-launch short-circuit, vector_status semantics)
- [x] cortex-sync 85 unit tests PASS (no regression)
- [x] Direct analyzer invocation WORKS end-to-end (cplus 1517 docs, sql 17 docs, java/sql/dart)

### Root cause of phase-04 deferral (architectural, NOT a bug in this plan)

**Verified via debug** (rev2):

1. Orchestrator's embedding pass sets `CORTEX_DISABLE_GRAPH=1` + `CODE_GRAPH_PROVIDER=neo4j`
   in env (`cortex-sync/orchestrator.rs:3128-3142`).
2. Each analyzer's main checks `graph_writes_disabled()` early. In graphless
   mode, `open_store()` returns None → `if let Some(writer) = writer { ... }`
   block is SKIPPED ENTIRELY → analyzer parses nothing, no data accumulated.
3. Analyzer-frame CLI `--embedding-input-output` IS passed but the analyzer
   reads it from the closed `if let Some(writer)` block.
4. Result: orchestrator's embedding pass invokes the analyzer (logs `[upsert] exec:`),
   the analyzer exits early with no parse work, no artifact file produced.
   Orchestrator's `finish_native_embedding_pass` then errors with
   "child did not produce embedding artifact … No such file or directory".

### Required architectural fix (out of plan scope)

Each legacy 17 analyzer must ALWAYS parse + extract categories, and ONLY
conditionally write to graph:
```
loop { parse file → push to buf_* }
if let Some(writer) = writer { flush_write_buffers(...) }  // graph write optional
if args.embedding_input_output().is_some() {
    // ALWAYS extract categories from buf_* → emit artifact
}
```

Touch points (11 analyzers, 17 cells including framework):
- analyzer-cplus (graphless branch needs refactor — buf_* currently scoped inside writer block)
- analyzer-java (similar structure)
- analyzer-sql-family
- analyzer-csharp
- analyzer-dobol
- analyzer-delphi
- analyzer-kotlin
- analyzer-android (write_nodes_batch direct)
- analyzer-vb
- analyzer-python
- analyzer-js
- analyzer-ts
- analyzer-php
- analyzer-plsql
- analyzer-dart (write_graph returns tuple; need to surface embedding_categories from main, currently bug)

This is **out of scope** for the current plan. Recommended next plan:
`260916-HHMM-legacy17-graphless-emit`.

### Phase-04 status

Phase-04 (flip default + drill 3 legs + dogfood) DEFERRED until graphless emit
fix lands. Drill legs 1-3 would fail without the architectural fix.

## (Historical sections — see rev0/rev1 for the 4-phase plan)

Phase-01/02/03 done. Phase-04 needs unblocking fix; current plan cannot complete
phase-04 without rewriting each analyzer's main flow.