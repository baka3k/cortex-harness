# Phase 02 - Implement Phased Execution

## Scope

- Propagate `sync_mode` through `cortex_harness/dev.py` to `incremental_sync.py`.
- Run primary graph passes without vector configuration.
- Run framework overlays and project topology after graph journals drain.
- Run primary vector passes with graph writes disabled.
- Emit separate graph/vector summary entries and preserve final source verification.

## Acceptance

- Child environment isolation is covered by tests.
- Graph and embedding modes execute only their selected storage stage.
- Default both mode performs topology before embeddings.

