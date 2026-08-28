---
status: accepted-with-real-source-waiver
date: 2026-08-28
---
# Final graph-ingest validation

## Acceptance decision

The hardened C++/Pro*C graph-write lane is accepted for required mode. Every
other sync-selectable writer is fail-closed in required mode and recorded in
[`required-mode-mutation-inventory.md`](required-mode-mutation-inventory.md)
with an owner and promotion deadline. This closes the plan without allowing a
legacy/direct writer to claim the production contract.

The original 20,186-file source root is not mounted. The operational scale,
memory, ordering, and conservation gates therefore use a deterministic
20,186-file substitute. This is an explicit waiver only for original-source
semantic distribution and the historical warm-baseline comparison; it does
not waive schema, ordering, recovery, integrity, backend, or scale gates.

## C++/Pro*C real-sample canary

Source: `/Users/hieplq1.aip/Migration/procsample` (24 files).  
Remote target: FalkorDB `localhost:6379`, graph `procsample_v3_canary`.  
Local parity target: `/tmp/procsample-v3-local.rdb`, graph
`procsample_v3_local`.

The first v3 attempts fail-closed on real data issues that were previously
silent: external type observations were misclassified as conflicts, internal
grouping ordinals polluted edge digests, unscoped possible-call rows were
rejected, and the repository node lacked normalized project scope. After the
contract fixes, the compatible interrupted run resumed without reparsing its
ACKed work.

| Evidence | Result |
| --- | ---: |
| Journal batches | 53 produced / 53 ACKed |
| Journal rows | 2,115 |
| Node conservation | 739 emitted = 644 unique + 95 declared duplicate |
| Edge conservation | 1,359 emitted = 1,326 unique + 33 declared duplicate |
| Conflicted/rejected rows | 0 / 0 |
| Endpoint audit | sealed |
| Node barrier close / first edge lease | event 255 / event 257 |
| Business nodes / relationships / files | 646 / 1,327 / 24 |
| Local/remote count parity | exact |
| Parse-quality errors / missing files | 0 / 0 |

The read-only audit reports no duplicate canonical identities and no invalid
receipts for the canary. The pre-existing `procsample` graph is marked
incomplete/rebuild because it has 717 business nodes but no manifest-bound
receipts; the audit made no graph mutation.

Artifacts outside the repository:

- `/tmp/procsample-current-audit.json`
- `/tmp/procsample-v3-canary-audit.json`
- `/tmp/procsample-v3-summary-5.json`
- `/tmp/procsample-v3-local-summary.json`

## Deterministic 20,186-file scale gate

Generator: `scripts/generate_graph_ingest_scale_fixture.py`.  
Fixture manifest SHA-256:
`405db9ee1daa5ecb95ae8716e59a1d4041ad4a2625cff5ee3abdc0f1d8a8e9c6`.

The fixture contains 3,281 compile-command C files, 501 fanout headers, and
16,404 Pro*C files. The file-backed required-mode run completed in 49.31 s
(51.85 s measured wall time) with maximum RSS 735,543,296 bytes.

| Evidence | Result |
| --- | ---: |
| Files / parsed functions | 20,186 / 3,281 |
| Journal batches | 122 produced / 122 ACKed |
| Journal rows | 60,559 |
| Node conservation | 26,749 = 23,469 unique + 3,280 duplicate |
| Edge conservation | 33,810 = 33,810 unique |
| Endpoint audit | sealed |
| Node barrier close / first edge lease | event 294 / event 296 |
| Graph business nodes / edges / files | 23,471 / 33,811 / 20,186 |
| Journal / artifact bytes | 111,046,656 / 19,551,599 |

## Backend, publication, and regression gates

- The same real sample produced 646 business nodes, 1,327 relationships, and
  24 project-scoped files on file-backed and remote FalkorDB.
- A disposable three-point Qdrant fixture produced identical IDs, project
  payloads, and cosine-normalized vectors (to provider-neutral float precision)
  on local and `http://localhost:6333`; both remote disposable collections were
  removed after validation.
- Mixed/force-local target isolation, remote error no-fallback behavior,
  generation publication/recovery, retained-generation rollback, schema
  preflight, index plans, and guarded mutation tests pass in the repository
  suite.
- Focused backend/publication suite: 168 tests and 9 subtests passed.
- Journal/runtime suite after v3 changes: 68 tests passed.
- Full repository regression: 1,447 tests passed, 10 skipped, and 270
  subtests passed. The 14 warnings are existing FalkorDB deprecation and
  Pydantic forward-reference warnings; there were no test failures.

## Operator commands

Generate a disposable scale fixture:

```bash
.venv/bin/python scripts/generate_graph_ingest_scale_fixture.py /empty/output/path
```

Audit without mutating a remote graph:

```bash
.venv/bin/python scripts/audit_graph_ingest.py \
  --uri localhost:6379 --graph GRAPH_NAME --output /tmp/graph-audit.json
```

Exit code 0 means eligible for the remaining publication validators; exit code
2 means incomplete/rebuild is recommended.
