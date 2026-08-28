# Durable C++/Pro*C node-first recovery — 2026-08-28

## Context

Phase 04E required the C++/Pro*C graph writer to survive interruption without leasing relationships before every node batch had drained. Recovery also had to preserve the caller's explicit FalkorDB path or URI instead of falling back to project or process configuration. This is the scoped C++/Pro*C checkpoint in `plans/260807-1202-graph-ingest-write-path-hardening/plan.md:377-407`, not completion of Phase 04E or Phase 06.

## Change

The journal now opens a versioned global `phase:nodes` barrier: node operations produce it, every non-node operation requires it, and staged relationships become leaseable only after node production closes and all node batches drain (`code-tiny/tools/graph/journal/runtime.py:25-51`, `code-tiny/tools/graph/journal/runtime.py:113-175`, `code-tiny/tools/graph/writer/language_writer.py:399-416`). Specialized Pro*C, resource, unknown-call, and parse-run nodes use the indexed trusted replay compiler before the C++ analyzer releases staged edges (`code-tiny/tools/cplus/cplus_analyzer.py:4740-4789`, `code-tiny/tools/cplus/cplus_analyzer.py:4830-4863`, `code-tiny/tools/cplus/cplus_analyzer.py:5790-5839`).

Preflight writes can omit parser-scoped journal attachment, while explicit FalkorDB path/URI selection is mutually exclusive and propagated to analyzer children. The follow-up routing review fix centralized effective URI resolution across driver creation, topology bootstrap, journal resume, and impact expansion, preventing an explicit local path from being redirected by inherited `FALKORDB_URI` (`code-tiny/tools/graph/cli.py:43-60`, `code-tiny/tools/graph/cli.py:214-252`, `code-tiny/tools/sync/incremental_sync.py:984-1013`, `code-tiny/tools/sync/incremental_sync.py:1041-1075`, `code-tiny/tools/sync/incremental_sync.py:2503-2515`).

The review follow-up also moved C++ incremental deletion behind version-2,
receipt-backed node-phase jobs. File-owned facts are cleaned through 16
allowlisted label-specific queries, orphan `UnknownFunction` cleanup is scoped
by `project_id`, and legacy version-1 cleanup payloads fail closed instead of
being acknowledged under changed semantics
(`code-tiny/tools/graph/writer/language_writer.py:1754`,
`code-tiny/tools/graph/journal/executor.py:113`, commit
`2805fb578994fc1ae396df8d5ae1c605546104b8`). Remote FalkorDB URI userinfo stays
in the child environment and is never copied into argv or persisted command
artifacts (`code-tiny/tools/sync/incremental_sync.py:1219`).

Validation passed the full suite with 1,402 tests, 270 subtests, 10 skips. A fresh local 24-file `procsample` ingest read back 646 business nodes and 1,327 edges. Required-journal replay added only the expected 36 `GraphWriteReceipt` audit nodes. A forced kill after six node batches resumed to 36/36 batches with `phase:nodes` at 12/12; journal event 76 was the barrier close and the first edge lease followed at event 77. The persisted claim predicate requires every referenced barrier to be drained before emitting `batch_leased` (`code-tiny/tools/graph/journal/sqlite_store.py:1024-1063`, `code-tiny/tools/graph/journal/sqlite_store.py:1078-1150`).

## Impact

**Risk level: High.** This changes durable ordering, replay, and graph-target selection in a shared ingestion path. The focused tests and interrupted canary demonstrate C++/Pro*C recovery and business-graph parity, but the full backend matrix and approximately 20,000-file source canary were unavailable and were not completed. Other analyzers/custom writers and the remaining Phase 04E conservation/audit work also remain open (`plans/260807-1202-graph-ingest-write-path-hardening/plan.md:404-407`).

## Decision

Retain the durable global node barrier, trusted indexed node operations, and explicit effective-target propagation as the C++/Pro*C recovery contract. Treat receipt nodes as audit-only replay evidence, and keep Phase 04E/Phase 06 acceptance blocked until the remaining writers, backend matrix, and approximately 20,000-file canary are validated.

## References

- Plan checkpoint: `plans/260807-1202-graph-ingest-write-path-hardening/plan.md:377-407`
- Preflight journal attachment: `d6cc4d977086a006c4ab6f3fbbdae63421a2abd1`
- Durable node-first C++/Pro*C writer: `9133e58c0853d2a6ce65e513afc8ce70a255888f`
- Explicit FalkorDB target selection: `23d88efb8126b6c019cf406a7dfa001f65fab7b0`
- Target-routing review fix: `db3937a86145c99ef6e560d930470415963edfe2`
- Incremental cleanup review hardening: `2805fb578994fc1ae396df8d5ae1c605546104b8`
- Node-first and trusted-operation coverage: `tests/test_graph_write_journal_runtime.py:165-209`, `tests/test_graph_write_journal_runtime.py:830-895`
- Explicit target and preflight coverage: `tests/test_incremental_sync_graph_setup.py:61-109`, `tests/test_incremental_sync_graph_setup.py:194-270`
