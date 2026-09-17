# Spike — plane accessors runtime verification (phase-01 task 1.1)

Date: 2026-09-17 · Jar: rebuilt with `mvn -q -DskipTests package` (worker + vendored ProLeap), spike main `PlaneAccessSpike.java` run over `/tmp/vb6-spike/spike.cls` (designer block + Enum + Const + Event + Declare + `VB_PredeclaredId` + Optional/ParamArray sub + `rs!FieldName` + comments).

Method: single-file `runner.analyzeFiles` → registry walk `program.getASGElementRegistry().getASGElement(ctx)` + direct ASG getters.

## Results

| Accessor (plan AD-04 risk) | Status | Evidence from run |
|---|---|---|
| `Module.getEnumerations()` | ✅ VERIFY | `enum.count=1`, `SpikeColor` ctxLine=46 endLine=49, 2 members, each with `getPosition()`, `getValueStmt()` (LiteralImpl) and ctx text `scRed = 1` |
| `Scope.getConstants()` (module-level) | ✅ VERIFY | `const.count=1`, `APP_NAME`, `isModuleConstant=true`, value via ctx `valueStmt().getText()` = `"Spike"` |
| Event via ctx-walk + registry | ✅ VERIFY | `EventStmtContext` → registry returns `EventImpl`, name `BeforeSave`, `argList().arg().size()=1`, line 53 |
| Declare via ctx-walk + registry | ✅ VERIFY | `DeclareStmtContext` → `ProcedureDeclarationImpl`; ctx accessors give kind (`FUNCTION()`/`SUB()`), `STRINGLITERAL(0)`=lib `"kernel32"`, `STRINGLITERAL(1)`=alias, `asTypeClause()`=`As Long`, `visibility()`=Private, line 55 |
| Attribute via ctx-walk + registry | ✅ VERIFY | 5 attributes; registry returns element; `Attribute.getLiteral().getValue()` is **UNQUOTED** (`spike`, `True`, `False`) while ctx `literal(0).getText()` keeps quotes (`"spike"`) → prefer ASG value, fallback ctx |
| `Procedure.getOptionalArgs()/hasOptionalArgs()` | ✅ VERIFY | `LoadSpike` arity=2, `hasOptional=true`, `optionalCount=1`; `Arg.isOptional()`=true; ParamArray arg visible on ctx (`PARAMARRAY()`) |
| DICTIONARY_CALL via registry | ❌ NULL — ctx-walk fallback | `DictionaryCallStmtContext` IS in the parse tree (`!FieldName`, line 67) but `getASGElement(ctx)` returns NULL: ProLeap never registers it in this parse path. Emission must be **ctx-level** in `collectProcedureCalls` (receiver from parent `iCS_S_MembersCall` text, member = `ambiguousIdentifier()`) |
| `Module.getTokens()` hidden channel | ✅ VERIFY | `CommonTokenStream` present; 4 COMMENT tokens with original line numbers (42, 45, 61, 65) |
| Designer block in .cls keeps lines | ✅ VERIFY | `module.getLines()=68` = full file; enum ctx lines refer to ORIGINAL positions (46–49) with 36-line designer+attributes block above |

## Consequences for implementation

1. AD-04 primary path (ASG registry + getters) works for **enums, constants, events, declares, attributes** — no regex-lite fallback needed.
2. **DICTIONARY_CALL is the one fallback**: emit from `DictionaryCallStmtContext` nodes during the existing per-procedure ctx walk (same walk as other calls → same dedup/collapse pipeline, red-team F17). `resolution_status` starts `undefined` (worker-side); Python resolver re-derives to `external`/`late_bound` via the receiver path before publication (both in the pinned vocabulary — graph contract stays green).
3. Attribute values: use `Attribute.getLiteral().getValue()` (unquoted) — `parse_meta.module_attributes` stores `vb_predeclaredid: "True"` etc. directly comparable to `"True"`.
4. Enum member values: read `ctx.valueStmt().getText()` (e.g. `1`); empty when no `= value` clause (regex parity).
5. Comments: pair by line from hidden-channel COMMENT tokens (phase-04).
