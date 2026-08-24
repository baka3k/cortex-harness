---
title: "C/C++ Clang containment and dual-plane hardening"
description: "Preserve Tree-sitter structure, restrict Clang to faithful-context semantic evidence, and prove safe FalkorDB publication and queries."
status: pending
priority: P1
effort: "4-6 engineering weeks plus external provider and scale canary time"
issue: null
branch: develop
tags: [cplus, clang, tree-sitter, semantic-coverage, falkordb, correctness]
blockedBy: []
phaseBlockedBy:
  "03": [260807-0929-mcp-ingest-query-concurrency]
  "04": [260807-1202-graph-ingest-write-path-hardening, 260807-0929-mcp-ingest-query-concurrency]
  "05": [260807-1202-graph-ingest-write-path-hardening, 260807-0929-mcp-ingest-query-concurrency, 260817-storage-backend-adapter]
blocks:
  - 260807-1329-parser-quality-recovery
  - 260821-1144-cplus-semantic-call-graph
created: 2026-08-24
mode: hi-plan --full
---

# C/C++ Clang containment and dual-plane hardening

## Outcome

Tree-sitter remains the sole structural payload; Clang protocol 2 adds call evidence only for proven-faithful TU/configuration contexts, and incomplete coverage never licenses a negative answer.

## Decision contract

- Disable every cross-backend whole-payload replacement, including legacy diagnostic-count fallback and bounded `repair` selection.
- Redefine `--parse-quality off` as Tree-sitter-only with quality artifacts disabled; same-backend Tree-sitter grammar retry remains allowed.
- Stop loading or writing legacy LIBCLANG structure caches and bump cache/policy identities.
- Fix the mutable extent-index bug, but do not expand the legacy adapter into a second structural parser.
- Migrate `Function` identity to signature-v2; Clang USRs join observations to an exact structural endpoint and never replace it.
- A successful whole-project build is not required; faithful flags, target, macros, headers, and generated inputs are required per TU.
- Only trusted, faithful, accepted, complete contexts may publish strict evidence; all others emit scoped noncoverage.

## Verified evidence

| Signal | Current result | Consequence |
| --- | --- | --- |
| Seven-fixture differential | Tree-sitter 13 calls/34 relations; legacy Clang 9 calls/0 relations | Total-row equality is invalid; Tree-sitter structure invariance is mandatory |
| Legacy fallback | Replaces payload when Clang diagnostics are fewer than Tree-sitter `ERROR` nodes | Remove immediately; the metrics are not comparable |
| Bounded recovery | Still selects a whole Clang payload and admits free mode | Cross-backend replacement must also be retired |
| Rollout baseline | 6/7 priority contexts faithful (85.7143%); live FalkorDB canary absent | Remain in containment below the 90% gate |

## Phases

1. [Containment and cache cutover](phase-01-containment-and-cache-cutover.md)
2. [Adapter correctness and differential contract](phase-02-adapter-correctness-and-differential-contract.md)
3. [Faithful-context dual-plane orchestration](phase-03-faithful-context-dual-plane-orchestration.md)
4. [Publication and coverage-aware query safety](phase-04-publication-and-query-safety.md)
5. [Provider canary, rollback, and promotion decision](phase-05-provider-canary-and-rollout.md)

## Ownership and dependencies

The parser-quality plan owns structural containment; the semantic plan owns protocol 2. Incremental sync plus `StoreGateway`/`GenerationManager` is the sole admission/publication owner. Phase 3 waits for concurrency ownership; Phases 4-5 also wait for their listed provider blockers. This plan blocks both parents' rollout completion.

## Acceptance gates

- After the signature-v2 clean-generation cutover, all semantic modes persist the same canonical Tree-sitter structural projection on one frozen horizon.
- No cache, worker, CLI policy, or recovery path can return a Clang structural winner.
- Later-function calls are retained; overload, pure-virtual, template, function-pointer, prototype, and missing-header cases have reviewed outcomes.
- Expected and actual scope-manifest keys match exactly; non-eligible contexts publish zero strict `CALLS` and one stable reason per TU/configuration.
- Any traversal under incomplete coverage is `partial`; empty results are authoritative only as policy-qualified negatives over an exact complete project/generation/revision/configuration scope.
- Direct precision is at least 98%, recall at least 95%, priority faithful-context coverage at least 90%, and weak-to-`CALLS` promotion is zero.
- Clean FalkorDB/Neo4j readback, deterministic rerun, context-loss downgrade, and rollback all pass before promotion.

## Non-goals

No full Clang AST parity, build-command execution, generated-header synthesis, per-node AST merge, LibTooling migration, or production-scale/Pro*C work already owned by other plans.

## Validation log

Scope is `HOLD`: all five requested remediation items are included and no parser/writer/scheduler is reinvented. `docs/development-rules.md` is absent; repository-local conventions were inferred from active plans and code. Unresolved provider/environment prerequisites remain explicit Phase 5 gates.

## Delivery command

After approval, implement with `$hi-craft plans/260824-1411-cplus-clang-containment-hardening/plan.md`.
