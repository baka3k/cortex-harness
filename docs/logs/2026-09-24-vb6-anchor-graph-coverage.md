# VB6 anchor graph coverage: Control nodes, UI wiring, type/state edges — 2026-09-24

## Context

Plan `docs/plans/260924-1434-vb6-anchor-graph-coverage/` (successor of the 260917-1628 depth
upgrade, which deliberately deferred all schema/writer changes to "a separate schema plan" —
this is that plan). The 4 user-defined anchor groups (procedure/type/event/state) were only
partially traceable: controls existed as Qdrant payload with no graph node, `New X` was
filtered out of calls, `With` targets were dropped, ReDim had zero representation, and
`exported` was hardcoded False. The complaint: form actions flowing down to UI components
were not traceable. Implemented same-day via `hi-craft --full` (research digest + red-team +
4 phases + full review cycle); landed as commit `e8b47c7` on develop. Scope: `code-tiny/tools/vb/**`
+ graph schema (ladybug_schema/manifest) + surgical writer changes + tests — MCP query layer
explicitly left as follow-up.

## Change

- Schema (AD-01/AD-02/AD-03): `:Control` node spec + **manifest id-index** (required —
  `group_typed_relations` fail-closed without it, `code-tiny/tools/graph/schema/manifest.py:172`);
  new rels `HAS_CONTROL` (Type→Control), `WIRED_TO` (Function→Control|Type — new rel, HANDLES
  untouched), `INSTANTIATES` (Function→Type); `USES` gains (Function,Control) pair;
  `Variable.is_static` BOOL column (`code-tiny/tools/graph/schema/ladybug_schema.py:196`).
- Writer (surgical): `write_controls_full` + `controls=` plane on `write_all` (written before
  relations so endpoint MATCHes resolve, `code-tiny/tools/graph/writer/language_writer.py:1696`);
  `write_variables_full` SETs `is_static` with `coalesce` (`:1682`).
- Worker anchor planes (pure ctx-walks, no extra parse pass): `instantiations[]` (vsNew +
  `As New` + `With New`), `with_targets[]` (proc + `block_end_line` — nested-With disambiguation),
  `ui_access[]` (member read/write via LetStmt/SetStmt LHS detection + bare module-state refs
  filtered by a program-wide name set; handles ICS_S **and ICS_B/ECS member-call shapes** —
  statement-position calls parse as `ICS_B_MemberProcedureCallContext`, found via Java probe),
  `redim[]` (`Vb6Worker.java:1013+`). `functions[]` += param_types/param_names/return_type;
  variables += is_static/with_events; `is_global` accepts `VisibilityEnum.GLOBAL`
  (spike S1: `Global` is a DISTINCT enum value, `ScopeImpl.java:2403` — no content rewrite needed).
- Resolver: typed COM receivers (`ADODB.|DAO.|RDO.|Scripting.`) annotated `com_type` + USES_TYPE
  instead of blind late_bound; `New X` → project :Type node vs external `external::vb6/<t>` com
  node; With-target classification order control → typed var/param → form/class → unknown with
  innermost-block member attachment; const reference with local shadowing
  (`code-tiny/tools/vb/vb6_resolver.py:495+`).
- Analyzer write path: guarded builders (`control_rows`/`has_control_rows`/`wiring_rows`/
  `instantiation_rows`/`ui_access_rows`/`redim_rows` — target-in-batch, red-team M4);
  resync filter extended to the new rels with a **multi-owner file map** for shared external
  nodes; Qdrant control points + exported flag; `parse_meta.public_surface`;
  `match_event_handlers` returns wired triples and Class pseudo-control wires
  Class_Initialize/Terminate → module Type node (designer-only gating preserved for
  Form/MDIForm/UserControl).
- Cache: `PARSE_CACHE_VERSION` → `vb-family-v2026-09-24-1`; both pins now assert-equality
  against the imported constant; pre-existing red `test_vb6_engine_dispatch.py:38` fixed as
  part of the bump.
- Fixtures: +4 files (modState.bas, clsSource.cls, clsSink.cls, frmAnchor.frm) force-tracked,
  covering every zero-in-corpus anchor (`tests/fixtures/vb6-application/`).

## Review round (full mode)

1 Critical + 1 High + 3 Medium, all fixed with regression tests: **C1** — `Dim cn As
ADODB.Connection` + member access with no `New` anywhere produced an unguarded USES_TYPE row
→ typed-rel endpoint preflight **aborted the entire relations batch** (fixed: `type_node_id`
restricted to class-module kinds + write-path Type-target guard); M1 With-target dead branch;
M2 pseudo-control designer gating; M3 placeholder external functions leaked `exported=true`
(corpus public surface 164→42, now matches payload truth); M4 shared external node owner mapping.
Fixtures gitignore hazard (H1) resolved via `git add -f`.

## Impact

- VB6 projects gain queryable UI/state/type anchors: 165 Control nodes, 165 HAS_CONTROL,
  72+4 WIRED_TO, 204 USES, 1 USES_TYPE on the Bookworm corpus through a REAL embedded
  LadybugDB write (no preflight abort). Golden UI story verified at driver level on corpus:
  `login.cmdSubmit_Click` → WIRED_TO cmdSubmit → USES txtUsername.Text{read} → USES
  main.userStatus{write} → CALLS superadmin_menu.Init → menus read userStatus back.
- Schema fingerprint change ⇒ journal in-flight rejects old catalogs (`INCOMPATIBLE_SCHEMA`) —
  **full resync required**, runbook in verification report. Other languages unaffected
  (cobol/cplus/csharp writer suites green). Benchmark: no regress (106.1 vs 106.4 files/s).
- Known corpus limitation: 8/30 Bookworm files rejected by the pre-existing duplicate
  `Attribute VB_Name` guard → ReDim denominator is 13/13 reachable (26 in source), With
  blocks unreachable on corpus (gate carried by fixture). MCP profile for `:Control` label
  registration is a recorded follow-up.

## Decision

- Schema re-open (option c hybrid from the digest): real node plane only for Control (the UI
  trace complaint needs a first-class queryable entity + new rel types for its edges);
  type/state anchors ride EXISTING `USES`/`USES_TYPE` pairs — zero new rel types for groups
  2/4, keeping the blast radius to one manifest id-index + one writer lane.
- `Global` fixed at the enum check rather than the planned content-rewrite fallback: reading
  `ScopeImpl` showed GLOBAL survives into the ASG; a rewrite would have touched every file's
  content hash for nothing.
- WIRED_TO instead of reusing HANDLES: HANDLES is Endpoint→Function with a live MCP reader —
  reuse would flip edge semantics and destroy existing rows.

## References

- plan: ./docs/plans/260924-1434-vb6-anchor-graph-coverage/plan.md
- report: ./docs/plans/260924-1434-vb6-anchor-graph-coverage/verification-report.md
- corpus scan: tests/scan_vb6_corpus_anchors.py
- commit: e8b47c7
- prior: ./docs/logs/2026-09-17-vb6-antlr-depth-upgrade.md
