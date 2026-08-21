# C++ semantic compile-context, cache identity, and Pro*C manifests (Phase 03) — 2026-08-21

## Context

Phase 03 of the C++ semantic call-graph plan
(`./plans/260821-1144-cplus-semantic-call-graph/phase-03-context-cache-and-incremental.md`):
Clang semantics are valid only for a specific translation unit configuration,
so semantic coverage needed explicit context identity, dependency-aware
invalidation, bounded scheduling, and a Pro*C two-layer context model.

## Change

- `code-tiny/tools/cplus/semantic_context.py` (new): normalized
  compile-context registry keyed by (project, TU, config fingerprint) with
  faithful/synthetic/inherited coverage states and stable rejection reasons;
  bounded multi-variant selection (variant cap, explicit profiles, no
  implicit winner); bounded Clang `.d` dependency-manifest parsing and
  `ReverseInvalidationIndex`; lexical include-closure fallback;
  `SemanticCacheIdentity` covering source, dependency closure, compile
  context, working dir, target/sysroot/resource, toolchain, worker
  protocol/schema, policy, and Pro*C source-map version; bounded semantic
  scheduler with item/byte backpressure, cancellation checkpoints, non-yield
  circuit breaker (owner-resettable), reserved-CPU-share concurrency
  ceiling, latency/status metrics; baseline report builder.
- `code-tiny/tools/cplus/proc_manifest.py` (new): redacted Pro*C
  artifact/context manifest (original/generated/map sha256, language mode,
  precompiler fingerprint, exact-name credential redaction, EXEC SQL INCLUDE
  resolution), replacement-set dependency index matching real watcher paths
  (map path + header stem expansion), `proc_cache_fingerprint`, and
  semantic-complete/sql-only downgrade classification.
- Tests: `tests/test_cplus_semantic_context.py` (17),
  `tests/test_proc_semantic_manifest.py` (11). 28/28 pass; full suite shows
  no new failures vs the pre-change baseline.

## Impact

Semantic lane can now be scheduled incrementally with exact invalidation
instead of graph-query-only expansion. Risk: medium — scheduler admission is
a self-contained bounded lane; StoreGateway lane reuse is deferred to the
concurrency owner. Synthetic/inherited contexts fail closed for publication.

## Decision

- Synthetic contexts keep coverage identity but are auto-rejected for
  semantic publication (wrong conditional branch risk).
- SQLCHECK and other semantic precompiler options keep identity; only
  userid/user/password/pwd are credential-redacted (exact-name match). The
  sensitive-guard hook blocks a three-letter substring rule that matches the
  Oracle C-linkage option name; that option was dropped from the redaction
  list since Pro*C credential options are USERID/PASSWORD.
- Reviewer (score 5→fixed) caught a critical bug: lowercase include names
  were skipped as invalidation edges (`include.islower()` filter), silently
  defeating exact Pro*C invalidation; index edges now use real paths.

## References

- plan: ./plans/260821-1144-cplus-semantic-call-graph/phase-03-context-cache-and-incremental.md
- commit: 1903fb9
- prior: ./docs/logs/2026-08-21-cplus-semantic-worker-phase02.md
