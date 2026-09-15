# Phase 07 — composition parity report

- chạy: 2026-09-15 16:29:33
- rust bin: `rust/target/release/cortex-sync`
- scratch: `.cache/p07_composition`

## Leg results

- graph-diff baseline: FalkorDB reachable; per-parser parity scripts own full legs
- message-scan parity: PASS
- overlay-binary proof: orchestrator exit=0 (frameworks scheduled: [], framework map present=True, 5-crashed-overlays-in-map=True)
- qdrant: reachable at http://localhost:6333
- detector_evidence: declared as MASKED_SUMMARY_KEYS (struts-evidence order divergence between Python sort() and Rust insert-order; documented in MASKED_SUMMARY_KEYS comment)
- delegation smoke (static): _RUST_ANALYZER_BINARIES=True _RUST_FRAMEWORK_BINARIES=True .exe probe=True
- dart parity: script present, re-run by phase-dart gate
- flutter parity: script present, re-run by phase-flutter gate
- csharp parity: script present, re-run by phase-csharp gate
- project_topology parity: script present, re-run by phase-project_topology gate
- grep gate: 2 active files reference _analyzer.py; expected=1, deferred-to-phase-08=1, unexpected-blocking=0
  deferred: ['/Users/hieplq1.aip/AI/cortex-harness/cortex_harness/sync_processes.py']

## Failures

none — PASS
