---
title: "VB6 Anchor Graph Coverage — 4 nhóm anchor (procedure/type/event/state) + UI traceability"
status: complete
created: 2026-09-24
completed: 2026-09-24
mode: hi-plan --full
scope: "Chỉ stack VB6 + schema graph: capture đủ 4 nhóm anchor (procedure/type/event/state) từ ProLeap ASG lên đồ thị truy vấn được — node Control (manifest id-index + writer plane riêng) + cạnh HAS_CONTROL/WIRED_TO/INSTANTIATES, USES/USES_TYPE mở rộng (type_decl, com_type_ref, with_block_target, member access, redim mutation, const reference), is_static/global/exported publication, WithEvents; resolver nâng cấp (typed COM receiver, New→Class, With-target); golden trace form-action login→menu. Writer chỉ chạm 3 điểm surgical: write_controls_full mới, write_variables_full SET is_static, manifest id-index. KHÔNG đụng MCP query layer (follow-up riêng), embedding pipeline, dialect khác (vba/vbscript/vbnet), .dsr"
blockedBy: []
blocks: []
relatedPlans:
  - 260917-1628-vb6-antlr-depth-upgrade
  - 260917-1200-vb6-antlr-call-graph
  - 260917-1139-vb6-call-capture-prediction
sources:
  - docs/plans/260924-1434-vb6-anchor-graph-coverage/research-digest.md
  - docs/plans/260924-1434-vb6-anchor-graph-coverage/red-team.md
  - code-tiny/tools/vb/antlr_worker/worker/src/main/java/io/cortex/vb6/worker/Vb6Worker.java
  - code-tiny/tools/vb/vb6_antlr_adapter.py
  - code-tiny/tools/vb/vb6_resolver.py
  - code-tiny/tools/vb/vb_analyzer_base.py
  - code-tiny/tools/vb/vb_common.py
  - code-tiny/tools/graph/schema/ladybug_schema.py
  - code-tiny/tools/graph/schema/manifest.py
  - code-tiny/tools/graph/writer/language_writer.py
  - code-tiny/tools/graph/writer/query_contract.py
  - /Users/hieplq1.aip/test/Bookworm-VisualBasic/Revamp1 (27 .frm, 3 .bas — corpus xác thực)
---

# VB6 Anchor Graph Coverage — 4 nhóm anchor + UI traceability

## Overview

Kế thừa 2 plan VB6 đã complete: engine ANTLR-first (`260917-1200`) hydrate planes + controls[]/event-wiring payload (`260917-1628`). Nhưng đo trên 4 nhóm anchor người dùng định nghĩa, **đồ thị hiện chỉ truy được nhóm 1 (procedure)**; nhóm 2/3/4 hoặc bị drop ở worker, hoặc chỉ tồn tại dạng payload Qdrant chứ không có cạnh/node để trace. Đúng như complaint: "chưa trace được hết — các action từ form đi xuống, các UI component".

### Coverage matrix (evidence từ research-digest + red-team verify chéo)

| Nhóm | Anchor kind | Hiện trạng | Evidence | Plan |
|---|---|---|---|---|
| 1 | procedure_decl / procedure_call | ✅ captured (gồm `Call x`, implicit paren-less) | `Vb6Worker.java:94-103,549-569,610-653` | giữ |
| 1 | module_qualified_call | ✅ resolve cross-module qua receiver branch | `vb6_resolver.py:391-412` | giữ |
| 1 | public_surface | ⚠️ payload có `is_private` nhưng publication hardcode `exported: False` (cột `exported` sẵn trong schema, writer đã SET) | `vb_common.py:1223`; `ladybug_schema.py:196-201`; `language_writer.py:1372-1374` | P1 (không cần bump cache — payload shape không đổi) |
| 2 | type_decl | ⚠️ `variables[].type_name` có; **param/return type không** | digest §Group2 | P2+P3 |
| 2 | com_type_ref | ❌ resolver route COM receiver → late_bound/unresolved | `vb6_resolver.py` | P2+P3 |
| 2 | class_instantiation (`New <CLS>`) | ❌ bị filter chủ đích khỏi calls[] | `Vb6Worker.java:765-771` | P2+P3 |
| 2 | with_block_target | ❌ `.Member` rows có nhưng mất target; grammar parse được | `VisualBasic6.g4:620-621`; corpus `addbook.frm:529` | P2+P3 |
| 3 | with_events | ❌ regex match rồi vứt group; worker không emit flag | `vb_common.py:488,1097-1141` | P2 |
| 3 | event_handler | ⚠️ chỉ là payload flag `vb6_event` (Qdrant), **không có cạnh control→handler** | `vb_analyzer_base.py:728-791` | P1 |
| 3 | auto_lifecycle | ⚠️ `Form_*` match qua suffix; `Class_Initialize/Terminate` chủ đích bỏ | `vb_analyzer_base.py:728` | P1 (WIRED_TO form Type node) |
| 4 | global_decl / module_const | ⚠️ node Variable/Constant có `is_global`/`value`/`type_name`; `Global` keyword chưa verify mapping ProLeap | `ladybug_schema.py:211-213`; digest | P2 |
| 4 | static_local | ⚠️ có row với `procedure_name` nhưng thiếu cờ `is_static` (dataclass + writer SET đều thiếu) | `vb_common.py:27-53,1429-1453`; `language_writer.py:1631-1653` | P1+P2 |
| 4 | redim_preserve | ❌ zero representation; grammar parse được; **26 site trong corpus** | `VisualBasic6.g4:461-463`; digest | P2+P3 |

### User story chuẩn Golden (corpus, đã verify thủ công)

`login.frm`: `cmdSubmit_Click` (event handler trên control `cmdSubmit`) → đọc control state `txtUsername.Text` / `txtPassword.Text` → so CSV → ghi **global state** `main.userStatus`, `main.currentUsername` (khai báo `Public` ở `main.bas`) → gọi **form navigation** `superadmin_menu.Init` / `user_menu.Init` (default-instance qua `VB_PredeclaredId = True`) → `superadmin_menu` đọc lại globals. Hiện tại chỉ mắt `CALLS` (`Init`) sống sót; mắt control-read, state-write, UI-nav qua `.Show/.Hide` đều đứt.

### Scope Challenge (3 câu)

1. **Giải vấn đề gì?** Biến 4 nhóm anchor từ "regex/payload một phần" thành **cấu trúc đồ thị truy vấn được**, khép kín chuỗi form-action → UI component → module → state. Không phải làm lại engine (ANTLR-first đã có).
2. **In/Out?** IN: `code-tiny/tools/vb/**` (worker Java, adapter, resolver, analyzer, common), `code-tiny/tools/graph/schema/ladybug_schema.py` + `schema/manifest.py` (id-index Control), `code-tiny/tools/graph/writer/language_writer.py` (3 điểm surgical: `write_controls_full` mới, SET `is_static`, không đổi phần khác), `tests/**` (golden, graph-contract, resolver, qdrant payload, parity, benchmark). OUT (kế thừa ruling 260917 + mở rộng): MCP query layer/profile registration (follow-up riêng — xem Follow-ups), `framework_registry.py`, embedding pipeline, dialect vba/vbscript/vbnet, `.dsr`, Spring/ASPNET writers.
3. **Biết khi nào xong?** Acceptance M1-M7 (dưới) — mọi mục đo bằng fixture/golden/graph-contract/benchmark; golden UI trace login→menu chạy multi-hop liên tục (driver-level).

### Architecture Decisions

| AD | Quyết định | Lý do |
|---|---|---|
| AD-01 | **Node `Control` mới**: `_NODE_SPECS` + **manifest `_id_indexes`** (`schema/manifest.py` — bắt buộc, `query_contract.py:27-31` raise nếu endpoint label thiếu id-index) + **writer plane riêng `write_controls_full`** (pattern `write_all` hiện có không có generic node plane — `language_writer.py:2757-2836`). Cạnh **`HAS_CONTROL`** (**Type→Control** — form VB6 đi qua types lane `:Type`, KHÔNG phải `:Class`, `vb_analyzer_base.py:1118-1134` + pin `test_vb6_graph_contract.py:194-199`), **`WIRED_TO`** (Function→Control — rel mới, không tái dụng `HANDLES` vì đã là Endpoint→Function và MCP read side đang tiêu thụ, `ladybug_schema.py:394-396`, `framework_registry.py:84`). Pseudo-control `Form`/`MDIForm`/`UserControl` **không tạo Control node**; handler lifecycle của chúng (`Form_Load`, `Class_Initialize`…) → `WIRED_TO` (Function→**Type** của form/module — target luôn tồn tại ở types lane). Member access trên control dùng `USES` mở thêm pair (Function→Control), chi tiết read/write/property trong `properties` spill (đã verify persist qua `ladybug_driver.py:1065-1083`) | "UI component" là khái niệm truy vấn trực tiếp; `:Class` endpoint sẽ fail-closed ở preflight (red-team C1); tái dụng `HANDLES` là destructive overwrite + đảo chiều ngữ nghĩa (red-team H1) |
| AD-02 | `New <CLS>` → plane **`instantiations[]`** riêng; cạnh **`INSTANTIATES`** (Function→**Type**) cho project class (types lane); class ngoài (ActiveX/COM) → node `Type` (kind="com") + `USES_TYPE` | Tách khởi tạo khỏi gọi hàm, giữ two-tier CALLS/POSSIBLE_CALLS; endpoint Type khớp thực tế write path (red-team C1) |
| AD-03 | Type/state edges **tái sử dụng rel có sẵn**: type_decl (param/return/var) → `USES_TYPE`; redim_preserve → `USES` với `properties.mutation="redim_preserve"`; const reference → `USES` (Function→Constant); With-target member rows post-process ở resolver rồi phát `USES`/`CALLS` đúng target. `Variable` thêm cột **`is_static`** — kèm `VariableDef.is_static` + `asdict_variable` (`vb_common.py:27-53,1429-1453`) + SET list `write_variables_full` (`language_writer.py:1631-1653`), nếu không cột là dead weight (red-team H2) | Zero rel type mới cho nhóm 2/4; schema edit tập trung |
| AD-04 | `exported`/`is_public_api` publication từ visibility payload (`is_private` đã có cả ANTLR lẫn regex path — `Vb6Worker.java:545,568`, `vb_common.py:790`; writer đã SET `exported/is_public_api` — `language_writer.py:1372-1374`); parity regex engine **không nâng** — parity test ghi nhận asymmetry | 1 dòng fix, không bump cache; regex fallback giữ nguyên |

### Pipeline sau plan

```text
Vb6Worker.java (ctx-walk, 0 pass parse mới)
  ├── instantiations[]  ← New <CLS> (bỏ filter moduleNames cho New) {proc, name, line}
  ├── with_targets[]    ← With <EXPR> {proc, expr_raw, line, block_end_line}
  ├── ui_access[]       ← member read/write {proc, receiver_raw, member, access, via_with, line}
  ├── redim[]           ← ReDim [Preserve] {proc, name, preserve, line}
  ├── variables[] += is_static / is_global / with_events
  ├── functions[] += param_types/return_type
  └── controls[] (đã có từ 260917-1628)
        ↓
vb6_antlr_adapter.py → payload planes mới
vb_analyzer_base._hydrate_payload → whitelist mở cho planes mới (không thì bị drop silently, :320-337)
vb6_resolver.py: COM-typed receiver, New→Class(Type), With-target member resolution, const reference
vb_analyzer_base.py: rows Control/HAS_CONTROL/WIRED_TO/INSTANTIATES/USES/USES_TYPE + exported/is_static
  + resync filter mở rộng cho rel mới (source-or-target, mirror :1332-1349)
ladybug_schema.py: _NODE_SPECS(Control) + _REL_SPECS(HAS_CONTROL, WIRED_TO, INSTANTIATES) + USES pair (Function,Control) + Variable.is_static
schema/manifest.py: Control id-index
language_writer.py: write_controls_full + SET is_static
        ↓
Golden UI trace: cmdSubmit_Click -WIRED_TO→ cmdSubmit; -USES→ txtUsername (read .Text);
-CALLS→ superadmin_menu.Init; -USES→ main.userStatus (write); main.userStatus ← đọc ở menu
```

### Milestones / Acceptance

| # | Tiêu chí | Đo bằng |
|---|---|---|
| M1 | Mọi proc VB6 Public/Friend có `exported=true` trong graph; Private=false; fixture pin | golden + graph-contract |
| M2 | Control nodes = controls[] (nested flatten, `HAS_CONTROL` từ **Type** form); handlers planted wired `WIRED_TO` ≥95%, **0 false-match**; pseudo-control handlers (`Form_Load`, `Class_Initialize/Terminate`) → `WIRED_TO` Type node; member access control → `USES` có properties {member, access:read/write}. *Driver-level: MCP profile registration là follow-up (không trong M này)* | fixture .frm thật + planted |
| M3 | `New <project class>` → `INSTANTIATES` đúng **Type** node; `As ADODB.*`-typed receiver không còn rơi `unresolved` mù mà annotated + `USES_TYPE`; **≥80% member rows trong With block** trên corpus gắn đúng target (control config + form nav `addbook.frm:529`) | resolver tests + corpus scan script |
| M4 | 26 site `ReDim Preserve` corpus → mutation rows đủ; `is_static` flag đúng **qua write path thật** (không chỉ unit row); `Global` → `is_global=true` (spike VisibilityEnum.GLOBAL; fallback rewrite `Global`→`Public`, KHÔNG `Public Dim` — grammar `variableStmt` không chấp nhận, `g4:600-601`) | corpus scan + fixture synthetic + graph contract |
| M5 | **Golden UI trace login→menu**: multi-hop query không đứt mắt nào (control, state, call) trên fixture tái hiện corpus — driver-level | graph-contract test mới |
| M6 | Parity test ghi asymmetry ANTLR-only anchors; `tests/benchmark_vb6_parse_quality.py` không regress quá gate; `PARSE_CACHE_VERSION` bump (P2); pins chuyển sang **assert equality với imported version** (`test_vb6_antlr_worker_contract.py:29` hiện là local constant không pin; `test_vb6_engine_dispatch.py:38` đang đỏ sẵn — pin `vb-family-v2026-09-17-4` vs actual `09-18-1`, sửa như một phần task bump); jar rebuild mtime-stamp | tests + benchmark report |
| M7 | Scope hygiene: `git diff` chỉ chạm tools/vb + schema (ladybug_schema, manifest) + writer (3 điểm surgical) + tests | spot-check |

### Phases

1. **Phase 01 — Schema, manifest, writer plane & publication** (`phase-01-schema-publication.md`): `_NODE_SPECS`/`_REL_SPECS` + manifest id-index + `write_controls_full` + `Variable.is_static` end-to-end + `exported` publication + smoke rel mới qua typed path. Gate: schema/graph-contract xanh trước khi có payload mới.
2. **Phase 02 — Worker planes + hydration + fixtures** (`phase-02-worker-planes.md`): 4 plane mới + flags trong `Vb6Worker.java`; adapter; **`_hydrate_payload` whitelist**; fixture synthetic cho anchor 0-corpus; jar rebuild + PARSE_CACHE_VERSION + pins (gồm pin đỏ có sẵn).
3. **Phase 03 — Resolver upgrades** (`phase-03-resolver.md`): COM-typed receiver, New→Type, With-target post-process (dùng `block_end_line`), const reference, surface list.
4. **Phase 04 — Integration, golden UI trace, parity, benchmark** (`phase-04-integration-golden.md`): write-path rows nối phases 1-3 + **resync filter mở rộng**, golden M5, parity M6, reindex corpus + verification report.

### Risks / Open questions

- `Global` → ProLeap `VisibilityEnum.GLOBAL` mapping: **unknown** — phase 02 spike S1 check enum cụ thể; fallback rewrite `Global` → `Public` (giữ 1:1 line; cấm `Public Dim`).
- Local `Const`/`Dim` trong `procedure.getVariables()`: **unknown** — spike S2; nếu không, ctx-walk.
- Corpus **0** occurrence: `Global`, `WithEvents`, COM typed receiver, `!` bang → fixture synthetic bắt buộc.
- Manifest fingerprint: thêm id-index đổi `CODE_GRAPH_SCHEMA.fingerprint` — journal in-flight fail recovery `INCOMPATIBLE_SCHEMA` (`journal/consumer.py:307-315`); chấp nhận (reindex P4 là bắt buộc rồi), ghi runbook.
- Pre-existing red: `test_vb6_engine_dispatch.py:38` pin cache version đã lệch trước plan này — xử lý trong P2 task bump, không tính là regress.
- Missing-target rows: `USES`/`WIRED_TO` không nằm `_OPTIONAL_EXTERNAL_RELATION_TYPES` — row trỏ target thiếu sẽ **abort cả batch** (fail-closed). Builders chỉ emit khi target có trong batch; STRIP_DESIGNER env → controls[] rỗng → 0 rows (guarded).

### Spike resolutions (post-implementation)

- **S1 RESOLVED**: `VisibilityEnum.GLOBAL` tồn tại và được map từ token `Global` (ScopeImpl.java:2403-2404) — KHÔNG rewrite content; fix = worker chấp nhận `PUBLIC || GLOBAL` cho `is_global`.
- **S2 RESOLVED**: `procedure.getVariables()` chứa mọi `variableStmt` của proc (Dim + Static); `is_static`/`with_events` đọc từ `STATIC()`/`WITHEVENTS()` trên `VariableStmtContext` bao ngoài.
- **M3/M4 corpus denominator**: 8/30 file corpus bị duplicate-`VB_Name` guard từ chối (pre-existing) → ReDim reachable = 13/13; With-target gate chuyển xuống fixture. Chi tiết: `verification-report.md`.

### Follow-ups (ngoài scope plan này)

- MCP profile `visual_basic`: đăng ký label `Control` + rels mới vào GENERIC_LABELS/GENERIC_RELATIONSHIPS (hiện là `_generic_profile`, `framework_registry.py:614-616`; pattern cplus `UIControl` ở `fastmcp_server.py:362-370`) — để truy vấn được từ MCP layer.
- Persist `resolution_status` trên CALLS (nợ R10 từ plan 260917-1628).

## Resync / Rollback

- Reindex corpus sau merge: PARSE_CACHE_VERSION bump (P2) tự invalidate cache; **manifest fingerprint đổi → journal recovery từ chối catalog cũ** — chạy full resync, không incremental.
- Rollback schema: specs/thêm manifest/writer đều additive — revert commit + reindex; rel cũ không phá.
- Env-guard giữ nguyên: `VB6_ANTLR_STRIP_DESIGNER=1` → controls[] rỗng → Control/WIRED_TO rows tự co về 0 (builders guarded), không crash.
