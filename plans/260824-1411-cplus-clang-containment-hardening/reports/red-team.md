# Red-team review

## Review outcome

Three independent reviews initially returned **STOP** for Phases 3-5 and
**GO** for the immediate Phase 1 containment cutover. All critical/high design
findings below were accepted into the plan. The amended plan is a **conditional
GO** for phased implementation: Phase 1 may start; Phase 4 and Phase 5 remain
blocked by their declared runtime/provider dependencies and hard gates.

## Architecture and correctness dispositions

| ID | Severity | Challenge | Disposition and plan amendment |
| --- | --- | --- | --- |
| A1 | Critical | Name+arity structural IDs cannot represent same-arity overloads or accept USR endpoints safely | Accepted: signature-v2 Tree-sitter `Function` IDs, clean-generation migration, exact USR-to-structural join, ambiguous joins stay dangling (Phase 2) |
| A2 | Critical | A runtime “visited frontier” makes negative completeness circular | Accepted: immutable expected `SemanticScopeManifest`; exact expected/actual equality; default whole selected project/config domain (Phases 3-4) |
| A3 | Critical | Retaining a last-valid semantic generation can serve stale semantics after context loss | Accepted: publish current containment generation; old semantic snapshots are historical only and never relabeled current (Phases 4-5) |
| A4 | High | Compile database is multi-config, not first-command-wins | Accepted: TU/config multimap, deterministic dedup, working-directory/toolchain/dependency fidelity and explicit variant policy (Phase 3) |
| A5 | High | Fidelity, admission, and worker execution were conflated | Accepted: three independent axes; strict eligibility is faithful + accepted + complete + exact provenance (Phase 3) |
| A6 | High | Legacy payload/cache/graph/query states lacked one cutover matrix | Accepted: version/quarantine matrix plus clean Tree-sitter generation or refused activation (Phase 1) |
| A7 | High | One favorable build variant could create config-neutral truth | Accepted: observations stay per configuration; queries choose exact/union/intersection policy (Phase 4) |
| A8 | High | Analyzer/scheduler/publication ownership was ambiguous | Accepted: admitted incremental-sync job plus `StoreGateway`/`GenerationManager` is the sole owner (Phases 3-4) |
| A9 | High | Header changes and variant caps could create invisible gaps | Accepted: dependency reverse index, fan-out budget, and explicit pending/not-analyzed manifest keys (Phase 3) |
| A10 | Medium | Byte equality and count equality are unstable/weak gates | Accepted: canonical structural and provider projections/full digests (Phases 2, 4, 5) |
| A11 | Medium | Non-empty results were labeled complete despite incomplete coverage | Accepted: `result_state` is separate; positives under incomplete coverage are `partial` (Phase 4) |
| A12 | Medium | Effort and aggregate rollout metrics were optimistic | Accepted: 4-6 week estimate, minimum overall/per-cohort sample floors, and no averaging away cohort failures (plan, Phase 5) |
| A13 | High | Stable logical IDs were conflated with generation-scoped storage keys | Accepted after re-review: stable symbol/callsite logical IDs, physical `(project, generation, logical_id)` keys, generation-free cache/differential projections, separate isolation assertions (Phases 2-5) |
| A14 | High | Phase 1/2 provider migration and Phase 3 owner wiring did not match blockers | Accepted after re-review: Phase 1 ends at parser/cache incompatibility marker, Phase 2 emits versioned artifacts only, blocked Phase 4 owns clean provider migration, and Phase 3 now waits for concurrency ownership |
| A15 | High | Failed containment publication could leave an old semantic pointer serving as current | Accepted after re-review: monotonic desired-horizon/scope-epoch fence is durable before staging; default reads reject mismatch and historical access is explicit (Phases 4-5) |
| A16 | High | Shard closure and exact/union/intersection negatives were ambiguous | Accepted after re-review: first-release shards are always partial and a policy truth table defines exact, union, and intersection edge/negative semantics (Phase 4) |

## Security dispositions

| ID | Severity | Challenge | Disposition and plan amendment |
| --- | --- | --- | --- |
| S1 | High | Worker/context could self-assert “faithful” | Accepted: parent-side trusted-origin/freshness attestation; worker fields are untrusted (Phase 3) |
| S2 | High | A subprocess is not an OS sandbox | Accepted: low-privilege isolation, read-only roots, no network, private temp, cleared loader/Python environment, verified binaries/libraries, path containment and resource limits (Phase 3) |
| S3 | High | Semantic cache permits tampering, symlink and cross-project/revision reuse | Accepted: trusted per-project root, no-follow/permissions, locks, atomic rename+fsync, full provenance keys, integrity validation (Phase 3) |
| S4 | High | Graph identity/mutations were not project/generation bound | Accepted: scope every row, endpoint, mutation, deletion, constraint and readback; add two-project/two-generation tests (Phase 4) |
| S5 | High | Count-only readback outside the atomic boundary permits wrong-target publication | Accepted: exact target-bound set/digest readback under owner lock immediately before pointer flip (Phase 4) |
| S6 | High | Canary credentials/targets lacked enforceable production guard | Accepted: no argv secrets, protected secret source, TLS/allowlist, least privilege, nonce ownership marker, explicit authorization and production deny (Phase 5) |
| S7 | Medium | Macro flags and diagnostics may leak secrets | Accepted: structured define handling and secret-taint redaction across requests, errors, cache and reports (Phase 3) |
| S8 | Medium | Query caches could cross provider/physical target/schema/coverage boundaries | Accepted: complete target/generation/schema/config/scope key and atomic invalidation; negatives bind coverage digest (Phase 4) |

## Failure-injection disposition

Accepted all nine requested crash boundaries: manifest/enqueue, worker/cache
rename, journal/enqueue, provider mutation/ACK, stale delete/replacement,
graph/vector split, readback/pointer flip, pointer flip/ledger, and rollback
pointer. Phase 5 requires one visible generation, idempotent recovery, no
duplicates or mixed revisions, fail-closed reads, and deterministic ambiguous
mutation reconciliation at each boundary.

## Residual gates

- Phase 4 waits for graph-write-path and single-owner concurrency contracts.
- Phase 5 additionally waits for the storage backend adapter and provider
  capability matrix.
- Missing `neo4j`, an unreachable pilot revision, absent live-provider evidence,
  and the current 85.7143% faithful-context ratio are evidence gaps, not waived
  failures.
- OS sandbox or generation isolation unavailable means containment/noncoverage,
  never a degraded strict-semantic mode.
