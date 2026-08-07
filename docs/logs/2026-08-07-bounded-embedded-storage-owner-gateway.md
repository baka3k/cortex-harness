# Bounded Embedded Storage Owner Gateway — 2026-08-07

## Context

The MCP ingest/query concurrency plan requires one embedded-store owner per physical target, bounded work admission, and reads that continue on the last committed generation while a replacement is staged.

## Change

- Added `StoreGateway` lanes, dedicated executors, physical-target leases, and a single writer lane; reads pin the active generation and return freshness metadata (`cortex_harness/storage/gateway.py:59`, `cortex_harness/storage/gateway.py:109`, `cortex_harness/storage/gateway.py:148`).
- Added generation manifests with atomic active-manifest replacement, reader reference tracking, and retirement only after readers drain (`cortex_harness/storage/generation.py:36`, `cortex_harness/storage/generation.py:59`, `cortex_harness/storage/generation.py:93`).
- Serialized embedded FalkorDB queries through a bounded executor and restricted retries to clearly read-only Cypher (`code-tiny/tools/graph/driver/falkordb_driver.py:70`, `code-tiny/tools/graph/driver/falkordb_driver.py:209`, `code-tiny/tools/graph/driver/falkordb_driver.py:336`).
- Added focused contract coverage for the single-writer profile, bounded overload response, generation pinning, and idempotent ingestion jobs (`tests/test_storage_concurrency_contract.py:41`, `tests/test_storage_concurrency_contract.py:61`, `tests/test_storage_concurrency_contract.py:78`, `tests/test_storage_concurrency_contract.py:101`).

## Impact

Risk level: **high**. Embedded graph/vector access now has an explicit owner and bounded execution boundary, preventing unbounded queues, concurrent embedded writes, and retry-driven duplicate mutations. Queries can retain a consistent committed generation during staged updates.

## Decision

Use physical storage identity—not logical project identity—as the lease, queue, and idempotency boundary. Keep exactly one writer and conservative per-handle concurrency until measurements justify more; unbounded concurrency and retrying ambiguous mutations were rejected because embedded operations may have already committed.

## References

- Plan: [MCP ingest/query concurrency](../../plans/260807-0929-mcp-ingest-query-concurrency/plan.md) (`plans/260807-0929-mcp-ingest-query-concurrency/plan.md`)
- Gateway: `cortex_harness/storage/gateway.py:59`
- Generation publication: `cortex_harness/storage/generation.py:59`
- FalkorDB adapter: `code-tiny/tools/graph/driver/falkordb_driver.py:336`
- Contract tests: `tests/test_storage_concurrency_contract.py:61`
- Commit: `654761820be7bdd1e1f45deea93730e69969d872`
