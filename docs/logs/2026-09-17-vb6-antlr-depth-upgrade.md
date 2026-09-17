# VB6 ANTLR depth upgrade: plane hydration, controls/events, resolver precision — 2026-09-17

## Context

Plan `docs/plans/260917-1628-vb6-antlr-depth-upgrade/` (successor of plan 260917-1200, which
closed complete-with-exclusions). The 1200 plan landed an ANTLR-first VB6 engine, but the worker
was discarding most of what ProLeap already parsed: the six symbol planes (enums/constants/
events/declares/attributes) were serialized empty, `obj!Field` DICTIONARY_CALL rows were
whitelist-dropped, optional/ParamArray arity was flattened to exact, designer blocks were
blanked before `Attribute VB_Name`, and comments were dropped. All five phases (01–05) plus a
review cycle (lens=code, 7 findings: 0C/1H/6M, all fixed or dispositioned) completed same-day;
landed as commit `236dc4327ca9f081b03c6aa4d638a570d7ff3986` on develop. Scope stayed inside
`code-tiny/tools/vb/**` + `tests/**` — no schema/writer/MCP changes (plan AD-01).

## Change

- Worker plane hydration: enums/constants/events/declares/attributes serialized from ProLeap
  ASG getters + ctx walks; `functions[]` gained `min_arity`/`has_optional_args`/`has_paramarray`
  (`code-tiny/tools/vb/antlr_worker/worker/src/main/java/io/cortex/vb6/worker/Vb6Worker.java:560`);
  `controls[]` from designer ctx walk for .frm/.ctl/.pag
  (`Vb6Worker.java:468`); comment extraction from hidden-channel COMMENT tokens via
  `CommentIndex` (`Vb6Worker.java:1380`), note mirrors python `_build_note`.
- DICTIONARY_CALL emission from `DictionaryCallStmtContext` ctx walk
  (`Vb6Worker.java:660`, dedup on display+column at `:702`) — spike showed
  `getASGElement(ctx)` returns NULL for these (ProLeap never registers them).
- Adapter: keep-designer default in `materializeDesignerModule`
  (`code-tiny/tools/vb/vb6_antlr_adapter.py:57`), strip fallback behind
  `VB6_ANTLR_STRIP_DESIGNER=1` (`:54`) with `parse_meta.designer_stripped` observability (`:235`).
- Resolver: `VB6ModuleRegistry` seeds declared-API names from `payload["declares"]` → external
  (`code-tiny/tools/vb/vb6_resolver.py:90`, `:560`); interface-typed receiver dispatch via
  implements reverse map; exact-then-range arity checks (`:259`); predeclared-id default
  instances; dictionary branch (external/late_bound, never dropped).
- Analyzer: declares published through the functions lane `kind="declare"`
  (`code-tiny/tools/vb/vb_analyzer_base.py:709`); event wiring via `match_event_handlers`
  with `VB6_EVENT_SUFFIXES` (`:744`, `:728`); `default_member` prop on dictionary rows (`:1255`);
  Qdrant payload enriched with `vb6_event`/`vb6_control_type` + class controls summary
  (`:1424`).
- Cache: `PARSE_CACHE_VERSION` → `vb-family-v2026-09-17-4` (`code-tiny/tools/vb/vb_common.py:25`),
  per-phase bumps -2/-3/-4 per AD-05.
- Fixtures/tests: fixture corpus force-added to git (`tests/fixtures/vb6-application/`, 16 files)
  after being swallowed by the `tests/fixtures/**` ignore; new tests
  `tests/test_vb6_qdrant_payload.py` (fake-driver M3/M5) and `tests/test_vb6_engine_parity.py`
  (enums/constants/events symbol-id parity, declares ANTLR-only). 86 tests + 24 subtests green.

## Impact

- Affected: VB6 engine payloads change shape (new planes, arity/wiring fields on functions,
  dictionary call rows, comment text); Qdrant `vb_functions` embedding text enriched
  (comment-carrying notes); Neo4j gains declare-kind Function nodes via existing lane.
- Risk: **med** — payload shape change is the main blast radius; caches auto-invalidate via the
  version bump (`vb_common.py:25`), and regex fallback parity is pinned by the new parity test.
- Operators: re-sync existing VB6 corpora per
  `docs/plans/260917-1628-vb6-antlr-depth-upgrade/resync-runbook.md`. Guards if needed:
  `--vb6-parser-engine` CLI regex to pin engine choice, `VB6_ANTLR_STRIP_DESIGNER=1` to restore
  old strip behavior (controls[] then empty + `designer_stripped` flagged).
- Perf: within budget — worker-internal p95 8.6 ms (≤50 ms), JVM 156 ms (≤5 s) on 156-file
  corpus (`docs/plans/260917-1628-vb6-antlr-depth-upgrade/benchmark-report.md`).

## Decision

- Keep-designer default supersedes the old plan-1200 AD-03 (strip) with runtime evidence:
  designer blocks in materialized .cls parse OK with original line numbers; verified on a real
  99-line .frm (nested Frame→TextBox, BEGINPROPERTY). Strip kept only as env-guarded fallback.
- Dictionary rows emitted from parse ctx, not ASG registry: spike
  (`docs/plans/260917-1628-vb6-antlr-depth-upgrade/spike-plane-accessors.md`) proved ProLeap
  never registers `DictionaryCallStmtContext`; all other planes needed no regex-lite fallback.
- Dictionary rows are POSSIBLE_CALLS-only with `default_member` prop, no graph schema change —
  keeps AD-01 scope ruling and the pinned resolution vocab intact.
- Interface-first dispatch kept after reviewer challenge (R3 REJECTED): when `type_name` is
  `Implements`-ed, VB6 semantics say the variable is interface-typed, so dispatching via
  implementers is correct; concrete-type module remains the no-implementer fallback (plan 3.1).
- Exact-arity hits preferred over range (R2 fix) to avoid turning resolvable calls ambiguous —
  deterministic-resolution regression guard.
- Chained `obj!Field.Count` loses the dict row (ProLeap parse-recovery folds `!Field` into the
  members-call) — documented as vendored-grammar limitation; mitigated by analyzer setting
  `default_member` on any row whose callee contains `!` (R5).

## References

- Plan: `docs/plans/260917-1628-vb6-antlr-depth-upgrade/plan.md` (Close-out 2026-09-17; Review cycle)
- Commit: `236dc4327ca9f081b03c6aa4d638a570d7ff3986`
- Evidence: `docs/plans/260917-1628-vb6-antlr-depth-upgrade/spike-plane-accessors.md`,
  `docs/plans/260917-1628-vb6-antlr-depth-upgrade/benchmark-report.md`
- Runbook: `docs/plans/260917-1628-vb6-antlr-depth-upgrade/resync-runbook.md`
- Tests: `tests/test_vb6_qdrant_payload.py`, `tests/test_vb6_engine_parity.py`,
  `tests/test_vb6_antlr_worker_contract.py`, `tests/test_vb6_resolver.py`
