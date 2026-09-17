# Research digest — VB6 ANTLR depth upgrade (260917-1628)

Scope under research: hydrate the currently-empty ANTLR payload planes
(properties/events/enums/constants, form/control + event-wiring metadata),
resolver upgrades, DICTIONARY_CALL emission, comment extraction — ALL inside
`code-tiny/tools/vb/**` and `tests/**`. No shared graph schema changes, no new
relation types, no writer/MCP-layer changes.

Method note: every claim carries a `path:line` reference or an explicit
`unknown`/`inferred` marker. Verified against the working tree at
commit-state of 2026-09-17 (~14:35 local).

---

## Findings

### 1. REGEX-PAYLOAD PARITY (the shapes the ANTLR planes must mirror)

`parse_vb_file` (regex engine) returns 13 planes
(`code-tiny/tools/vb/vb_common.py:528-551`, return tuple at `:1133`). Payload
dicts are produced by `_payload_from_parsed` via `dataclass.__dict__`
(`code-tiny/tools/vb/vb_analyzer_base.py:267-298`), so payload keys == dataclass
fields exactly.

**Hydration drops unknown keys** — `dataclass_from_payload`
(`vb_common.py:232-241`): `known = {f.name for f in dataclasses.fields(cls)}`;
the dict comprehension keeps only known keys ("unknown keys are dropped instead
of raising", `vb_common.py:236-238`). Used by `_hydrate_payload`
(`vb_analyzer_base.py:301-332`) for every plane. Consequence: the worker MAY
emit extra keys per row safely, but MUST emit all keys that have no dataclass
default, or hydration raises TypeError.

Plane-by-plane (regex emission sites + row fields):

- **enums** — `EnumDef` (`vb_common.py:143-157`); emission `:996-1012`.
  Fields: `symbol_id` = `"{qualified}@{rel_path}"` (`:993`),
  `qualified_name`, `name`, `namespace_name` (None today; note regex
  post-loops run after stacks drained, so effectively null — `:989-990, 1072-1074`),
  `class_name`, `file_path`, `start_line`, `end_line` (End Enum line, `:976-981`),
  `members`: `List[Tuple[name, value_str]]` (`:977-987`), `code` (full Enum..End Enum
  slice `:994`), `comment=""`, `summary=""`, `note=_build_note(code,"","")`.
  Graph row `asdict_enum` JSON-encodes members to a string
  (`vb_common.py:1346`).
- **constants** — `ConstantDef` (`vb_common.py:160-174`); emission `:1015-1051`.
  Fields: `symbol_id` = `"{qualified}@{rel_path}"` (`:1032`), `qualified_name`,
  `name`, `value` (raw text after `=`, stripped, `:1017`), `type_name` (may be
  `""`), `class_name`, `namespace_name`, `file_path`, `line_number` (single
  line, not a range), `code` = matched line, `comment`, `summary`, `note`.
  Graph row `asdict_constant` (`:1360-1383`).
- **events** — `EventDef` (`vb_common.py:110-124`); emission `:883-918`.
  Fields: `symbol_id` = `"{qualified}@{rel_path}"` (`:899`), `qualified_name`,
  `name`, `class_name`, `namespace_name`, `file_path`, `start_line` ==
  `end_line` == declaration line (`:909-911`), `parameters` (raw inner-paren
  text, may be `""`), `code` = matched declaration line only (`:900`),
  `comment`, `summary`, `note`.
- **properties** — `PropertyDef` (`vb_common.py:91-107`); emission `:834-880`.
  Fields: `symbol_id` = `"{qualified}@{rel_path}"` (`:859`), `qualified_name`,
  `name`, `kind` ∈ {`get`,`let`,`set`} (lowercased `:835`), `class_name`,
  `namespace_name`, `file_path`, `start_line`, `end_line` (End Property),
  `parameters`, `return_type` (may `""`), `code`, `comment`, `summary`, `note`.
  Graph row kind is rewritten to `property_{kind}` in `asdict_property`
  (`vb_common.py:1259`).
- **declares / API procedures** — the regex engine emits NOTHING for
  `Declare` statements: `_FUNC_START_RE`
  (`vb_common.py:419-423`) only matches Sub/Function/Property Get|Let|Set, and
  its modifier-prefix alternation (`:420`) does not include `Declare`, so
  `Public Declare Function ...` never matches. There is no Declare plane. If
  declares are to be published they must ride an existing plane (the natural
  mirror is FunctionDef rows — see Recommended approaches) — `inferred`, not an
  existing contract.
- **variables** (already hydrated by the worker) — `VariableDef`
  (`vb_common.py:177-196`); the worker mirrors it at `Vb6Worker.java:867-924`.

The worker's existing function-row shape already proves the mirror pattern:
`procedureJson` emits exactly the FunctionDef keys incl. `module_name` /
`is_private` (`Vb6Worker.java:506-537`), and empty planes are emitted at
`Vb6Worker.java:438-444` (`namespaces`, `relations`, `properties`, `events`,
`enums`, `constants`).

`comment`/`summary` are hardcoded `""` on the regex path for every plane
(e.g. `vb_common.py:615-617, 673-675, 876-878, 913-916, 957-959, 1008-1010,
1046-1048`); `note = _build_note(code, comment, summary)`
(`vb_common.py:267-275`). Only `FileDef.comment` is real: leading `'` header
block via `_extract_file_comment` (`vb_common.py:252-264`, used `:1100-1109`).

### 2. PUBLICATION FLOW (read-only; what edits are in scope)

**Node rows** (`vb_analyzer_base.py:987-1069`): each payload plane maps to a
row list via the `asdict_*` functions in vb_common.py — file `:992`,
namespaces `:996`, classes (types lane) `:1001-1017` (+ synthetic CONTAINS
class→function edges `:1005-1017`), functions `:1019-1022`, relations
`:1024-1030`, properties `:1041-1044`, events `:1046-1049`, interfaces
`:1051-1054`, enums `:1056-1059`, constants `:1061-1064`, variables
`:1066-1069`. Two-tier VB6 edges at `:1071-1124`; IMPLEMENTS at `:1126-1162`;
external_symbol placeholders `:1164-1185`. Graph label set already covers all
planes (`index_specs` Event/Interface/Enum/Constant/Variable at
`vb_analyzer_base.py:940-944`), and the shared schema already defines
Property/Event/Interface/Enum/Constant/Variable node specs
(`code-tiny/tools/graph/schema/ladybug_schema.py:203-212`) — hydrating planes
needs NO schema change.

**Qdrant embedding lives in the ANALYZER, not the writer.** The block at
`vb_analyzer_base.py:1249-1497` (the mission cited language_writer ~1249-1498;
that range in language_writer.py is actually the Neo4j `_full` Cypher writers —
see below). Embedded node types: function `:1258-1285`, class `:1286-1313`,
property `:1314-1343`, event `:1344-1371`, interface `:1372-1399`, enum
`:1400-1427`, constant `:1428-1455`, file `:1456-1477`. Variables are NOT
embedded. The point payload dict is an **explicit field mapping constructed in
vb_analyzer_base.py** (e.g. function payload keys listed at `:1265-1282`),
then passed through `enrich_project_scope` which only copies and adds
`project_id_normalized` (`code-tiny/tools/common/project_scope.py:89-104`) and
upserted via the writer (`vb_analyzer_base.py:1479-1487`). Collection default
`vb_functions` (`vb_analyzer_base.py:1518`).

**Decision this forces:** adding event-wiring metadata / comments to the
Qdrant payload requires edits ONLY in vb_analyzer_base.py + vb_common.py
dataclasses (both in `code-tiny/tools/vb/**`). No writer edit needed. Caveat:
new payload keys must come from dataclass fields (payloads are hydrated into
dataclasses before publication — attribute access like `fn.comment` only sees
dataclass fields), or be added as parse_meta-derived extras inside the
analyzer.

**Graph (Neo4j) node props are writer-fixed.** `write_functions_full`,
`write_types_full`, `write_properties_full`, `write_events_full`,
`write_interfaces_full`, `write_enums_full` use explicit `SET` column lists
(`code-tiny/tools/graph/writer/language_writer.py:1339-1378, 1293-1337,
1398-1442, 1444-1487, 1489+, 1530+`). Extra row keys are silently ignored by
Cypher. So a new field (e.g. `wired_controls`) can reach Qdrant payload but NOT
graph node props without a writer edit (out of scope).

**Exception — edge props ARE passthrough:** `write_possible_calls_with_site`
does `SET r += row.props` (`language_writer.py:2147-2149`); the props dict is
built in the analyzer (`vb_analyzer_base.py:1110-1123`). Event-wiring facts
tied to a call site (e.g. handler invocation) can ride POSSIBLE_CALLS props
today, subject to `enforce_strong_call_row` (`language_writer.py:2128`) —
`resolution_class` must stay in the standard vocabulary (AD-04). Strict CALLS
edges are writer-fixed (`language_writer.py:836-859`: only
count/call_type/project_id/project_id_normalized).

**Where `comment`/`summary`/`note` originate for embedding text:** dataclass
defaults `""` (`vb_common.py:40-42` etc.); regex sets them to `""` and builds
`note` from code (`vb_common.py:615-617`); worker emits `""`
(`Vb6Worker.java:531-533, 916-918, 942-944, 458-460`); embedding text is
`fn.note or fn.code or ""` (`vb_analyzer_base.py:1259`, same pattern for all
planes) and `fd.note or fd.summary or fd.code[:500]` for files (`:1459`). So
comment extraction alone will change embedding text automatically once
`comment`/`note` are populated — no embedding-code change needed.

### 3. TEST CONTRACTS that must be updated when planes hydrate

No test currently asserts the empty planes (`events == []` etc.) — grep over
`tests/test_vb6_*.py` found none. Pins that DO break or need review:

- `tests/test_vb6_engine_dispatch.py:35-39` — `test_parse_cache_version_bumped`
  pins `PARSE_CACHE_VERSION == "vb-family-v2026-09-17-1"`. MUST bump + update
  when payload shape changes again (AD-08).
- `tests/test_vb6_antlr_worker_contract.py:58-74` —
  `test_payload_shape_matches_contract` pins `parser_engine=="antlr"`,
  `parser_language=="vb6_antlr"`, `resolution_source=="asg"` and the call-row
  key set (`caller_id, callee_name, call_line, call_type, resolution_status`).
  Additions are fine; renaming/removal breaks.
- `tests/test_vb6_antlr_worker_contract.py:92-101` —
  `test_frm_materialized_with_payload` pins `frmMain Form_Load start_line == 31`
  (designer padding keeps original numbering). Any change to designer-block
  handling or comment-driven line math must keep this true or update it.
- `tests/test_vb6_antlr_worker_contract.py:103-113` — pins exact
  `callee_id == "modUtil.CalcTotal/2@modUtil.bas"` (exact arity in symbol id).
  Changing arg counting (e.g. optional/ParamArray handling) breaks this.
- `tests/test_vb6_antlr_worker_contract.py:115-129` — pins symbol-id prefix
  `modMain.` for all modMain functions and the implements map +
  `interface::IShip@IShip.cls` emission. Hydrating more planes must not move
  these ids.
- `tests/test_vb6_golden.py:97-102` — `_target_ok` pins property-get arity 0 /
  property-let arity 1 encoding in the symbol id; `:104-120` M2 >= 80% recall;
  `:122-132` zero-drop; `:134-142` no_call string-literal trap (new
  DICTIONARY_CALL rows must not create edges at those sites);
  `:144-156` pins `LateBound=late_bound`, `TestSameName=ambiguous`,
  `MsgBox=external`.
- `tests/test_vb6_graph_contract.py:109-124` — CALLS tier exact pair; `:118-120`
  no ambiguous in CALLS; `:122-134`
  `test_possible_calls_use_standard_vocabulary_and_site_ids` pins
  `resolution_class == "lexical_candidate"` and `resolution_status` ∈
  {`ambiguous`,`late_bound`,`external`,`unresolved`,`asg_resolved`,
  `name_resolved`,`""`} — dictionary-call rows MUST use one of these statuses
  or this test (tests/**, updatable) changes;
  `:136-150` pins late_bound/ambiguous/external presence and multi-candidate
  ambiguous site sharing one site_id.
- `tests/test_vb6_graph_contract.py:152-157` — external_symbol placeholder
  Function rows must intersect POSSIBLE_CALLS targets (new resolved callees
  shrink this set only if a currently-external callee becomes project-resolved;
  the test only requires SOME placeholder remains).
- `tests/test_vb6_graph_contract.py:174-180` — class ids on the `:Type` lane;
  `:182-191` every Function row (incl. placeholders) carries
  `project_id_normalized`; `:193-199` Interface nodes.
- `tests/test_vb6_engine_dispatch.py:112-159` — `test_per_file_worker_error_falls_back`
  uses a fake worker payload containing all planes as `[]` plus file_def with
  `comment/summary/note: ""` (`:123-134`); shape-compatible with hydration as
  long as every plane key keeps its name.
- `tests/test_vb6_resolver.py` — pins resolver semantics incl. exact statuses
  (`:87-186`); resolver upgrades must keep these behaviors for existing inputs.
- Not pinned anywhere: comment extraction (no test asserts `comment == ""`),
  declares, dictionary calls (no test asserts their absence — they are silently
  dropped today because `DICTIONARY_CALL` is not in the worker's emission
  filter, `Vb6Worker.java:89-95`).

### 4. PROLEAP ASG ACCESSORS (vendored 3.0.0, `code-tiny/tools/vb/antlr_worker/vendor/proleap-vb6-parser`)

Java accessor names + return types (paths under `src/main/java/io/proleap/vb6/`):

- **Module** (`asg/metamodel/Module.java`):
  - `Map<String, Enumeration> getEnumerations()` (`Module.java:92`);
    `Enumeration getEnumeration(String name)` (`:88`);
    `EnumerationConstant getEnumerationConstant(String name)` (`:90`).
  - `CommonTokenStream getTokens()` (`Module.java:129`); impl field
    `ModuleImpl.tokens` (`asg/metamodel/impl/ModuleImpl.java:109`), filled from
    the constructor with the `CommonTokenStream` created in
    `VbParserRunnerImpl.parseCode` (`asg/runner/impl/VbParserRunnerImpl.java:196`:
    `new CommonTokenStream(lexer)`) — the SAME stream the parser consumed, so
    after `parser.startRule()` all default-channel tokens are buffered; call
    `tokens.fill()` then filter `getTokens()` for
    `channel == Token.HIDDEN_CHANNEL && type == VisualBasic6Lexer.COMMENT`.
    COMMENT lexer rule → `channel(HIDDEN)` at
    `src/main/antlr4/io/proleap/vb6/VisualBasic6.g4:2077-2078` (also WS →
    HIDDEN at `:2068`). Note: the worker's pre-check `countSyntaxErrors`
    (`Vb6Worker.java:993-1018`) builds a SEPARATE lexer/stream; comments must
    come from `module.getTokens()`, not that one.
  - Also: `List<String> getLines()` (`:105`), `List<Sub> getSubs()` (`:126`),
    `List<Function> getFunctions()` (`:82`), `getPropertyGets/Lets/Sets()`
    (`:114-120`), `List<Procedure> getProcedures()` (`:83`).
- **Module-level Constants** — `Scope.getConstants(): List<Constant>`
  (`asg/metamodel/Scope.java:327`; impl `ScopeImpl.java:2456-2458`, map at
  `:326`). Module extends Scope, so `module.getConstants()` covers module-level
  constants. Populated by `VbDeclarationVisitorImpl.visitConstStmt` →
  `scope.addConstants(ctx)` (`asg/visitor/impl/VbDeclarationVisitorImpl.java:35`;
  per-const `ScopeImpl.addConstant` `:1167`).
  `Constant` (`asg/metamodel/statement/constant/Constant.java`):
  `String getName()` (via `Declaration extends NamedElement`),
  `ConstSubStmtContext getCtx()`, `ValueStmt getValueStmt()`,
  `boolean isModuleConstant()`. Value text: `getCtx().valueStmt().getText()`
  (generated `ConstSubStmtContext` has `ambiguousIdentifier()`,
  `valueStmt()`, `asTypeClause()`, `typeHint()` —
  `target/generated-sources/antlr4/io/proleap/vb6/VisualBasic6Parser.java`,
  ConstSubStmtContext block).
- **Enumeration members** — `Enumeration`
  (`asg/metamodel/statement/enumeration/Enumeration.java`):
  `Map<String, EnumerationConstant> getEnumerationConstants()`; each
  `EnumerationConstant`: `String getName()`, `EnumerationStmt_ConstantContext
  getCtx()`, `int getPosition()`, `ValueStmt getValueStmt()`
  (`EnumerationConstant.java`). Member ctx accessors: `ambiguousIdentifier()`,
  `valueStmt()` (generated parser, EnumerationStmt_ConstantContext).
- **Event declarations** — `Event` (`asg/metamodel/statement/event/Event.java`):
  `EventStmtContext getCtx()`, `Arg addArg(ArgContext)`; name/params via ctx
  (`EventStmtContext.ambiguousIdentifier()`, `.argList()`, `.visibility()` —
  generated parser). **No getter enumerates Events from a Module/Scope**:
  `Scope` only has `Event addEvent(EventStmtContext)` (`Scope.java:207`,
  impl `ScopeImpl.java:1277-1292`, stored via generic
  `registerStatement` — `ScopeImpl.statements` list `:332`). So event
  extraction should walk `module.getCtx()` for `EventStmtContext` and resolve
  through `program.getASGElementRegistry().getASGElement(ctx)` (same pattern
  the worker already uses for calls, `Vb6Worker.java:591-593`). `inferred`
  approach, accessors verified.
- **ProcedureDeclaration (Declare)** — `asg/metamodel/ProcedureDeclaration.java`:
  minimal — `Arg addArg(ArgContext)`; inherits `getName()` /
  `getCtx(): DeclareStmtContext` / `getModule/getScope/findScope` (via
  `Declaration`→`NamedElement`, `ScopedElement`). Registered by
  `module.addDeclaration(ctx)` (`VbDeclarationVisitorImpl.java:42`,
  `ModuleImpl.java:149-154`). **No Module getter exposes declarations** — walk
  ctx or registry. LIB/ALIAS/return type live on the parse ctx:
  `DeclareStmtContext` (generated parser, `VisualBasic6Parser.java:4122+`):
  `ambiguousIdentifier()` (declared name), `LIB()` (keyword token),
  `STRINGLITERAL()` list (1st = lib path, 2nd = alias when `ALIAS()` present),
  `ALIAS()`, `FUNCTION()`/`SUB()` (kind), `visibility()`, `argList()`,
  `asTypeClause()` + `typeHint()` (return type).
- **Attribute + Literal** — `Attribute` (`asg/metamodel/Attribute.java`):
  `AttributeStmtContext getCtx()`, `Literal getLiteral()`;
  `Literal.getValue(): String` (`asg/metamodel/Literal.java`). Stored in
  `ModuleImpl.attributes` map (`ModuleImpl.java:71`, `addAttribute` `:129-139`)
  but **there is no public getAttributes()** — iterate via
  `module.getCtx().moduleAttributes()` (grammar: `moduleAttributes : (attributeStmt NEWLINE+)+`,
  `VisualBasic6.g4:61-62`) + registry lookup, or read
  `AttributeStmtContext.implicitCallStmt_InStmt().getText()` (name) and
  `literal(i).getText()` (values; comma-separated list, generated parser
  AttributeStmtContext block).
- **Procedure** — `asg/metamodel/Procedure.java`:
  `List<Arg> getArgsList()` (`:32`), `Map<String, Arg> getOptionalArgs()`
  (`:38`), `List<Arg> getOptionalArgsList()` (`:40`),
  `boolean hasOptionalArgs()` (`:42`); plus `getName()`, `getVisibility()`,
  `getCtx()` (already used in `Vb6Worker.java:512-516`). `Arg.isOptional()`
  (`asg/metamodel/Arg.java:38`), `Arg.getDefaultValueCall()` (`:28`).
- **DictionaryCall** — `asg/metamodel/call/DictionaryCall.java` is a bare
  marker interface, but `DictionaryCallImpl extends CallImpl`
  (`call/impl/DictionaryCallImpl.java:14`) so it IS a `Call` with
  `getCallType() == CallType.DICTIONARY_CALL` and inherits
  `getName()/getCtx()/getModule()/getScope()/unwrap()` from
  `CallImpl`. `Call.CallType` full vocabulary at
  `asg/metamodel/call/Call.java:19-21`. Current worker filter
  `CALL_TYPES_EMITTED` (`Vb6Worker.java:87-95`) excludes DICTIONARY_CALL (and
  MODULE_CALL, VARIABLE_CALL, etc.) — emission is a one-line filter addition;
  the `Calls` count already visits every `Call` element via the ctx registry
  walk (`Vb6Worker.java:568-583`).
- **Module creation keys on EXTENSION** — `VbParserRunnerImpl.isClazzModule`
  (ext == "cls", `VbParserRunnerImpl.java:188-191`), `isStandardModule`
  (ext == "bas", `:193-196`); `VbModuleVisitorImpl.visitModule` creates
  `ClazzModuleImpl`/`StandardModuleImpl`, and **`result = null` for any other
  extension — no ASG module is registered**
  (`asg/visitor/impl/VbModuleVisitorImpl.java:51-65`). A designer-block-bearing
  file RENAMED to `.cls` DOES get a module (extension is the only gate; the
  grammar-level `module` rule accepts `controlProperties?` for any file —
  `VisualBasic6.g4:30` and `controlProperties` at `:97-99`).
  **Experimentally confirmed** (Q5 below): ok=true, zero syntax errors with a
  `Begin VB.Form` block present.

### 5. WORKER RUNTIME STATE + EXPERIMENT

- Jar exists: `/Users/hieplq1.aip/AI/cortex-harness/code-tiny/tools/vb/antlr_worker/worker/target/vb6-antlr-worker.jar`
  (15.6 MB, mtime Sep 17 14:34 — newer than all worker/vendor sources, so
  `ensure_worker_built`'s stamp check reuses it without Maven,
  `vb6_antlr_adapter.py:121-127`).
- `java` on PATH: `/opt/homebrew/opt/openjdk/bin/java`, OpenJDK 26.0.2.1.
- Experiment (fresh temp dir; `Design.cls` with `VERSION 5.00` /
  `Begin VB.Form Form1` / `Caption = "X"` / `End` / `Attribute VB_Name = "T"` /
  `Sub Main()` + `MsgBox "hello"` / `End Sub`; manifest
  `{"root": tmp, "project": "", "files": [{"file_path": "Design.cls"}]}`;
  `java -jar <jar> --manifest <json> --workspace-timeout-ms 60000`):
  - exit 0, `ok: true`, `has_error: false, error_nodes: 0` — the shared
    `module` rule accepted the designer block (controlProperties) in a `.cls`.
  - Line numbers refer to ORIGINAL positions: `Main.start_line = 6`,
    `call_line = 7` for MsgBox (designer block occupies lines 1-4, Attribute
    line 5).
  - Payload also shows: empty planes emitted
    (`namespaces/relations/properties/events/enums/constants: []`),
    `classes[0].code` includes the raw designer text, `kind: "class"` for a
    `.cls` (worker's `kindForFile` returns "form" only for
    .frm/.ctl/.pag reporting paths, `Vb6Worker.java:955-961`).
  - No `.frm` in this experiment (no adapter involved) — `.frm` behavior is
    already covered by `tests/test_vb6_antlr_worker_contract.py:92-101`.

### 6. SCOPE RISK SCAN (runtime touchpoints OUTSIDE code-tiny/tools/vb/** and tests/**)

All imports the VB6 path pulls in from outside its own directory (from
`vb_analyzer_base.py` header `:27-63` and `:1663`, `vb_common.py:12-17`,
`vb6_analyzer.py` wrapper):

| External dependency | Cite | Needed by | Modification needed for this plan |
|---|---|---|---|
| `tools.common.analyzer_cache` (file_signature, load/write_parse_cache, safe_cache_root) | vb_analyzer_base.py:29 | cache hydration | none |
| `tools.common.project_scope` (enrich_project_scope, project_id_lookup_key) | vb_analyzer_base.py:33; vb_common.py:12-17 | Qdrant point enrichment; node rows | none (passthrough copy, project_scope.py:89-104) |
| `tools.graph.writer.language_writer.LanguageCodeWriter` | vb_analyzer_base.py:35 | graph publication | none — planes already lane-wired (`write_all` params `properties/events/interfaces/enums/constants/variables`, language_writer.py:2720-2725) |
| `tools.common.call_evidence.callsite_site_id` (+ enforce_strong_call_row inside writer) | vb_analyzer_base.py:60, use :1102; language_writer.py:2128 | site ids / row validation | none (keep resolution_class vocabulary per AD-04) |
| `tools.graph.cli` (arg helpers) | vb_analyzer_base.py:34 | CLI | none |
| `tools.common.harness_config`, `tools.common.git_diff`, `tools.common.incremental_cleanup`, `tools.common.message_scan` | vb_analyzer_base.py:27-32 | pipeline framing | none |
| `tools.python.python_analyzer` (CodeEmbedder, QdrantWriter) | vb_analyzer_base.py:1663 | embedding/upsert | none (generic) |
| `tools.sync.incremental_sync.AnalyzerConfig("vb6", .../vb6_analyzer.py)` | incremental_sync.py:136 (list `:346`) | routing to analyzer | none (wrapper in-scope) |
| `tools.sync.owner_manifest` / `build_owner_manifests` import `tools.vb.vb_path_classifier.VBPathClassifier` | owner_manifest.py:13; build_owner_manifests.py:65 | ownership heuristics | none — do not change VBPathClassifier public API |
| `tools.project_topology.registry` vb6 CoverageEntry | registry.py:299 | topology descriptors | none |
| `tools.common.message_scan` vb6 extension tuple + `tools.common.message_detectors/vb6.py` | message_scan.py:35; message_detectors/vb6.py | message scan | none |
| `code-tiny/tests/test_analyzer_provider_wiring.py` pins `vb/vb_analyzer_base.py` source text (`create_graph_driver_from_args(args)` present) | test_analyzer_provider_wiring.py:25-45 | static source check | none — keep that call in vb_analyzer_base.py |

Conclusion: the ANTLR plane-hydration work can be done with zero edits outside
`code-tiny/tools/vb/**` and `tests/**`. Verified: no other module imports the
worker adapter or resolver (`grep` over code-tiny/tools excluding tools/vb
found only the files above).

---

## Conventions to follow

1. Mirror regex-plane shapes exactly for hydrated rows: same key names as the
   dataclass fields (Q1 lists); extra keys allowed (dropped at hydration,
   `vb_common.py:236-241`), missing required keys are not.
2. `symbol_id` conventions are load-bearing: `{qualified}/{arity}@{rel_path}`
   for functions (worker `Vb6Worker.java:520, 539-542`), `{qualified}@{rel_path}`
   for property/event/enum/constant (regex `vb_common.py:859, 899, 993, 1032`),
   `interface::{name}@{rel}` for interface targets (worker `:932-933`),
   `external::vb6/{name}` for placeholders (`vb_analyzer_base.py:1171`).
   Tests pin exact ids.
3. Any payload-shape change ⇒ bump `PARSE_CACHE_VERSION`
   (`vb_common.py:24`) and update `test_vb6_engine_dispatch.py:38` (AD-08).
4. `resolution_status` stays free-text vb6 vocabulary
   {ambiguous, late_bound, external, unresolved, asg_resolved, name_resolved,
   undefined}; `resolution_class` stays `lexical_candidate` (AD-04; enforced
   at language_writer.py:2128, pinned at test_vb6_graph_contract.py:127-132).
5. Designer blocks: continue materializing .frm/.ctl/.pag to padded temp .cls
   (AD-03); worker line numbers must keep referring to original file positions
   (pinned at test_vb6_antlr_worker_contract.py:101).
6. Analyzer-side embedding text stays `note or code` — populate
   `comment`/`summary`/`note` at engine level (regex + worker) rather than
   touching embed calls (vb_analyzer_base.py:1259).
7. Keep `create_graph_driver_from_args(args)` in vb_analyzer_base.py (provider
   wiring source test).

## Risks / open questions

1. **No public ASG getters for Events, Declarations (Declare), Attributes** —
   extraction must walk parse ctx + ASG registry (accessor facts verified in
   Q4; the exact walk pattern is `inferred`, not exercised). A spike in the
   worker (rebuild jar required — Maven run, which this research did NOT
   execute) should validate event/declare/attribute/enum/constant emission
   end-to-end.
2. **Constant values via ASG may be null**: `Constant.getValueStmt()` is set by
   a visitor pass; if unresolved, fall back to
   `getCtx().valueStmt().getText()`. Same for `EnumerationConstant`. (Edge
   behavior untested here — unknown.)
3. **Dictionary-call semantics**: `DICTIONARY_CALL` fires on collection/dict
   member access; naively emitting every one could add noisy POSSIBLE_CALLS
   rows and shift the golden zero-drop/no_call matrices
   (test_vb6_golden.py:122-142). Needs a dedup/noise rule (e.g. only when the
   receiver is not a known module — the worker already has `moduleNames`
   filter, Vb6Worker.java:632-634).
4. **Comment extraction scope**: hidden-channel COMMENT tokens include every
   trailing comment; pairing them to declarations (preceding-comment heuristic)
   is a design choice that changes embedding text for every function — M2
   golden recall is line-tolerance based and should be unaffected, but the
   worker contract test line pin (frmMain Form_Load start_line 31) constrains
   any line math.
5. **AD-03 tension (flag, not contradiction)**: the experiment proves a
   designer block parses cleanly inside a `.cls` and that extension is the
   only module-creation gate (VbModuleVisitorImpl.java:51-65). This suggests
   the adapter could someday pass .frm content directly with a .cls-typed
   registration instead of temp files — but AD-03 explicitly chose
   materialization; keep it unless the plan owner re-opens AD-03.
6. **Arity conventions**: optional args currently count in `getArgsList().size()`;
   `hasOptionalArgs()` exists if the plan wants min-arity/max-arity — changing
   the `/arity` segment breaks pinned ids
   (test_vb6_antlr_worker_contract.py:113, test_vb6_golden.py:97-102).
7. Jar rebuild required for any worker change: Maven is NOT to be run during
   planning (per mission); the build path already exists
   (vb6_antlr_adapter.py:135-144). First implementation step should rebuild and
   re-run the contract tests.
8. Regex fallback must gain the same planes or stay plane-poor: AD-05 keeps
   regex as fallback; if ANTLR hydrates planes the regex path stays empty for
   them — `parse_meta.parser_engine` already makes this observable
   (vb_common.py:1120), and `semantic_provider` flips to `vb6_regex` at
   vb_analyzer_base.py:968-973. Decide whether parity for regex is in scope
   (recommend: not required; document asymmetry).

## Recommended approaches (max 3)

1. Hydrate enums/constants/events/properties in `Vb6Worker.filePayload` from
   `module.getEnumerations()`/`getConstants()`/ctx-walks + ASG registry (Q4
   accessors), mirroring the exact dataclass keys of Q1; bump
   PARSE_CACHE_VERSION; extend the worker-contract test with fixture
   Enum/Const/Event/Property assertions.
2. Extract comments from `module.getTokens()` hidden-channel COMMENT tokens
   (preceding-line heuristic) into `comment`/`summary`/`note` for functions and
   file_def in the worker (regex path keeps header-block comment), letting the
   existing `note or code` embedding and explicit Qdrant payload mapping in
   vb_analyzer_base.py carry them to Qdrant with no writer changes.
3. Emit DICTIONARY_CALL rows by adding `"DICTIONARY_CALL"` to
   `CALL_TYPES_EMITTED` with `resolution_status="unresolved"` (staying inside
   the pinned status vocabulary) plus the module-name receiver filter, and
   expose Declare/Attribute facts as parse_meta extras + event-wiring metadata
   via POSSIBLE_CALLS `props` (passthrough at language_writer.py:2147-2149) —
   zero schema/writer edits.
