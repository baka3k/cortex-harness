# Red-team review — VB6 ANTLR Depth Upgrade (260917-1628)

- Reviewer: isolated adversarial review worker
- Lens: `assumptions` + `failure-modes` (two passes, combined report)
- Date: 2026-09-17
- Artifacts reviewed: plan.md, phase-01..05, research-digest.md; predecessor plan
  260917-1200 (AD-01..AD-10); code under code-tiny/tools/vb/**; tests/test_vb6_*.py;
  fixtures/tests/fixtures/vb6-application/**; vendored ProLeap 3.0.0 sources; ProLeap grammar
  reference at /Users/hieplq1.test/proleap-vb6-parser-main (vendored copy used for line cites).
- Verdict: plan direction is sound and evidence-backed, but **5 High findings must be resolved
  before/during phase-01** — three of them (F1, F4, F11) will produce silent data loss or
  hydration TypeErrors that the plan's own gates would then mis-diagnose.

Severity scale: Critical | High | Medium. Every finding carries path:line evidence and a
one-line fix directive; dispositions are suggestions for the plan owner.

---

## Findings

### Assumptions lens

**F1 [assumptions][High] — Phase-01 inline plane shapes contradict the dataclass contract they cite.**
phase-01 1.2 specifies `enums[] = {symbol_id, name, module_name, file_path, line, members: [names], code}`,
`constants[] = {symbol_id, name, value?, type_name?, is_global, module_name, procedure_name?, file_path, line, code}`,
`events[] = {name, args, visibility, module_name, file_path, line}` — but hydration is into the
regex dataclasses, whose required (non-default) fields are different: `EnumDef` requires
`qualified_name, namespace_name, class_name, start_line, end_line, members` (vb_common.py:143-157),
`ConstantDef` requires `qualified_name, value, type_name, class_name, namespace_name, line_number, code`
(vb_common.py:160-174), `EventDef` requires `symbol_id, qualified_name, class_name, namespace_name,
start_line, end_line, parameters, code` (vb_common.py:110-124). `dataclass_from_payload` keeps only
known keys (vb_common.py:240-241), so following the plan's field lists verbatim raises
`TypeError: missing required argument` at hydration — the exact failure AD-03 warns about
("thiếu key bắt buộc sẽ raise"). The `line` key is also not a field (it is `line_number`), and
`members: [names]` loses the regex `(name, value)` tuple convention consumed by `asdict_enum`
(vb_common.py:1346). The new `Vb6DeclareRow` must additionally give every `?` key a dataclass
default or the worker must always emit it.
*Fix: derive the worker row shapes programmatically from the dataclasses in the phase-01 spike
(cross-check `dataclasses.fields`), not from the plan table; delete the inline field lists or
correct them.*

**F2 [assumptions][High] — symbol_id convention conflict makes the phase-05 parity gate unsatisfiable for enums/constants/events.**
Phase-01 1.2 emits enum/const/event ids as `<Mod>.<Symbol>@<rel>`, but the regex engine emits
bare-qualified ids for these planes because the post-loop extraction runs after the type stacks are
drained: `symbol_id = f"{qualified}@{rel_path}"` with `qualified == name` for module-level symbols
(vb_common.py:989-993 enums, :1028-1032 constants, :895-899 events; digest §1 notes "effectively
null"). Phase-05 5.2 requires "mọi plane có tiền lệ regex ... phải cho cùng SET `symbol_id`" —
under the plan's shapes the two engines produce disjoint id sets for the same fixture symbol
(`modMain.E@modMain.bas` vs `E@modMain.bas`). Qdrant point ids are uuid5 over symbol_id
(vb_analyzer_base.py:66-67), so the same logical symbol would also flip point id depending on
engine.
*Fix: match the regex convention (bare `<Name>@<rel>` — safe, since @rel_path disambiguates files)
or amend gate 5.2 to name-based coverage; state the chosen convention in AD-03.*

**F3 [assumptions][High] — Single cache bump at phase-01 leaves a mixed-version window for phase-02/04 payload changes.**
AD-05 bumps `PARSE_CACHE_VERSION` once ("merge payload-đổi đầu tiên"), but phase-02 changes the
payload shape again (`controls[]`, `vb6_event`, class-row `controls` in Qdrant) and phase-04 fills
`comment/summary/note`. Cache acceptance is exact-string equality (vb_analyzer_base.py:165, 400,
594, 841), so caches written by phase-01-era code keep validating after phase-02/04 merge and the
new planes/comments never appear for unchanged files — the exact "cache cũ che payload mới" failure
AD-05 exists to prevent. This also contradicts the plan's own evidence base (digest convention 3:
"Any payload-shape change ⇒ bump PARSE_CACHE_VERSION"). The exact-string test pins must move on
every bump: tests/test_vb6_engine_dispatch.py:35-39 **and the duplicated local constant**
tests/test_vb6_antlr_worker_contract.py:27 (not mentioned in phase-01 1.4).
*Fix: schedule a `PARSE_CACHE_VERSION` bump (and both test-pin updates) in every
payload-changing phase (01, 02, 04), or run the plan as one merge with a single bump.*

**F4 [assumptions][High] — Hydration boundary silently strips the new function-row fields the later phases depend on.**
Phase-01 1.3 adds `min_arity/has_optional_args/has_paramarray` to worker `functions[]` and phase-02
2.3 attaches `vb6_event/vb6_control_type`, but `FunctionDef` has no such fields (vb_common.py:27-46)
and `dataclass_from_payload` drops unknown keys (vb_common.py:240-241). The analyzer consumes
hydrated dataclasses (`_hydrate_payload` vb_analyzer_base.py:301-332; `resolve_vb6_calls` receives
`payload["functions"]` dataclasses at vb_analyzer_base.py:891-894), so phase-03's arity range check
(3.2) would find no `min_arity` and silently degrade to exact-arity, and phase-02's Qdrant payload
mapping (vb_analyzer_base.py:1258-1285, explicit key list) could not carry `vb6_event`. The digest
already flags this trap (§2 caveat: "new payload keys must come from dataclass fields ... or be
added as parse_meta-derived extras inside the analyzer") but neither phase task mentions extending
`FunctionDef`.
*Fix: phase-01 adds defaulted `min_arity/has_optional_args/has_paramarray` fields to `FunctionDef`
(and phase-02 adds `vb6_event/vb6_control_type`), or specifies an analyzer-side attach point that
runs post-hydration; add a hydration round-trip assertion to the contract test.*

**F5 [assumptions][Medium] — declares[] has no publication destination and the plan doesn't say so.**
Declares have no regex precedent (vb_common.py:419-423 omits `Declare`) and no writer lane: the
Neo4j writers are fixed-SET and out of scope (digest §2), and there is no `asdict_declare`/node-lane
in vb_analyzer_base.py:987-1069. Phase-01 1.4's "cho planes mới điền qua node-row builders hiện có"
is therefore false for declares — they can only live in the parse payload plus a new Qdrant embed
block. M1 is payload-level so it passes, but the deliverable is ambiguous and an implementer may
waste a cycle trying to publish declares as graph nodes (which would also violate AD-01).
*Fix: state explicitly "declares = payload + Qdrant embed only, no graph node"; add the Qdrant
declare block to phase-01 1.4 or descope declares from M1.*

**F6 [assumptions][Medium] — `has_paramarray` has no ASG accessor and `min_arity` as specified miscounts ParamArray.**
AD-04's spike list covers events/declares/attributes, but phase-01 1.3 also assumes
`has_paramarray` from "Procedure.java:38-42" — that interface only exposes
`getArgsList/getOptionalArgs/hasOptionalArgs` (vendor Procedure.java:32-42); `Arg`/`ArgImpl` carry
no ParamArray flag (Arg.java:38 is the only flag), the token lives on the parse ctx
(VisualBasic6.g4:714 `(OPTIONAL WS)? ((BYVAL|BYREF) WS)? (PARAMARRAY WS)?`). Worse, a ParamArray arg
is not `OPTIONAL`, so "min_arity = số arg không optional" counts it as required:
`Sub Foo(a As Integer, ParamArray r() As Variant)` gets `min_arity=2`, and the phase-03 gate
`args >= min_arity` (3.2) then wrongly rejects the legal call `Foo 1`.
*Fix: add the paramarray ctx check to spike 1.1 and define `min_arity = argsList.size() − optional −
paramarrayArgs`.*

**F7 [assumptions][Medium] — Phase-04 misstates the regex engine's comment behavior.**
Phase-04 4.3 says the regex path "đã extract comment riêng — parity tự nhiên ... chỉ assert
non-empty". False: the regex engine hardcodes `comment=""` on every plane (vb_common.py:615-617,
673-675, 876-878, 913-916, 1008-1010, 1046-1048); only `FileDef.comment` is real, via the leading
`'` header block (vb_common.py:252-264, used at :1100-1109). An M5 "non-empty" assertion scoped to
the regex path fails for every declaration; scoped to file_def it is trivially true today.
*Fix: correct the claim; assert M5 only on the ANTLR path and assert regex `comment==""` unchanged
(or descope regex comment parity explicitly, per digest risk 8).*

**F8 [assumptions][Medium] — Phase-03 predeclared-id delta is overstated; the flagship idiom may already resolve today.**
vb6_resolver.py:286-296 already resolves a qualified member on a form/cls module when the member
exists (`module.functions_named(member)` → `asg_resolved`), regardless of kind or predeclared-id;
the kind-based `external` classification fires only when the member is missing (vb6_resolver.py:297-298).
So 3.3's "kể cả caller ở module khác; không còn rơi external" changes almost nothing for
member-exists cases, and the M4 "predeclared-id cross-module call" golden idiom would show
`asg_resolved` even without the phase — risking a mismeasured acceptance and a redundant
expected.json entry. Also `"VB_PredeclaredId" == "True"` is fragile: `Literal.getValue()` is raw ctx
text (ScopeImpl.addLiteral, vendor ScopeImpl.java:1469-1481 — `ctx.getText().replace("\"","")`), so
capitalization is file-derived; compare case-insensitively on both name and value.
*Fix: redefine 3.3's delta as (a) intrinsic-member set gating and (b) predeclared-id gating of the
external classification; pick a golden idiom whose status actually changes; compare attributes
case-insensitively.*

**F9 [assumptions][Medium] — The "closed" event suffix set is not closed; type-prefixed handlers are structurally unmatched.**
Q3's set omits `Timer` (`Timer1_Timer` — pervasive in VB6 UI code) and spells `MouseMove` as "Move"
(so `Form_MouseMove` matches neither `Move` nor `MouseMove` under the `^(.+)_(SUFFIX)$` regex).
Structurally, `UserControl_Initialize` / `MDIForm_Resize` handlers in .ctl/MDI forms have no
control named `UserControl`/`MDIForm` in `controls[]` (the designer names instances, e.g.
`UserControl1`), and only `Form` is special-cased — so .ctl event wiring yields 0% and M3's ≥90%
gate is only reachable with a cherry-picked fixture. The guards themselves are sound: menu arrays
resolve via recursive controls[] + longest-control-name-first; `Command1_Clicked` is blocked by the
suffix set; `Class_Initialize` blocked because `Class` ∉ controls[]; .bas handlers blocked because
.bas has no controls plane; same-named controls on different forms are module-scoped.
*Fix: add a type-alias prefix set (`Form/UserControl/MDIForm/PropertyPage`) plus the missing
suffixes (`Timer`, `MouseMove`, `OleCompleteDrag`, ...) and say so in Q3; include a .ctl handler in
the M3 fixture.*

**F10 [assumptions][Medium] — Hydration wiring for the new planes is unspecified and is the linchpin of phases 01/02.**
`_hydrate_payload` builds a fixed-key dict (vb_analyzer_base.py:317-332); unless it learns
`declares`/`controls` keys, worker-emitted rows vanish before `match_event_handlers(controls,
functions)` (phase-02 2.3) or any consumer sees them. Downstream must also use `payload.get(...)`
because regex-path payloads and the fake worker in tests/test_vb6_engine_dispatch.py:116-135 carry
no new keys (`_payload_from_parsed` is a fixed-key dict, vb_analyzer_base.py:283-298). Phase-05 5.2
only verifies tolerance at the very end.
*Fix: add explicit tasks: extend `_hydrate_payload` with the new plane keys (`.get` with defaults),
and grep all new consumers for `payload["<plane>"]` direct indexing.*

### Failure-modes lens

**F11 [failure-modes][High] — Keep-designer breaks module lookup for real .frm whose designer block exceeds 60 lines.**
The worker identifies each file's module by scanning only the first 60 lines of the parse content
for `Attribute VB_Name` (`Math.min(lines.length, 60)`, Vb6Worker.java:963-977) and then looks the
module up in the Program by that name (Vb6Worker.java:311). ProLeap registers the ASG module under
the VB_Name parsed from the full tree (`analyzeDeclaredModuleName` → `effectiveModuleName`,
VbParserRunnerImpl.java:223-237; VbModuleNameAnalyzerVisitorImpl walks the whole ctx). Today the
adapter blanks everything before VB_Name, so it is always near the top of the materialized content
(vb6_antlr_adapter.py:47-78). With AD-02 keep-designer as default (phase-02 2.1 "copy nguyên file"),
any real form with a designer block >20-ish lines plus header (common: menus, OCX, grid
BeginProperty blocks push VB_Name past line 60) makes `declaredModuleName` fall back to the file
stem; if stem ≠ VB_Name, `lookupModule` misses → `fileError("module not registered after parse")` →
**the whole .frm loses procedure extraction** — a regression versus today's strip behavior. The
experiment in digest §5 used a 5-line designer block and could not surface this.
*Fix: under keep-designer, scan the full content (or scan from both ends / take the LAST match) for
VB_Name in `declaredModuleName`, and add a >60-line-designer fixture to the phase-02 golden set.*

**F12 [failure-modes][Medium] — Keep-designer widens the blast radius of the batch-retry exclusion path.**
With designer content retained, `countSyntaxErrors` runs on the full designer
(Vb6Worker.java:218, 993-1018). Any designer construct the grammar mishandles on a real-world .frm
(OCX-specific property shapes beyond the experiment's `Caption`) sets `syntaxErrors > 0`; if the
batch then fails for an unrelated reason, ALL such files are excluded from the retry
(Vb6Worker.java:246-260) and cascade to regex, and a designer-erroring form with zero procedures is
dropped outright (Vb6Worker.java:323-327). Under strip, .frm files were shielded from exactly this.
The plan's flip criterion for AD-02 is only "golden line-number fail" (plan Risks table);
parse-success/has_error metrics are not part of the decision.
*Fix: extend the phase-02 golden .frm corpus with nested + BeginProperty + OCX-reference + menu
forms, and gate the keep-designer default on ok-rate and `has_error` counts, not only line numbers;
keep the `VB6_ANTLR_STRIP_DESIGNER=1` guard.*

**F13 [failure-modes][Medium] — The AD-02 strip fallback silently disables controls[] and event wiring.**
controls[] is extracted from the parsed designer (phase-02 2.2); under `VB6_ANTLR_STRIP_DESIGNER=1`
(the documented rollback, phase-05 5.4) the designer is blanked again, so `controls[]` is empty and
`match_event_handlers` annotates nothing — with no warning anywhere. Phase-02 2.4 runs golden line
checks "cả hai mode" but the controls/wiring contract assertions only hold in keep mode, and
nothing tells the operator that the rollback regresses two features, not one.
*Fix: in strip mode, emit an explicit `[vb6][engine] designer stripped: controls[]/event wiring
disabled for N files` log line and record it in the runbook; make the controls contract test
env-aware.*

**F14 [failure-modes][Medium] — Extending the existing fixture form shifts a wide, unenumerated pin surface.**
Phase-02 2.1 extends the *existing* `frmMain.frm` designer with nested controls; that moves
`Form_Load` start_line (currently 31) **and every frmMain callsite line** in expected.json (33, 35,
37, 42, 45, 47, 51, 53, 58, 62 — plus `start_line: 31/56/61`), which the golden matcher checks with
line tolerance ±1 (test_vb6_golden.py:65-72), and the contract pin at
tests/test_vb6_antlr_worker_contract.py:101. Phase-02 names only "mở rộng pin contract:101" — an
implementer following it literally breaks M2 recall, zero-drop and special-status tests wholesale.
*Fix: either add a NEW dedicated .frm fixture for nested-control coverage (leaves all existing pins
stable), or enumerate in the task every file/line pin that must move: expected.json frmMain block,
contract:101, test_vb6_baseline helpers.*

**F15 [failure-modes][Medium] — Phase-05 parity gate includes `properties`, which no phase hydrates.**
The `properties` plane stays `[]` in the worker (Vb6Worker.java:441) and phase-01 fills only
enums/constants/events/declares — yet the plan's own Overview table counts properties among the
"6 plane luôn rỗng", the fixture has `Property Get/Let Total` (tests/fixtures/vb6-application/clsOrder.cls),
and the regex engine emits PropertyDef rows for them. Phase-05 5.2 demands the same `symbol_id` SET
for "enums/constants/events/properties" → guaranteed failure (or silent gate-weakening by excluding
properties ad hoc).
*Fix: hydrate properties[] from `getPropertyGets/Lets/Sets()` (cheap, accessors verified,
Vb6Worker.java:403-411 already enumerates them) in phase-01, or strike "properties" from gate 5.2
and document the asymmetry.*

**F16 [failure-modes][Medium] — New worker walks must stay inside filePayload's per-file try or one NPE fails the whole workspace.**
Per-file serialization is individually guarded (try/catch → `fileError`, Vb6Worker.java:328-334),
but anything placed in `analyze()` outside that loop (module_attributes collection, implements
scan changes, comment indexing) runs under the single `future.get` — any Throwable there produces
`allFailed` for every file (Vb6Worker.java:162-171), i.e. the whole project cascades to regex
(predecessor R5 semantics: workspace timeout/batch failure is all-or-nothing). The plan mandates
AD-04 spike verification but never states the placement/guard requirement for the new ctx walks,
nor per-row null-tolerance for `Attribute.getLiteral()`, `module.getCtx()`, `procedure.getCtx()`.
*Fix: add an explicit implementation rule to phase-01/02: all new extraction helpers are invoked
from inside `filePayload`'s try (or individually try-guarded) and are null-tolerant per row.*

**F17 [failure-modes][Medium] — Dictionary-call emission has no noise rule scheduled, despite the digest requiring one.**
Phase-01 1.3 emits **all** DICTIONARY_CALL rows (M2 "0 drop") and phase-03 3.4 never drops them —
but the digest's risk 3 explicitly demands a dedup/noise rule (e.g. the existing moduleNames
receiver filter, Vb6Worker.java:632-634) because `obj!Field` access is pervasive in real VB6.
Unfiltered emission inflates POSSIBLE_CALLS, the `[vb6][summary]` possible count, and creates many
`external::vb6/<member>` placeholder Function nodes (vb_analyzer_base.py:680-683, 1089-1092) that
the predecessor's Q7 reconciliation will then keep alive (each has an edge). Golden tests still
pass (extra rows don't hit the no_call trap), so the regression ships silently.
*Fix: apply the module-name/self-receiver filter at emit time in phase-01, or add an explicit
accepted-noise budget + placeholder-count assertion to M2/phase-05 benchmark.*

---

## Coverage (checked and cleared)

Attack surfaces from the mission that were verified and did **not** yield findings:

1. **AD-02 grammar ordering** — `module : ... controlProperties? NEWLINE* moduleConfig? NEWLINE*
   moduleAttributes? ...` (VisualBasic6.g4:29-31) matches real .frm order (designer lines 1-20,
   `Attribute VB_Name` line 21 in tests/fixtures/vb6-application/frmMain.frm). `Object = "{GUID}#..."; "OCX"`
   references are handled by `moduleReferences` (g4:33-37), `FRX_OFFSET` by `cp_SingleProperty`
   (g4:105), GUIDs by `cp_NestedProperty` (g4:117-119), negative/hex literals by signed
   INTEGERLITERAL (g4:2033) and `literal` (g4:786-796). `implementsNames(lines)` (Vb6Worker.java:979-991)
   is unaffected by kept designer lines (`^Implements` never appears in designer blocks);
   `_VB_NAME_LINE_RE` (vb6_antlr_adapter.py:35) is used only by the strip path. A designer
   property line reading `Attribute VB_Name` is a lexical impossibility (`ATTRIBUTE` is a keyword
   token, not a valid `cp_PropertyName` head), so the first-match hazard is contrived — residual
   risk is covered by F12's real-file gate.
2. **AD-04 attach semantics** — `Declare` is a `moduleBodyElement` (g4:75) in both .bas and .cls,
   module-scoped; the per-procedure call collection (Vb6Worker.java:550-584) is unaffected by
   module-level declares. `Attribute VB_PredeclaredId` is visited for every module kind
   (VbDeclarationVisitorImpl.visitAttributeStmt → ModuleImpl.addAttribute, ModuleImpl.java:129-143);
   the internal attribute map is case-insensitive via `getSymbol` lowercase
   (ScopedElementImpl.java:60-62). Remaining gaps were filed as F6/F8.
3. **Event wiring guards** — cleared cases listed under F9 (menu arrays, `Command1_Clicked`,
   `Class_Initialize`, .bas handlers, cross-form same-name controls, control-array `Index`).
4. **Arity semantics** — `getArgsList()` includes optional args (ProcedureImpl.addArg adds every
   arg, ProcedureImpl.java:60-74), so `arity` = TOTAL and phase-03's `args <= arity` max-bound is
   **correct**; `arity` itself stays untouched (phase-01 1.3), so the pinned ids
   `modUtil.CalcTotal/2@modUtil.bas` (contract test :113) and the property get/let 0/1 arity pins
   (test_vb6_golden.py:97-102) do not move. Residual ParamArray defect filed as F6.
5. **Predeclared-id retention** — `materializeDesignerModule` blanks only lines *before* the first
   `Attribute VB_Name` (vb6_antlr_adapter.py:61-70), so `VB_PredeclaredId = True` (frmMain.frm:24)
   survives materialization in both modes. Attribute literal value is raw token text ("True") —
   see F8 for the casing caveat.
6. **Worker batch semantics** — per-file `filePayload` failures are contained
   (Vb6Worker.java:328-334); per-file regex fallback for missing/invalid payloads is exercised by
   tests/test_vb6_engine_dispatch.py:89-159; `_is_valid_payload_shape` (vb_analyzer_base.py:369-374)
   requires only the 5 original keys, so new planes are optional there. Placement gap filed as F16.
7. **Scope discipline (M7)** — the embedding loop really lives in
   `vb_analyzer_base.py:1249-1497` (in-scope); node lanes/index specs already cover
   Event/Enum/Constant (vb_analyzer_base.py:934-945, 1041-1069); Qdrant payload keys are built
   in-analyzer and pass through `enrich_project_scope` untouched; the 13 external touchpoints list
   in digest §6 checks out (spot-checked `language_writer` SET lists, `callsite_site_id`,
   `enforce_strong_call_row`, provider-wiring source test). No planned edit requires files outside
   `code-tiny/tools/vb/**` + `tests/**` + plan docs. Phase-05 5.5's audit is adequate.
8. **symbol_id/point-id collisions** — new plane ids (`<...>@<rel>`, `Vb6DeclareRow`, `external::vb6/<name>`)
   are uuid5-distinct from Function `/arity@` ids (vb_analyzer_base.py:66-67); placeholder merges
   by name are intentional. Convention conflict across engines is F2.
9. **Test pins that survive** — `start_line==31` holds under keep-designer (1:1 line preservation
   verified against frmMain.frm: designer occupies 1-20, `Private Sub Form_Load()` at 31, exactly
   what the strip path pads to); resolver unit pins (tests/test_vb6_resolver.py) survive phase-03
   because the fallback path keeps exact-arity when `min_arity` is absent; graph-contract vocab
   (test_vb6_graph_contract.py:128-132) is safe because `resolve_vb6_calls` re-derives statuses
   before publication (vb_analyzer_base.py:893-894), including dictionary rows (their bare-member
   names fall to `unresolved`), before phase-03 refines them.
10. **Comment/line-math interaction** — hidden-channel COMMENT tokens carry original line numbers;
    the attach heuristic (above-block/same-line) cannot shift `start_line`, and frmMain has a blank
    line above Form_Load, so no pin interaction (digest risk 4 confirmed).

## Suggested disposition summary

| Finding | Severity | Suggested disposition |
|---|---|---|
| F1 | High | Fix phase-01 shape tables from dataclasses (blocking, phase-01 task 1.2) |
| F2 | High | Decide id convention; align 1.2 and gate 5.2 (blocking, phase-01/05) |
| F3 | High | Per-phase bump or single-merge strategy + both test pins (plan AD-05 edit) |
| F4 | High | Extend FunctionDef or define analyzer attach point (phase-01/02 tasks) |
| F11 | High | Fix declaredModuleName scan + >60-line designer fixture (phase-02 task 2.1) |
| F5 | Medium | Declare publication story explicitly (plan edit) |
| F6 | Medium | Spike paramarray; fix min_arity formula (phase-01 1.3 edit) |
| F7 | Medium | Correct regex-comment claim (phase-04 4.3 edit) |
| F8 | Medium | Rescope 3.3 delta + case-insensitive compare (phase-03 edit) |
| F9 | Medium | Extend suffix set + type-prefix aliases (Q3 edit + M3 fixture) |
| F10 | Medium | Add _hydrate_payload wiring tasks (phase-01/02 edits) |
| F12 | Medium | Widen AD-02 flip criteria beyond line numbers (phase-02 gate) |
| F13 | Medium | Observable strip-mode degrade (phase-02/05 task) |
| F14 | Medium | New fixture form OR enumerated pin-move list (phase-02 2.1/2.4) |
| F15 | Medium | Hydrate properties or strike from 5.2 (phase-01/05 edit) |
| F16 | Medium | Placement/guard rule for new walks (phase-01/02 implementation rule) |
| F17 | Medium | Dictionary noise filter or accepted-noise budget (phase-01 edit) |
