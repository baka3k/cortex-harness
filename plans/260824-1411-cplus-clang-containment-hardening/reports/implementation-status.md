# Implementation status and rollout decision

Date: 2026-08-24

## Decision

**RECONVENE.** Containment and adapter correctness are implemented and their
focused suite is green. Semantic runtime publication remains disabled because
the declared single-owner and provider phase blockers are not complete, no
authorized isolated FalkorDB/Neo4j canary targets were supplied, and the
promotion sample/coverage gates have not been met on one immutable horizon.

## Completed

- Tree-sitter is the only selectable/cacheable C/C++ structural backend.
- Protocol-1 LIBCLANG output is diagnostic-only with the stable
  `cross_backend_structure_forbidden` outcome.
- Legacy LIBCLANG payloads are rejected at payload validation and provider
  generations can be durably fenced as structurally incompatible.
- Legacy mutable extent caching was replaced with immutable parse-local
  two-pass call resolution in both adapter and protocol-2 extraction.
- `cplus-function-v2` distinguishes overloads and joins reviewed Clang
  observations to Tree-sitter identities; legacy name/arity IDs are aliases.
- Logical callsites have a v2 callee-independent spelling/expansion/ordinal
  identity contract.
- Strong direct evidence now requires faithful context, accepted admission,
  complete execution, parent attestation, and a manifest key.
- Exact scope coverage compares immutable expected keys to actual rows and
  rejects missing, duplicate, unexpected, or incomplete keys.
- The unreachable pilot revision was replaced with reachable immutable commit
  `e3f6b006d1a006f9c6e161a7d25572643d45c759`; the declared Neo4j extra was
  installed in the test environment.

## Verification

```text
215 passed, 10 subtests passed
```

Command: the combined focused suite listed in Phase 05, including the new
Clang parser/differential and dual-plane component integration tests.

Mandatory review completed its three-cycle cap at 9.0/10 with no critical
finding. The final high finding (provider `id` taking precedence over logical
callsite identity and missing production `site_id` support) was corrected
exactly as specified; the focused suite above was rerun after that correction.

## Open gates

- `260807-0929-mcp-ingest-query-concurrency` remains `pending`; normal-path
  semantic orchestration must not create a second scheduler or publisher.
- `260807-1202-graph-ingest-write-path-hardening` remains `in_progress` and
  still owns durable provider mutation/reconciliation behavior.
- `260817-storage-backend-adapter` remains `active` and owns the remote/local
  provider boundary required by the dual-provider canary.
- No authorized fresh provider targets, nonce ownership markers, protected
  credentials, or live readback/rollback evidence were available in this run.
- The required 500 reviewed direct callsites, per-cohort sample floors,
  30-key build cohorts, >=90% faithful-context ratio, production-scale gates,
  and all nine provider crash boundaries remain unproven.

Containment therefore remains the default. No semantic generation was
published, activated, rolled back, deleted, or relabeled current.
