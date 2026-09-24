# Research digest — VB6 anchor/graph coverage upgrade (260924-1434)

Scope under research: make 4 user-defined anchor groups (procedure graph, type
graph, event graph, state graph) captured and traceable end-to-end in the VB6
pipeline, plus the headline complaint that form actions flowing down to UI
components (button click handler → module calls → DB) are not fully traceable.

Method note: every claim carries a `path:line` reference or an explicit
`unknown`/`inferred` marker. Stack verified against the working tree at
2026-09-24. Corpus spot-checks against
`/Users/hieplq1.aip/test/Bookworm-VisualBasic/Revamp1` (27 .frm, 3 .bas, 1
.vbp, 0 .cls).

---

## 0. Pipeline map (where an anchor must live, stage by stage)

| Stage | File | Evidence |
|---|---|---|
| 1. Parse + plane serialization (Java) | `code-tiny/tools/vb/antlr_worker/worker/src/main/java/io/cortex/vb6/worker/Vb6Worker.java` — `filePayload` | `Vb6Worker.java:390-527` |
| 2. Worker invocation / .frm materialization | `code-tiny/tools/vb/vb6_antlr_adapter.py` | `vb6_antlr_adapter.py:57-94, 182-321` |
| 3. Payload hydration (dataclass contract) | `code-tiny/tools/vb/vb_analyzer_base.py` `_hydrate_payload` | `vb_analyzer_base.py:330-334` (`declares` dataclass, `controls` stays raw dicts); unknown keys dropped at `vb_common.py:267-276` |
| 4. Resolver (project model) | `code-tiny/tools/vb/vb6_resolver.py` `resolve_vb6_calls` | `vb6_resolver.py:306-575` |
| 5. Event wiring annotation | `vb_analyzer_base.py` `match_event_handlers` | `vb_analyzer_base.py:728-791` |
| 6. Graph row building | `code-tiny/tools/vb/vb_common.py` `asdict_*` | `vb_common.py:1206-1453` |
| 7. Two-tier edge publication | `vb_analyzer_base.py` | CALLS/POSSIBLE_CALLS loop `:1203-1269`; write_all `:1351-1367`; POSSIBLE_CALLS `:1369-1370` |
| 8. Schema (node/rel specs) | `code-tiny/tools/graph/schema/ladybug_schema.py` | `_NODE_SPECS` `:184-266`, `_REL_SPECS` `:304-402`, generic fallback `:420-437, 440-452` |
| 9. Writers | `code-tiny/tools/graph/writer/language_writer.py` | `write_relations_typed` `:1717+`; `write_possible_calls_with_site` `:2164+`; node SET lists are fixed (prior digest 260917-1628 §2) |
| 10. Qdrant payload (explicit mapping) | `vb_analyzer_base.py` | `:1394-1560` |

Worker payload planes emitted today (`Vb6Worker.java:407-520`): `functions`,
`calls`, `classes`, `interfaces`, `namespaces` (always empty), `relations`
(always empty), `properties` (always empty), `events`, `enums`, `constants`,
`declares`, `controls` (designer files only, `:469`), `variables`,
`file_def`, `parse_meta` (incl. `module_attributes`, `implements`,
`designer_stripped`, `module_name`), `parse_cache_version`. Worker-level
`worker_meta.implements_map` at `:360`.

---

## Findings

### Group 1 — procedure graph

**1.1 procedure_decl: CAPTURED.** Every Sub/Function/Property Get|Let|Set in a
module becomes a `functions[]` row with
`symbol_id = Module.Name/arity@rel_path` (`Vb6Worker.java:549-569`,
`symbolId` at `:599-602`), `module_name`, `is_private` (`:545, 568`),
`min_arity`/`has_optional_args`/`has_paramarray` (`:560-562`). Published as
`Function` nodes via `asdict_function` (`vb_common.py:1206-1230`). Fan-out
"who calls X corpus-wide" is a reverse traversal of `CALLS`/`POSSIBLE_CALLS`
Function→Function rels (`ladybug_schema.py:306-307`) — both are in
`_REL_SPECS`, so caller lookup works once edges exist. Catch: `asdict_function`
hardcodes `exported: False` and drops `module_name`/`is_private` from the row
(`vb_common.py:1223` — only keys in the dict survive; the writer SET list is
fixed), so the graph Function node does NOT carry public/private — see 1.4.

**1.2 procedure_call (call sites, fan-in, `Call x`, implicit calls):
CAPTURED.** Worker walks each procedure's parse tree, resolves each call ctx
through the ASG registry, and emits rows for `CALL_TYPES_EMITTED` =
SUB_CALL/FUNCTION_CALL/PROPERTY_GET|LET|SET_CALL/UNDEFINED_CALL/API_PROCEDURE|PROPERTY_CALL
(`Vb6Worker.java:94-103`, walk at `:610-653`, emission `:732-882`).
Implicit (paren-less) calls and `Call x` are handled: ProLeap parses them as
explicit/implicit call stmts and the display builder strips a leading `Call`
(`callDisplayName`, `Vb6Worker.java:901-922`); plan 260917-1200 M1 gated
exactly these idioms. Rows carry `caller_id`, `callee_name`, `callee_id`
(when ASG-bound), `call_line`, `site_column`, `call_type`,
`resolution_status` (`:862-881`) — pinned by
`tests/test_vb6_antlr_worker_contract.py:58-74`. Publication: deterministic
targets → CALLS, everything else survives as site-keyed POSSIBLE_CALLS
(`vb_analyzer_base.py:1203-1269`), zero-drop by design.

**1.3 module_qualified_call (`Module.Member` cross-module edge): CAPTURED.**
The worker emits the receiver chain in `callee_name` ("modUtil.CalcTotal",
`Vb6Worker.java:901-940`); the resolver's receiver branch looks up
`registry.modules.get(receiver_key)` → `functions_named(member)` and sets
`callee_id` to the target symbol id (`vb6_resolver.py:391-412`), producing a
cross-module CALLS edge. The module key is `Attribute VB_Name`, scanned over
the WHOLE file content (`Vb6Worker.java:1537-1554`) and pinned as the
project model in the registry (`vb6_resolver.py:118-155`). Predeclared-id
receivers (`login.Init` where login is a Form) resolve via the
`kind in {"frm","ctl","pag"} or predeclared` branch
(`vb6_resolver.py:402-412`; `vb_predeclaredid` read at `:136-139`) — corpus
evidence: `mainmenu.frm:121-124` `cmdLogin_Click` → `login.Init`, and
`login.frm:172` `Attribute VB_PredeclaredId = True`.

**1.4 public_surface (Public decls as interop entry points): CAPTURED in
payload, DROPPED at publication.** `is_private` is serialized
(`Vb6Worker.java:545, 568`), consumed by the resolver
(`public_functions`/`public_by_name`, `vb6_resolver.py:86-87, 179-184`) and by
the private-cross-module check (`:397-398`). But neither the graph Function
row (`asdict_function` has no is_private; `exported` hardcoded False,
`vb_common.py:1206-1230`) nor the Qdrant function payload
(`vb_analyzer_base.py:1410-1431` — no public flag) preserves it. The Function
node spec already HAS an `exported` column
(`ladybug_schema.py:196-201`), so publishing public surface needs only
`asdict_function` to write `exported: not fn.is_private` — no schema, no
writer change.

**Group-1 insertion points:** public_surface → `vb_common.py:1223` only.
Everything else in group 1 needs no new anchor, only consumers.

### Group 2 — type graph

**2.1 type_decl (`As <TYPE>`): PARTIAL.** `variables[]` carries `type_name`
for module-level AND procedure-local variables, with `procedure_name` on
locals (`Vb6Worker.java:1005-1062`); published as Variable nodes with
`type_name` (`asdict_variable`, `vb_common.py:1429-1453`; Variable spec
`ladybug_schema.py:212-213`). NOT captured: (a) parameter types — args are
not in `variables[]` (grammar `arg` has `asTypeClause`,
`VisualBasic6.g4:714`); (b) function return types — `procedureJson` emits no
`return_type` key (`Vb6Worker.java:549-569`); (c) no procedure→type edge —
`USES`/`USES_TYPE` Function→Type rels exist in `_REL_SPECS`
(`ladybug_schema.py:384-386`) and are unused by the VB6 path.

**2.2 com_type_ref (`As ADODB.|DAO.|RDO.|Scripting.X` → instances + member
access): NEVER PARSED as an anchor.** No COM-namespace extraction anywhere in
worker/adapter/resolver/analyzer. Typed instances would only surface as a raw
`type_name` on a Variable row ("adodb.recordset"); the resolver's typed-
receiver branch (`vb6_resolver.py:423-469`) finds no implementers and no
project module for a COM type, so member access falls to generic
late_bound/unresolved or an arbitrary ASG keep. Member-access sites DO exist
as call rows with receiver chains (`callee_name` "x.Member", worker display
`:901-940`), so a Python-side derivation (variables with COM-prefix type_name
+ call rows whose receiver matches those variables) is possible without
worker changes — `inferred`, not exercised. Corpus: 0 typed `As ADODB./DAO./
RDO./Scripting.` declarations; COM usage is late-bound
`Set fso = CreateObject("Scripting.FileSystemObject")`
(`superadmin_deletedb.frm:95`, `main.bas:131`) — the corpus cannot exercise
the typed flavor; fixtures must be synthetic.

**2.3 class_instantiation (`New <CLS>`): NEVER CAPTURED — explicitly
filtered out.** `New clsOrder` style bare references are dropped as
"not procedure calls" via the moduleNames filter
(`Vb6Worker.java:303-309, 767-771`): every ASG module name (incl. class
modules) is in `moduleNames`, so a `New X` member reference never becomes a
row. Grammar parses all flavors: `AS (NEW)? type`
(`VisualBasic6.g4:736-737`), `NEW valueStmt` in valueStmt (`:569` vsNew),
`WITH (NEW)? target` (`:620-621`), `Set x = New X` (`:529-531` setStmt).
Insertion: a worker ctx-walk (vsNew contexts + `asTypeClause` with NEW) is
the natural plane; alternatively `Set x = New X` rows can be derived in
Python only by regex (no payload support today). Edge carrier: `USES`
Function→Type / Class→Type already specced (`ladybug_schema.py:384-386`).

**2.4 with_block_target (`With <EXPR>` → `.Member` maps to target): TARGET
DROPPED, members partially resolved.** The grammar parses
`WITH (NEW)? implicitCallStmt_InStmt NEWLINE+ block END_WITH`
(`VisualBasic6.g4:620-621`). The worker emits `.Member` call rows inside With
blocks (display ".ProcessOrder", `Vb6Worker.java:896-922`) and the resolver
keeps the ASG binding for dot-prefixed rows
(`vb6_resolver.py:372-375`) — but nothing records the With TARGET, and no
`WithStmtContext` walk exists. Corpus: 6 With blocks; both user-relevant
flavors appear: `With cboType` (control config, `superadmin_createdb.frm:272`)
and `With superadmin_createdb ... .Show vbModeless ... .SetFocus` (form
navigation through a With target, `addbook.frm:529-534`). Insertion: worker
`WithStmtContext` walk using the existing `collectCtxs` helper
(`Vb6Worker.java:1464-1485`) emitting e.g. `with_blocks[]`
{target_text, is_new, enclosing_procedure, line}; members are then joinable
by (procedure, line-range).

**Group-2 insertion points:** worker plane additions (2.2/2.3/2.4) +
hydration dataclasses (`vb_common.py`, tolerant defaults) + row/edge building
in `vb_analyzer_base.py` (relations lane, using existing `USES`/`USES_TYPE`
rel types) + Qdrant payload keys if searchability is wanted.

### Group 3 — event graph

**3.1 with_events (`WithEvents x As T`): NEVER CAPTURED.** Grammar:
`variableStmt : (DIM | STATIC | visibility) WS (WITHEVENTS WS)? variableListStmt`
(`VisualBasic6.g4:600-601`) — parsed. The worker's `variableJson` does NOT
emit a with_events flag (`Vb6Worker.java:1020-1062` — no such key); whether
the ProLeap `Variable` ASG model exposes it is `unknown` (not inspected).
The regex engine matches it but discards the group:
`_VAR_DECL_RE` has `(?P<with_events>WithEvents\s+)?`
(`vb_common.py:486-493`) and the emission loop never reads it
(`:1097-1141`). Corpus: 0 WithEvents (designer-wired form app).

**3.2 event_handler (`Private Sub Control_Event(`): PARTIAL — payload-only,
no graph edges.** Designer control list: `controls[]` plane, parse-tree walk
of `controlProperties` (`g4:97-126`), designer files only
(`Vb6Worker.java:469, 1286-1338, 1529-1535`); parent/child + index +
Caption/Text/Name/Index/TabIndex properties. Wiring: `match_event_handlers`
matches `^(ctrl)_(evt)$` longest-control-first against the closed
`VB6_EVENT_SUFFIXES` set plus `Form`/`MDIForm`/`UserControl` pseudo-controls
(`vb_analyzer_base.py:728-791`), annotating `FunctionDef.vb6_event` /
`vb6_control_type` (`vb_common.py:52-53`) which reach the Qdrant function
payload (`vb_analyzer_base.py:1424-1425`) and a compact `controls` summary on
the class payload (`:1489-1496`). NO graph representation: no control node,
no handler/wiring edge — 260917-1628 AD-01/AD-06 + Q1 explicitly deferred
that to "plan schema riêng" (`docs/plans/260917-1628-vb6-antlr-depth-upgrade/plan.md:129`).
Corpus: 116 `_Click` subs, 124 `X_Y(...)` subs, 6 `Form_Load`, 302 designer
`Begin VB.*` controls.

**3.3 auto_lifecycle (Form_Load/QueryUnload/Unload,
Class_Initialize/Terminate): PARTIAL.** Load/Unload/QueryUnload are in the
suffix set and match against the `Form` pseudo-control
(`vb_analyzer_base.py:728-741`), so `Form_Load` in a .frm gets
`vb6_event="Form.Load"` in Qdrant. `Class_Initialize`/`Class_Terminate` are
DELIBERATELY not matched ("class lifecycle is not a control event", Q3
decision `vb_analyzer_base.py:738-741`) — the anchor is incomplete for .cls
modules (0 in corpus). There is no distinct lifecycle anchor kind; all
lifecycle handlers are ordinary Function nodes whose event-ness lives only in
Qdrant payload text.

**3.4 UI-trace complaint (click handler → module → DB): procedure edges
work; control/handler and UI-state edges do not exist.** What works today,
end-to-end on the corpus: `cmdLogin_Click` (Function node) → CALLS edge →
`login.Init` (resolved via predeclared form receiver,
`vb6_resolver.py:391-412`) → that form's procedures are Function nodes;
`trace_flow` from the handler is the designed query path
(`docs/plans/260917-1628-vb6-antlr-depth-upgrade/phase-02-form-controls-events.md:29`).
What is missing for the user's story:
  a. control → handler: no graph edge and no control node (3.2) — you cannot
     ask "what does clicking cmdLogin trigger" in graph terms;
  b. UI property writes: `superadmin_createdb.Visible = True`,
     `.SetFocus`, `.Text = ...` are let-stmts / member-lets, not call rows —
     they never appear as edges at all (implicit member LETs produce no
     CALL_TYPES_EMITTED row; `superadmin_createdb.Visible = True` at
     `addbook.frm:538`, `Me.Visible = False` at `:540`);
  c. `.Show` navigation inside `With <form>` resolves only when the ASG
     happened to bind the dot-prefixed row (`vb6_resolver.py:372-375`); with
     the With target dropped (2.4) the navigation edge is fragile;
  d. module state read/written by handlers (main.bas `Public userID` etc.,
     `main.bas:4-29`) has no READS/WRITES edge — see Group 4.
  `.Show`/`.Show 1` sites: 51 in corpus (e.g. `addbook.frm:530`).

**Group-3 insertion points:** worker (WithEvents flag on variableJson;
optionally lifecycle classification), `vb_analyzer_base.py`
(match_event_handlers already the seam; a control-node/handler-edge builder
would sit next to the IMPLEMENTS loop `:1271-1307`), schema
(`_NODE_SPECS`/`_REL_SPECS` additions — see Risks 1/2), Qdrant mapping.

### Group 4 — state graph

**4.1 global_decl (`Global x`): PARTIAL — flag semantics unverified for the
`Global` keyword.** Grammar: `GLOBAL` is a visibility
(`VisualBasic6.g4:805-809, 824-828`); `variableStmt` accepts it. Worker:
module-level variables emitted with `is_global = moduleLevel &&
visibility == VisibilityEnum.PUBLIC` (`Vb6Worker.java:1035, 1047`) — whether
ProLeap maps the `Global` keyword to `VisibilityEnum.PUBLIC` (or a distinct
enum value that loses the flag) is `unknown` (not runtime-verified). Regex
path: `is_global = scope in {public, global, friend}`
(`vb_common.py:1104`). Published: Variable node `is_global` prop
(`asdict_variable` `vb_common.py:1441`; spec `ladybug_schema.py:212-213`).
Corpus: 0 `Global` keyword; the app's global state is module-level `Public`
in `main.bas:4-29` (`Public userStatus As Integer`, `Public userID As
String`, ...), which does get `is_global=true` under the PUBLIC mapping.
No edges connect procedures to globals.

**4.2 module_const (`Const x =`): CAPTURED at module level.** Worker:
`module.getConstants()` → `constants[]` with name/value/type_name/line
(`Vb6Worker.java:1145-1188`); regex: `_CONST_RE`
(`vb_common.py:515-519`, emission `:1058-1094`); published as Constant nodes
with `value`/`type_name` (`asdict_constant` `vb_common.py:1403-1426`; spec
`ladybug_schema.py:210-211`). Corpus: 10 Const decls (e.g.
`md5.bas:17-18`). GAP: procedure-local `Const` — the worker only iterates
`module.getConstants()` (module scope; `inferred` that locals are absent);
the regex engine DOES catch locals (`_CONST_RE.finditer(source)`) but with no
procedure scoping. Corpus local example: `md5.bas:194`
(`Const CP_UTF8 As Long = 65001` inside a function).

**4.3 static_local (`Static x` inside Sub): PARTIAL — present but not
distinguishable.** Grammar: `STATIC` in `variableStmt` scope group
(`VisualBasic6.g4:600-601`) and on procedure headers (`:311, 438-446, 538`).
Worker: procedure-local variables ARE emitted with `procedure_name`
(`Vb6Worker.java:1012-1016, 1058-1060`), so a Static local produces a
Variable row scoped to its procedure — but there is NO `is_static` flag, and
`inferred`: whether `procedure.getVariables()` includes only Static locals or
all locals (Dim too) is unverified, so persistence is not identifiable from
the payload. Regex: `Static` is in the scope group and sets nothing
(`vb_common.py:487, 1104-1105`), and emission is file-wide with no procedure
attribution (`:1115-1121` admits the post-loop limitation). Corpus:
`keybinds.bas:15-17, 76` (`Static LastTrigger As Single` — a debounce state),
`md5.bas:83`.

**4.4 redim_preserve (array mutation site): NEVER CAPTURED.** Grammar:
`redimStmt : REDIM WS (PRESERVE WS)? redimSubStmt ...`
(`VisualBasic6.g4:461-463`). No plane in the worker, no regex, no resolver
role, no graph edge. Corpus: 26 sites (e.g.
`superadmin_manage_loans.frm:409` `ReDim Preserve csvFiles(i)`).
Insertion: worker ctx-walk for `RedimStmtContext` (caller procedure +
array name + preserve flag + line) — `collectCtxs` pattern applies; edge
carriers available without schema change: `USES` Function→Variable
(`ladybug_schema.py:384-385`). Note `WRITES_TO` exists but is
Function→DatabaseTable only (`:399`).

**Group-4 insertion points:** worker planes (4.3 flag, 4.4 new, 4.2 local
const scope) → hydration dataclasses → asdict rows (Variable/Constant nodes
already exist; edges would ride `relations_rows` with explicit labels,
precedent `vb_analyzer_base.py:1127-1134` CONTAINS + `:1300-1307`
IMPLEMENTS).

### 5. symbol_id scheme (how new anchors can reference procedure nodes)

- functions: `{Module}.{Name}/{arity}@{rel_path}` (`Vb6Worker.java:550,
  599-602`; exact pin `tests/test_vb6_antlr_worker_contract.py:103-113` —
  `modUtil.CalcTotal/2@modUtil.bas`). Every call row's `caller_id` parses via
  `_CALLER_ID_RE` (`vb6_resolver.py:47`).
- class/form modules: `{Name}@{rel}` (`typeJson` `Vb6Worker.java:1064-1087`);
  interface targets: `interface::{Name}@{rel}` (`:1070`);
  external placeholders: `external::vb6/{name}`
  (`vb_analyzer_base.py:689-692`).
- declares/enums/constants/events/regex-variables: `{Name}@{rel}` (worker
  `:1262, 1172, 1127, 1213`; regex `vb_common.py:1075, 1121`).
- worker variables: `{Module}.{Name}@{rel}` module-level,
  `{Module}.{Proc}.{Name}@{rel}` procedure-local (`Vb6Worker.java:1036-1043`)
  — a procedure-scoped id convention ALREADY EXISTS for state anchors to
  point at owners, though regex-path variable ids differ (bare `Name@rel`,
  `vb_common.py:1120-1121`).
- A control anchor would introduce a new id prefix; precedents
  (`interface::`, `external::vb6/`) show the convention is extensible
  (`inferred` — no test forbids new prefixes, but label lanes are pinned,
  `tests/test_vb6_graph_contract.py:174-199`).

### 6. Schema/writer state for new node kinds (decision inputs)

- `_NODE_SPECS` has NO VB6 control/state/with labels
  (`ladybug_schema.py:184-266`). Unknown labels fall back to `id` PK + core
  columns + `_properties` spill (`node_spec` `:420-437`, `SPILL_PROPERTY`
  `:26`) — writeable through generic lanes IF a writer lane emits them.
- `_REL_SPECS` VB6-relevant entries: CALLS/POSSIBLE_CALLS are
  Function→Function ONLY (`:306-307`); `USES`/`USES_TYPE` cover
  Function→Type, Class→Type, Function→Variable, Function→Constant
  (`:384-386`); IMPLEMENTS Class→Interface (`:374-375`); HANDLED_BY exists
  for ApiEndpoint/HttpEndpoint/Route/ServletEndpoint→Function (`:350-354`)
  but has no Control endpoint.
- Unknown REL types: `rel_spec` falls back to core columns (`:440-452`), BUT
  `compile_rel_ddl` raises `ValueError` when an unknown rel type has no
  endpoint pairs for DDL (`:486-487`) — on the LadybugDB provider a brand-new
  rel type MUST be added to `_REL_SPECS` (or pre-created) or the DDL
  self-heal path fails; whether FalkorDB/Neo4j drivers accept unknown rel
  types in `write_relations_typed` without a spec is `unknown`.
- `write_relations_typed` accepts arbitrary rel_type but REQUIRES explicit
  `source_label`/`target_label` (grouping rejects unlabeled endpoints,
  `language_writer.py:1717-1747`); the analyzer already emits labeled rows
  (`vb_analyzer_base.py:1127-1134, 1300-1307`).

---

## Conventions to follow

1. **Payload plane naming/emission**: one `payload.add("<plane>", jsonArray)`
   per plane inside `filePayload` (`Vb6Worker.java:407-520`); snake_case
   plane names; rows mirror regex-era dataclass shapes exactly — hydration
   drops unknown keys (`vb_common.py:267-276`) but missing required keys
   raise. Tolerant-plane precedent: `declares` hydrates to a dataclass with
   defaults, `controls` stays raw dicts
   (`vb_analyzer_base.py:330-334`).
2. **Any payload-shape change ⇒ bump `PARSE_CACHE_VERSION`**
   (`vb_common.py:25`, currently `vb-family-v2026-09-18-1`) and update BOTH
   pins: `tests/test_vb6_engine_dispatch.py:38` and
   `tests/test_vb6_antlr_worker_contract.py:29`.
3. **Worker change ⇒ jar rebuild**: `ensure_worker_built` stamp check reuses
   the jar only when its mtime ≥ newest worker/vendor source mtime
   (`vb6_antlr_adapter.py:108-165`); first implementation step should rebuild
   and run the contract suite.
4. **Fixture/golden conventions**: fixture app at
   `tests/fixtures/vb6-application` (force-tracked; prior plan R7 —
   `tests/fixtures/**` is gitignored), expectations in `expected.json`;
   golden gates `tests/test_vb6_golden.py` (M2 recall ≥80%, zero-drop,
   no_call string-literal trap, status pins); graph contract
   `tests/test_vb6_graph_contract.py` pins label lanes
   (`:174-199`), `resolution_class == "lexical_candidate"` (`:127, 170`)
   and status vocabulary (`:122-134`); Qdrant payload assertions live in
   `tests/test_vb6_qdrant_payload.py`; parity in
   `tests/test_vb6_engine_parity.py`.
5. **Graph row building**: node rows via `asdict_*`
   (`vb_common.py:1206-1453`); relation rows need `source_id`, `target_id`,
   `rel_type` PLUS explicit `source_label`/`target_label` when labels are not
   inferable (`vb_analyzer_base.py:1127-1134` precedent); edges must be
   re-published source-or-target on incremental syncs (AD-09 filter
   `vb_analyzer_base.py:1332-1349`).
6. **Qdrant payloads are explicit field mappings in `vb_analyzer_base.py`**
   (`:1394-1560`); new searchable metadata needs a key added there; embedding
   text stays `note or code` (`:1404`).
7. **Event-wiring seam**: `match_event_handlers` runs after resolution,
   before rows/embeddings (`vb_analyzer_base.py:1001-1011`); closed suffix
   set + pseudo-controls (`:728-741`); any new handler-anchoring must keep
   the 0-false-match property (M3 gate of prior plan).
8. **Dialect safety**: `vba`/`vbscript` share `vb_common.parse_vb_file`; new
   VB6-only planes must hydrate tolerantly (declares precedent,
   `vb_analyzer_base.py:330-334, 651-653`).

---

## Risks / open questions

1. **Schema-change ownership (the central decision).** Prior plan
   260917-1628 explicitly ruled schema/writer/MCP changes OUT (AD-01,
   `plan.md:83`; Q1 `plan.md:129` deferred control/event graph edges to "a
   separate schema plan"). THIS plan is that plan if control nodes / handler
   edges / state edges are wanted in the graph. Options: (a) re-open schema —
   add `_NODE_SPECS`/`_REL_SPECS` entries + writer-lane support (bigger
   blast radius: shared `language_writer.py`, MCP profile, contract tests);
   (b) zero-schema: reuse `USES`/`USES_TYPE` Function→Type/Variable/Constant
   for state+type anchors, keep control/handler wiring in Qdrant payload as
   today; (c) hybrid. Planning agent must pick; digest does not decide.
2. **Unknown rel types on LadybugDB raise** (`compile_rel_dbl` →
   `compile_rel_ddl` ValueError on empty endpoint pairs,
   `ladybug_schema.py:486-487`); new rel types must be specced. FalkorDB/Neo4j
   behavior for unspecced rel types: `unknown`.
3. **`Global` keyword visibility mapping in ProLeap** (`VisibilityEnum`) —
   `is_global` on `Global x` rows is `inferred`-correct only if GLOBAL maps
   to PUBLIC (`Vb6Worker.java:1035, 1047`). Runtime spot-check needed before
   relying on it.
4. **Procedure-local coverage in the worker's variable/constant planes**:
   whether `procedure.getVariables()` includes plain Dim locals or only
   Static ones, and whether local `Const` appears anywhere, is `unknown`
   (code inspected, not runtime-verified). Affects static_local (4.3) and
   module_const-local (4.2).
5. **WithEvents on the ProLeap Variable model**: `unknown` — worker may be a
   one-line flag addition if the ASG exposes it, else a ctx-walk on
   `VariableStmtContext`.
6. **`.frm` materialization caveats**: `controls[]` exists only for
   designer files and only in keep-designer mode; `VB6_ANTLR_STRIP_DESIGNER=1`
   wipes controls and flags `parse_meta.designer_stripped`
   (`vb6_antlr_adapter.py:57-94`; `Vb6Worker.java:484-486`). Golden
   line-number pins (`tests/test_vb6_antlr_worker_contract.py:92-101`)
   constrain any designer/line math. Any new designer-derived anchor must
   degrade observably (designer_stripped precedent).
7. **Handler false-positive budget**: 124 `X_Y` subs vs 116 `_Click` in
   corpus shows near-collision density; extending suffix/prefix sets (e.g.
   adding Class_Initialize) must preserve the closed-set anti-FP rule
   (`vb_analyzer_base.py:768-783`).
8. **Corpus asymmetries** (Bookworm): 0 `Global`, 0 `WithEvents`, 0 typed
   `ADODB.|DAO.|RDO.|Scripting.`, 0 `Class_Initialize`, 0 `!` dictionary
   access; but 6 With blocks, 26 ReDim Preserve, Static locals, 10 Const,
   51 `.Show`, 116 `_Click`, 302 designer controls. Anchor fixtures must be
   synthetic (`tests/fixtures/vb6-application`) for the zero-in-corpus
   anchors; com_type_ref has no positive corpus instance.
9. **UI property writes are not call rows**: making `.Visible`/`.Text`
   assignments traceable needs a NEW statement-level anchor (let-stmt /
   implicit member assignment walk) — nothing today even represents them;
   scope this consciously.
10. **`POSSIBLE_CALLS` noise budget**: any new call-like rows (e.g. deriving
    COM member access) flow through the golden zero-drop/no_call matrices
    (`tests/test_vb6_golden.py:122-142`) and the dictionary-call noise
    precedent shows dedup rules are required (prior digest risk 3).
11. **Regex fallback asymmetry**: new anchors will be ANTLR-only unless
    regex parity is built (declares precedent: asymmetry is acceptable when
    recorded — `parse_meta.declares_regex_support`, `vb_common.py:207-215`).
    `parse_meta.parser_engine` makes it observable (`vb_common.py:1163`).

---

## Recommended approaches (max 3)

1. **Worker-first anchor planes + zero-schema edges**: add ctx-walk planes in
   `Vb6Worker.filePayload` — `with_blocks[]` (target/new/procedure/line),
   `instantiations[]` (`New X`), state rows for `redim`/`static`/`withevents`
   flags — then publish type/state facts through the ALREADY-specced
   `USES`/`USES_TYPE` rels (Function→Type/Variable/Constant,
   `ladybug_schema.py:384-386`) and flip `exported` from `is_private` in
   `asdict_function` (`vb_common.py:1223`) for public_surface; bump
   PARSE_CACHE_VERSION; fixtures for all zero-in-corpus anchors.
2. **Targeted schema re-open for the UI trace**: add a `Control` node spec +
   `WIRED_TO`/`HANDLES`-style rel spec (`Control→Function`) to
   `_NODE_SPECS`/`_REL_SPECS` and materialize the existing `controls[]` +
   `match_event_handlers` results as graph edges (controls are already
   parsed and matched — only the graph projection is missing); this is the
   explicitly deferred "plan schema riêng" of 260917-1628 Q1 and directly
   answers the click→UI→DB traceability complaint.
3. **Resolver/derivation completion without new planes**: Python-side
   derivation from existing payloads — pair `.Member` call rows to With
   targets by line-range, classify COM receivers by `variables[].type_name`
   prefix (ADODB.|DAO.|RDO.|Scripting.), treat `Set x = New <module>` via
   callee_name matching — cheapest path, no worker/jar rebuild, but no
   statement-level anchors (no ReDim/Static/WithEvents) and fragile for
   With targets; `inferred` feasibility, not exercised.
