# Phase 04B: Serializable operations, producers, and barriers

## Context

`LanguageCodeWriter.write_batches()` currently receives an opaque coroutine
closure. That closure cannot be stored or replayed, and the same relationship
state key can recur across streamed buffers. Some custom writers also report
input length without database reconciliation or use non-idempotent increments.

## Requirements

- Replace closure-only replay identity with a serializable, versioned operation
  specification.
- Enqueue every supported node/relationship/call batch before source memory is
  cleared.
- Preserve shared writer ordering with explicit produced/drained barriers.
- Make every journal-enabled mutation deterministic, reconciled, and replay
  safe before enabling retries.
- Migrate shared writers first and explicitly inventory direct/custom bypasses.

## Architecture

Introduce `GraphWriteOperation` containing operation type/version, validated
labels/type, parameter contract, expected rows, reconciliation strategy, and
required barriers. The executor compiles runtime Cypher only from allowlisted
operation specifications; it never deserializes arbitrary code or Cypher.

Producers write canonical artifact segments and enqueue batch metadata. Node
batches can drain as they are produced. Relationships remain ineligible until
their declared endpoint-label barriers are drained. Production closes only at
an analyzer-level lifecycle boundary, not at each `write_all()` call.

## Related Files

- `code-tiny/tools/graph/writer/language_writer.py`
- `code-tiny/tools/graph/writer/query_contract.py`
- `code-tiny/tools/cplus/cplus_analyzer.py`
- Shared/direct writers identified by the repository mutation-path inventory
- `tests/test_cplus_graph_runtime.py`
- `code-tiny/tests/test_relationship_query_contract.py`

## Implementation Steps

1. Inventory every writer and direct graph mutation path; classify it as shared
   operation, migratable custom operation, or blocked unsupported operation.
2. Define serializable operation versions for node upsert, repository edge,
   typed relationship upsert, deterministic call-site upsert, and supported
   custom batches.
3. Refactor `write_batches()` to enqueue an operation/artifact before execution
   and use job identity instead of buffer-local offset keys.
4. Make node and relationship counts reconcile against returned/read-back graph
   state before ACK eligibility.
5. Remove increment-on-replay behavior such as `CALLS` count accumulation;
   aggregate/set deterministic counts or require stable call-site identity.
6. Add analyzer lifecycle APIs to open production, close label barriers, close
   all node production, and close relationship/call production.
7. Migrate C++ streaming first: enqueue every buffer, enqueue inferred/tail
   nodes before closing node production, and make `INCLUDES` depend on the
   drained `File` barrier. Remove the memory-only deferred include list.
8. Migrate all `LanguageCodeWriter` consumers, then the direct/custom writer
   inventory. Repository checks prevent new bypasses.

## Todo

- [ ] Mutation-path inventory is complete and scope is truthful.
- [ ] Serializable operation contracts replace opaque replay closures.
- [ ] Non-idempotent/reconciliation-weak operations are converted or blocked.
- [ ] Analyzer-level barrier lifecycle is implemented.
- [ ] C++ and shared writer producers durably enqueue before releasing memory.
- [ ] Direct/custom writers are migrated or explicitly rejected in journal mode.

## Risks

Closing a node barrier too early can reproduce the same missing-endpoint defect
in a different form. C++ inferred and tail nodes occur after normal file
buffers. A partial migration that advertises all-writer durability while custom
paths bypass the journal is worse than a scoped feature flag.

## Success Criteria

Every enabled graph mutation can be reconstructed from a validated operation
specification and immutable artifact. A 501-file forward include produces one
durable relationship job that is not claimable until all 501 file-node effects
are ACKed. Replaying an ACK-ambiguous job does not increment or duplicate graph
effects.
