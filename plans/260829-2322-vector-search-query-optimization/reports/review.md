# Code review — 260829-2322-vector-search-query-optimization

Date: 2026-08-30 · Mode: hi-craft --full (review MUST) · 1 fix cycle (max 3)

Reviewer: independent subagent, read-only, full diff + new files against
plan constraints. Verified store adapters, qdrant-client 1.18.0 local-mode
behavior, test runs (54 new + 52 modified tests).

Verdict: **SCORE 7.6 — CRITICAL 1, MAJOR 1, MINOR 9.** All critical/major
and every actionable minor fixed in one cycle; suite re-run green
(1478 passed; only the 2 pre-existing `test_storage_lifecycle` infra-up
failures remain, failing identically on clean HEAD).

## Fixed

| # | Sev | Finding | Fix |
|---|-----|---------|-----|
| 1 | CRITICAL | Rebuild script deleted a stale `_rebuild_tmp` without `--yes` — could destroy the recovery copy kept after a failed mid-swap run | Dry-run now aborts on stale temp (`--yes` or manual removal required); 2 new tests |
| 2 | MAJOR | Preload defaulted `EMBED_DEVICE`→`"cpu"` while the query path auto-detects → double model load + first-query latency on MPS/CUDA hosts | Preload passes `None`/auto when env unset (4 backends); test asserts device="auto" |
| 3 | MINOR | Layout cache pinned empty `sizes` for a full TTL (reads as "no matching vector size") | Empty sizes are never cached |
| 4 | MINOR | Sync invalidation keyed by caller url could miss reader-side cache keys | `invalidate()` clears all (TTL repopulates) |
| 5 | MINOR | `_tuning_kwargs` unvalidated `int()` → raw traceback | Contextual ValueError |
| 7 | MINOR | Dead `_collect_vector_sizes`/`_select_vector_name` in 4 backends + missing blank lines | Removed; spacing normalized |
| 8 | MINOR | No end-to-end test of the output contract / lazy-fetch failure path | 4 new tests: narrowed hit preview + text dropped, `include_raw_fields` keeps fetched text, retrieve failure degrades to name fallback, preload auto-device |
| 9 | MINOR | `explore_service._make_embedder` loaded ST directly (2nd model copy in shared processes) | Routes through `embed_runtime.get_sentence_transformer` |
| 11 | MINOR | Stale test comment + `.cortext-harness` lock churn in diff | Fixed / reverted |

## Accepted (no change, with rationale)

- **#6** `MCP_SEARCH_TIMING` default-on + expand-bucket attribution: the
  default-on info line is the phase-01 plan decision (red team #11) and
  the benchmark depends on it; the expand bucket inherently contains the
  decorate/lazy-fetch work when measuring through the tool.
- **#10** benchmark harness caveats (env `setdefault`, logging teardown,
  stub patches only cplus): preload never runs in-process for the
  harness, stage capture is the documented contract, and dispatch
  resolves to cplus by production default (guard rails out of scope).
- **#4 note**: the latent sync-token/reader-token mismatch is fully
  closed by the invalidate-all change.
