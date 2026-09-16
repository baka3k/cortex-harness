---
title: "Legacy 17 analyzers — graphless emit (move artifact emission OUTSIDE writer block)"
status: phases 01-04 done (code); phase-05 verify + hand-back pending (drill 3 legs + cross-update plans).
created: 2026-09-16
branch: feat/change-db
target: >
  rust/crates/analyzer-cplus/src/analyzer.rs (hoist buf_* + emission, pattern B);
  rust/crates/analyzer-java/src/java_analyzer.rs (hoist WriteAllPayload + emission, pattern A);
  rust/crates/analyzer-sql-family/src/sqlcommon.rs (hoist, pattern A — covers sql + plsql);
  rust/crates/analyzer-dart/src/main.rs (return tuple from write_graph, surface categories, pattern B);
  rust/crates/analyzer-kotlin/src/kotlin_analyzer.rs (hoist + populate files_selected/files_deleted, pattern A);
  rust/crates/analyzer-delphi/src/danalyzer.rs (hoist + populate placeholders, pattern A);
  rust/crates/analyzer-vb/src/pipeline.rs (hoist + populate placeholders, pattern A);
  rust/crates/analyzer-python/src/python_analyzer.rs (Trait/AnalyzerContext seam, pattern C);
  rust/crates/analyzer-js/src/pipeline.rs (hoist, pattern A);
  rust/crates/analyzer-ts/src/pipeline.rs (hoist, pattern A);
  rust/crates/analyzer-php/src/php_analyzer.rs (hoist + populate categories, pattern A);
  rust/crates/analyzer-csharp/src/main.rs (wire artifact emit — never wired, pattern A);
  rust/crates/analyzer-cobol/src/pipeline.rs (capture categories from write_graph_facts, pattern B);
  rust/crates/analyzer-android/src/main.rs + assemble.rs (capture from write_nodes_batch, pattern B — major refactor).
blockedBy:
  - "260916-1432-legacy-17-vector-emit"   # gating plan surfaced this issue
blocks:
  - "260916-1432-legacy-17-vector-emit"   # unblocks phase-04 (flip default + drill)
relatedPlans:
  - "260916-1432-legacy-17-vector-emit"   # prior plan, primary-pass emission wired (phase-02 done)
  - "260916-1154-native-vector-ingest-local"  # local lane done
  - "260915-analyzer-layer-rust-cutover"  # analyzer binaries ported
research: "plans/260916-1520-legacy17-graphless-emit/research/digest.md"
---

# Plan — Legacy 17 graphless emit (fix phase-04 blocker)

## Overview — vấn đề đo 2026-09-16

Plan `260916-1432-legacy-17-vector-emit` phases 01-03 done; phase-04 blocked bởi
architectural issue (out of plan scope). Verified root cause:

| # | Fact | Evidence |
|---|---|---|
| F1 | Orchestrator's embedding pass sets `CORTEX_DISABLE_GRAPH=1` + `CODE_GRAPH_PROVIDER=neo4j` | `cortex-sync/src/orchestrator.rs:3128-3142` (`embedding_phase_env`) |
| F2 | Analyzer main checks `graph_writes_disabled()` early | `cortex-analyzer-framework/src/cli.rs:237` |
| F3 | In graphless mode, `open_store()` returns `None` → `if let Some(writer) = writer { ... }` block skipped → no parse, no data, no artifact | All 11 legacy analyzers share this shape |
| F4 | Orchestrator logs `[upsert] exec:` + `[embedding] ... native pass failed: child did not produce embedding artifact` | Live run on procsample 2026-09-16 |
| F5 | Prior plan `260916-1432` (phase-02) added `maybe_emit_embedding_artifact` calls INSIDE the writer block — unreachable in graphless | Diff review |
| F6 | `EMITTING_VECTOR_CLI_PARSERS` already widened 7 → 22 in `registry.rs:451-455`; capability gate ready | Prior plan phase-03 |
| F7 | Three structural patterns: A (hoist), B (split parse/write — major), C (Trait seam) | Research digest |
| F8 | 4 carve-outs (csharp/cobol/android/plsql) never wired emission at all | Prior plan phase-02 deferred |

## Bằng chứng quyết định (research §Key findings)

- 11 legacy + 4 carve-outs share gated emission shape
- Pattern A (hoist): java, sql, kotlin, delphi, vb, js, ts, php — WriteAllPayload already
  built in main, just need to hoist + populate files_selected/files_deleted
- Pattern B (split): cplus (interleaved flushes), dart (write_graph returns tuple — needs
  surfacing), cobol (write_graph_facts custom), android (assemble.rs ~1200 lines, direct
  write_nodes_batch — major refactor)
- Pattern C (Trait seam): python (AnalyzerContext), ts, php — investigate Trait/AnalyzerContext
  emission seam in cortex-analyzer-framework
- Complexity: cplus=5, cobol=5, android=5 (major); dart=4, vb=4, python=4; java/sql/kotlin/delphi/csharp/js/ts/php=3; plsql=1

## Scope decisions (rev0)

| # | Câu hỏi | Quyết định |
|---|---|---|
| D1 | Phạm vi | **All 11 analyzers + 4 carve-outs** = 15 analyzer mains refactored (user quyết định) |
| D2 | Refactor strategy | **Hoist WriteAllPayload build + emission outside writer block** (Pattern A) cho 8 analyzers; **Split parse from write** (Pattern B) cho 4 analyzers (cplus, dart, cobol, android); **Trait seam** (Pattern C) cho python/ts/php |
| D3 | files_selected/files_deleted population | Mỗi analyzer lấy từ `args.incremental` + `selected`/`deleted_set`/`selected_paths` (mirror prior plan but actually populated — không dùng `Vec::new()` placeholder) |
| D4 | Test coverage | Per-analyzer unit test (graphless mode + --embedding-input-output → artifact exists + parseable) + drill end-to-end |
| D5 | Phase structure | 5 phases theo analyzer group: interface → procsample-critical → dart → carve-outs → verify + hand-back |
| D6 | Backward compat | Phase-03 capability gate (22 parsers) UNCHANGED. Gate chỉ activates khi analyzer emit được trong graphless mode |
| D7 | Sequencing | Phase-01 (interface + verify) → Phase-02 (procsample-critical 4: cplus/java/sql/dart) → Phase-03 (4 carve-outs: csharp/cobol/android/plsql) → Phase-04 (6 remaining: kotlin/delphi/vb/python/js/ts/php) → Phase-05 (verify + hand-back) |
| D8 | Hand-back to phase-04 | Sau khi plan này done, plan `260916-1432-legacy-17-vector-emit` resume phase-04 (flip default + drill 3 legs) |

## Phase map (5 phases — rev0)

| # | Tên | Deliverable chính | Gate chốt |
|---|---|---|---|
| 01 | Interface + verify | Hoist pattern interface, helper function in analyzer-framework (nếu cần); verify pattern A works on simple analyzer (delphi) | Compile + unit test cho 1 analyzer PASS; orchestrator prod-side emits EmbeddingInputArtifact |
| 02 | Procsample-critical 4 | cplus (Pattern B), java (Pattern A), sql (Pattern A), dart (Pattern B — surface embedding_categories from write_graph tuple) | procsample `dev sync code --full-scan` → vectors_upserted > 0; Qdrant collections exist |
| 03 | 4 carve-outs | csharp (Pattern A — first wire), cobol (Pattern B — capture from write_graph_facts), android (Pattern B — major refactor assemble.rs), plsql (inherits sqlcommon) | All 4 analyzers emit artifact in graphless mode |
| 04 | 6 remaining | kotlin, vb, python (Pattern C), js, ts, php | All 6 emit artifact; total 15/15 wired |
| 05 | Verify + hand-back | Full drill (3 legs per phase-04 of `260916-1432`); cross-update both plans; remove placeholders; final test run | Drill 3 legs PASS; 0 vectors_upserted=0 in graphless; 0 regressions in primary pass |

## Rủi ro chính (rev0)

1. **cplus refactor (Pattern B)**: cplus uses streaming flushes (`flush_write_buffers`) inside the writer block. Splitting parse from write means buf_* must be hoisted OUTSIDE writer block. Loop refactor tốn ~50-80 LOC change. (R-class: high)
2. **dart write_graph return tuple (Pattern B)**: `write_graph` currently returns `(BTreeMap, Vec<(String, Vec<Value>)>)`. Need to surface categories from `run()` to `main()`'s emission site. Mirrors cplus's `flush_write_buffers` refactor. (R-class: high)
3. **cobol write_graph_facts**: Custom function returns `BTreeMap<String, i64>`. Need to ALSO return categories. Phase-01 of `260916-1432` audit (`reports/phase01-analyzer-write-all-locations.md`) flagged as Pattern B. (R-class: high)
4. **android assemble.rs (Pattern B)**: ~1200 lines, uses `write_nodes_batch` per-label. Need to capture rows per category BEFORE each `write_nodes_batch`. Major refactor. (R-class: very high)
5. **Trait/AnalyzerContext seam (Pattern C)**: python/ts/php use `AnalyzerContext`. Verify emission seam location in `cortex-analyzer-framework::cli`. (R-class: medium)
6. **files_selected/files_deleted placeholders**: Prior plan phase-02 used `Vec::new()` for delphi/kotlin/vb. Must populate from `selected`/`deleted_files` etc. (R-class: medium)
7. **Re-launch short-circuit interplay**: Phase-03 of `260916-1432` wires short-circuit rule; this plan must ensure short-circuit doesn't skip analyzers that didn't emit artifact (rare case — analyzer exited 0 but artifact missing). Add sanity check. (R-class: medium)
8. **Test fixture for graphless mode**: Need reproducible test that exercises `CORTEX_DISABLE_GRAPH=1` env without standing up FalkorDB. Mock store. (R-class: medium)

## Rollback

- Revert each phase's commits (atomic per analyzer) — surgical
- `EMITTING_VECTOR_CLI_PARSERS` revert nếu cần (capability gate restore to 7 parsers only)
- After revert: legacy 17 → `vector_status="disabled-no-emitter"` (graceful degradation per
  phase-03 D4) — no crash, just no vectors (status quo pre plan)

## Out of scope

- Phase-04 of plan `260916-1432` (flip default + drill + dogfood + runbook update) — that
  plan resumes AFTER this plan done. Cross-update bidirectional both plans.
- New `facts_adapter` for JVM/Web overlays (struts/servlet_jsp/spring/aspnet_* Facts) — not
  in scope. Currently `writes_vectors: false` per registry; revisit if user needs.
- mybatis Facts (4 sibling Facts thiếu `project_id`) — separate plan nếu cần.
- database_sql/database_plsql audit — defer phase-04.

## Cross-plan updates (lúc execute)

1. `plans/260916-1432-legacy-17-vector-emit/plan.md` — update status: phase-04 no longer
   blocked; resume phase-04 after this plan done
2. `plans/260916-1432-legacy-17-vector-emit/phase-04.md` — re-activate (no longer deferred)
3. `plans/260915-2230-python-legacy-cleanup/plan.md` — update blockedBy to include
   this plan (delete Python scripts AFTER this plan + `260916-1432` phase-04 done)

## Sign-off

- User explicit confirmation at end of phase-05 (full drill PASS)
- 15/15 analyzers emit artifact verified
- 0 regressions in primary pass verified
- Hand-back to `260916-1432` phase-04 documented