# MCP storage concurrency hardening — 2026-08-28

## Context

The MCP ingest/query concurrency plan required a single embedded-store owner,
bounded query and writer admission, generation-safe publication, and truthful
failure/lifecycle behavior. Commits `8e35140c4f34f706df8eb39219dc5e8ea71ddd4a`
and `aa687cecc918ec9f3b14f9d22522769f9e87c60c` unblocked the implementable
storage and MCP slices without completing the full plan
(`plans/260807-0929-mcp-ingest-query-concurrency/plan.md:345`).

## Change

The storage owner now acquires leases before creating bounded named executors,
persists and recovers ingestion jobs, pins active generations for reads, drains
before releasing resources, and exposes lane/job health
(`cortex_harness/storage/gateway.py:112`). Generation recovery discards abandoned
temporary manifests, while retirement uses reference checks and durable
tombstones to prevent a retired generation from being republished
(`cortex_harness/storage/generation.py:234`,
`cortex_harness/storage/generation.py:389`).

MCP retrieval now runs on a count-bounded named lane, rejects work during drain,
single-flights embedder initialization, validates `top_k`, and preserves storage
failures instead of returning false empty successes
(`code-tiny/mcp/services/explore_service.py:61`,
`code-tiny/mcp/services/explore_service.py:334`,
`code-tiny/mcp/services/explore_service.py:387`,
`code-tiny/mcp/services/explore_service.py:607`). The unified MCP boundary renders
typed gateway error details and retry guidance
(`code-tiny/mcp/unified_mcp.py:762`).

## Impact

**Risk level: high.** These changes govern shared embedded-store ownership,
publication visibility, cancellation, shutdown, restart recovery, and error
truthfulness; defects could expose partial generations, bypass bounded capacity,
or release storage while synchronous work still runs. Focused verification passed
90 tests. The full-suite run reported **1,392 passed, 10 skipped, and one
environment-induced failure** because `uv run` injected an absolute `UV` value
that defeated the lifecycle test's mocked lookup; that isolated test passed when
rerun directly with `env -u UV`. Mandatory review approved the result at **9.6/10
with zero critical or high findings**.

Phase 03 and Phase 06 remain blocked on the declared
`260807-1202-graph-ingest-write-path-hardening` dependency: staging must consume
its hardened writer contract, and guarded rollout still requires its full canary
plus the mixed-load acceptance evidence
(`plans/260807-0929-mcp-ingest-query-concurrency/plan.md:393`,
`plans/260807-0929-mcp-ingest-query-concurrency/phase-06-acceptance-and-rollout.md:27`).
This entry therefore does not claim full-plan completion or rollout.

## Decision

Keep embedded storage fail-closed behind one owner and fixed bounded lanes;
serve only pinned committed generations while staging, and represent overload,
maintenance, deadlines, and storage failures as structured errors. Retain the
safe profile and pause/restart fallback until the dependency and canary gates
close; do not promote concurrency or enable the no-downtime path from unit and
repository-suite evidence alone.

## References

- plan: `plans/260807-0929-mcp-ingest-query-concurrency/plan.md`
- commit: `8e35140c4f34f706df8eb39219dc5e8ea71ddd4a`
- commit: `aa687cecc918ec9f3b14f9d22522769f9e87c60c`
- tests: `tests/test_generation_faults.py:31`
- tests: `tests/test_mcp_gateway_errors.py:25`
- tests: `tests/test_storage_concurrency_stress.py:31`
