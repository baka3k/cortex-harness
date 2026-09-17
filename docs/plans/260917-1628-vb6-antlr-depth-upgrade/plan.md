---
title: "VB6 ANTLR Depth Upgrade — ASG Plane Hydration, Form/Control Events, Resolver Precision, Comments"
status: complete
created: 2026-09-17
completed: 2026-09-17
mode: hi-plan --full
scope: "Chỉ stack VB6 (code-tiny/tools/vb/** + tests/**): điền các plane ANTLR đang rỗng (enums/constants/events/declares/attributes) từ ProLeap ASG, plane controls[] cho form/control + event-wiring metadata, resolver nâng cấp (interface-typed receiver, arity range, predeclared-id, dictionary call), comment extraction từ hidden channel; KHÔNG đụng schema/writer/MCP chung"
blockedBy: []
blocks: []
relatedPlans:
  - 260917-1200-vb6-antlr-call-graph
  - 260917-1139-vb6-call-capture-prediction
  - 260910-1400-csharp-roslyn-upgrade
  - 260909-0854-ignore-folders-config
sources:
  - docs/plans/260917-1628-vb6-antlr-depth-upgrade/research-digest.md
  - code-tiny/tools/vb/antlr_worker/worker/src/main/java/io/cortex/vb6/worker/Vb6Worker.java
  - code-tiny/tools/vb/vb6_antlr_adapter.py
  - code-tiny/tools/vb/vb6_resolver.py
  - code-tiny/tools/vb/vb_common.py
  - code-tiny/tools/vb/vb_analyzer_base.py
  - code-tiny/tools/vb/antlr_worker/vendor/proleap-vb6-parser/src/main/antlr4/io/proleap/vb6/VisualBasic6.g4
---

# VB6 ANTLR Depth Upgrade — ASG Plane Hydration, Form/Control Events, Resolver Precision, Comments

## Overview

Kế thừa plan `260917-1200-vb6-antlr-call-graph` (complete-with-exclusions): engine ANTLR-first đã chạy, nhưng worker hiện **vứt bỏ phần lớn dữ kiện ProLeap đã parse**. Research digest xác nhận các khoảng trống và cách vá chúng **hoàn toàn nằm trong stack VB6** — không cần đổi schema, writer hay MCP layer:

| Dữ kiện grammar/ASG đã có | Worker hiện tại | Hệ quả |
|---|---|---|
| `controlProperties` parse native (.frm designer: control lồng nhau, `BEGINPROPERTY`, FRX offsets — g4:97-126) | `materializeDesignerModule()` blank toàn bộ trước `Attribute VB_Name` (`vb6_antlr_adapter.py:47`) | Không có model control; `Command1_Click` chỉ là sub thường |
| Plane `enums/constants/events/properties` + Declare LIB/ALIAS + Attribute values | 6 plane luôn rỗng (`Vb6Worker.java:438-444`) | Parity vỡ với regex engine; Qdrant `vb_functions` thiếu nhóm symbol |
| `CallType.DICTIONARY_CALL` (default member `obj!Field`) | Whitelist `CALL_TYPES_EMITTED` drop (`Vb6Worker.java:89-95`) | Luồng default-member mất hẳn |
| `Procedure.getOptionalArgs()/hasOptionalArgs()` | Chỉ serialize arity exact | Resolver exact-arity mis-filter với optional/ParamArray |
| Hidden-channel COMMENT tokens qua `module.getTokens()` (g4:2077) | `comment=""` | Embedding Qdrant chỉ có raw code |
| `variables[].type_name` + `implements_map` trong payload | Resolver chỉ coi `Object/Variant` là late-bound | `Dim x As IShip: x.Sail` → `unresolved` |

Runtime experiment (research digest §5) đã chứng minh: **designer block parse OK trong file .cls materialized** (`ok:true, has_error:false`, line numbers giữ nguyên vị trí gốc) — mở đường giữ nguyên designer block thay vì strip, và extract `controls[]` từ chính parse hiện có (không thêm pass parse).

### Scope Challenge (3 câu)

1. **Giải vấn đề gì?** Làm giàu luồng parser/query VB6 bằng dữ kiện ProLeap đã parse nhưng worker đang bỏ: form/control + event wiring, enum/constant/event/declare symbol, default-member, type/arity precision, comments. KHÔNG phải đổi kiến trúc engine hay đụng lớp chung.
2. **In/Out?** IN: mọi thay đổi trong `code-tiny/tools/vb/**` + `tests/**` (worker Java, adapter, resolver, analyzer VB6 paths, fixtures/golden, benchmark, runbook). OUT (owner ruling 260917): schema graph (không thêm relation type), `language_writer.py` Neo4j writers, `framework_registry.py`/MCP profile `vb6`, nợ R10 (persist resolution_status trên CALLS), vba/vbscript/vbnet, `.dsr`.
3. **Biết khi nào xong?** Acceptance metrics M1-M7 dưới — mọi mục đo được bằng fixture/golden tests/benchmark/fake-driver graph tests.

### Architecture

```text
Vb6Worker.java (đang parse WHOLE-PROGRAM, chỉ thêm serialize — 0 pass parse mới)
    ├── planes mới từ ASG/ctx-walk:
    │   ├── enums[]      ← Module.getEnumerations() + EnumerationConstant
    │   ├── constants[]  ← Scope.getConstants()                          (Scope.java:327)
    │   ├── events[]     ← eventStmt ctx + ASG registry (inferred, spike 1.1)
    │   ├── declares[]   ← declareStmt ctx: LIB/ALIAS/return (inferred, spike 1.1)
    │   ├── controls[]   ← controlProperties ctx walk (chỉ .frm/.ctl/.pag materialized)
    │   └── parse_meta.module_attributes ← Attribute.getLiteral() (VB_PredeclaredId...)
    ├── functions[] += min_arity/has_optional_args/has_paramarray        (Procedure.java:38-42)
    ├── calls[] += DICTIONARY_CALL rows (default_member flag)
    └── comment/summary ← hidden-channel COMMENT attach (trên + cùng dòng)

vb6_antlr_adapter.py
    └── materializeDesignerModule: GIỮ designer block (AD-02 mới, evidence-backed);
        fallback quay lại strip nếu golden line-number test fail trên .frm thật

vb_common.py / vb_analyzer_base.py (VB6 paths — Qdrant mapping 1249-1497 IN-SCOPE)
    ├── hydration dataclasses mới khớp shape regex (vb_common.py:91-174); Declare = shape mới
    ├── node rows cho planes mới → writer lanes đã có sẵn (Enum/Constant/Event/Property)
    ├── event-wiring: handler rows nhận payload `vb6_event` + note/summary
    │   → Qdrant vb_functions (explicit mapping tại analyzer, không đụng writer)
    └── resolver (vb6_resolver.py):
        ├── interface-typed receiver: type_name + implements_map → candidates
        ├── arity range: min_arity..max (optional/paramarray)
        ├── predeclared-id: Form1.Member → member của form module
        └── dictionary call: external/late_bound + default_member, không drop
```

### Architecture Decisions (AD)

| # | Quyết định | Lý do / alternative bị bác |
|---|---|---|
| AD-01 | **Toàn bộ diff gói trong `code-tiny/tools/vb/**` + `tests/**`** (owner scope ruling). Không thêm relation type, không sửa Neo4j writers (fixed `SET` lists — research §2), không MCP profile | Event wiring qua Neo4j cần rel type mới →OutOfScope; chặn trước scope creep |
| AD-02 | **materializeDesignerModule GIỮ designer block** (supersede AD-03 của plan trước có evidence): experiment cho thấy designer trong .cls parse `ok:true`, line numbers gốc giữ nguyên (`research-digest.md` §5); extension là gate duy nhất tạo module (`VbModuleVisitorImpl.java:51-65`). **Điều kiện bắt buộc (red-team F11):** worker bỏ cửa sổ scan 60 dòng khi tìm `Attribute VB_Name` (`Vb6Worker.java:963-966`) → scan toàn bộ nội dung; .frm thật có designer >60 dòng sẽ mất module nếu không sửa | Strip là workaround cho giả định sai "grammar không parse được designer"; giữ block = controls[] lấy từ chính parse hiện có, 0 pass thêm. Fallback: env-guard quay lại strip nếu golden line-number fail trên .frm thật; khi strip active phải set `parse_meta.designer_stripped=true` + summary line ghi chú (F13 — không để controls[] im lặng biến mất) |
| AD-03 | **Plane mới mirror shape regex** (regex rows = dataclass `__dict__` dumps — hydration drop unknown keys, thiếu key bắt buộc sẽ raise: `vb_common.py:232-241`); **Declare không có tiền lệ regex** (`vb_common.py:420` thiếu modifier) → định nghĩa shape VB6-only mới + dataclass hydration riêng, parse_meta ghi rõ regex không emit declares | Engine-independent hydration là contract AD-02 cũ; phá shape = vỡ fallback cascade |
| AD-04 | **Events/Declares/Attributes không có public getter trên ASG** (research §4) → extract bằng ctx walk + `ASGElementRegistry` (cùng pattern `collectProcedureCalls`); bắt buộc spike 1.1 verify runtime trước khi serialize hàng loạt | Tránh viết code Java dựa trên accessor `inferred`; fallback rẻ: regex-lite trên `module.getLines()` trong worker (vẫn VB6-contained) |
| AD-05 | **Bump `PARSE_CACHE_VERSION` ở MỖI phase đổi payload shape (01, 02, 04)** — không phải một lần duy nhất (red-team F3); update mọi pin version (bao gồm bản trùng ở `test_vb6_antlr_worker_contract.py:27`) | Cache so khớp exact-version: một bump đầu phase-01 để cửa sổ phase-02/04 output mới bị cache cũ che ở file không đổi |
| AD-06 | **Event wiring = Qdrant payload enrichment + note/summary text** (mapping explicit tại `vb_analyzer_base.py:1249-1497` — in-scope). Neo4j-side: KHÔNG tạo cạnh sự kiện; handler vẫn tra cứu được qua semantic search và trace_flow xuất phát từ handler function | `vb_functions` là collection VB6-only; embedding text = `note or code` nên note giàu tự động улучшить search. Cạnh sự kiện Neo4j defer cho plan schema riêng |
| AD-07 | **DICTIONARY_CALL emit với `call_type="dictionary_call"` + resolution flow vào status vocab hiện có** (external/late_bound + prop `default_member`); KHÔNG đụng `call_evidence.RESOLUTION_CLASSES` | Graph contract test pin vocab `lexical_candidate` (test:127-132) — test updates nằm trong tests/** (in-scope), shared vocab không đổi |
| AD-08 | **Perf guard: chỉ thêm serialize/walk ctx, 0 pass parse mới**; giữ absolute budget đã accept (≤50 ms/file worker-internal, JVM ≤5 s startup) | Điểm yếu 96x-vs-regex đã owner-accept theo absolute budget; mọi phase phải chứng minh không vượt |

### Acceptance Metrics (định lượng)

| # | Metric | Gate |
|---|---|---|
| M1 | Plane hydration trên fixture gài enum/const/event/declare/attribute | 100% symbol gài xuất hiện trong payload với shape hợp lệ (khớp hydration dataclass) |
| M2 | DICTIONARY_CALL trên fixture `obj!Field` | Row xuất hiện (0 drop), `default_member=true`, status thuộc vocab hiện có; graph contract xanh |
| M3 | controls[] trên fixture .frm (≥3 control, có nested + BEGINPROPERTY) | Control tree đúng cha/con; handler `*_Click` gắn `vb6_event` ≥90% số handler gài; 0 false-match trên sub thường của fixture |
| M4 | Resolver precision trên golden set MỞ RỘNG (thêm interface dispatch, optional args, predeclared-id, dictionary) | Recall expected-resolvable ≥80% (giữ bar plan trước); interface-typed receiver không còn `unresolved`; optional-args không còn mis-filter |
| M5 | Comments trên fixture có comment liền kề | ≥80% declaration có comment liền kề mang `comment != ""` trong payload VÀ trong Qdrant point payload (fake-driver assert) |
| M6 | Không regress | Toàn bộ tests vb6 + engine dispatch + incremental + message_scan compat xanh (sau khi update pins chính danh); benchmark trong absolute budget |
| M7 | Scope discipline | `git diff --name-only` toàn plan chỉ chứa `code-tiny/tools/vb/**`, `tests/**`, `docs/plans/260917-1628-*/**` (+ runbook/README trong tools/vb) |

### Phase Index

| Phase | Tên | Gate ra |
|---|---|---|
| [01](phase-01-plane-hydration.md) | Rebuild jar + spike accessors + hydrate planes + arity/dictionary enrichment | M1, M2; AD-04 verify; AD-05 bump |
| [02](phase-02-form-controls-events.md) | Giữ designer block + controls[] plane + event-wiring metadata | M3; AD-02 fallback quyết định |
| [03](phase-03-resolver-upgrades.md) | Resolver: interface dispatch, arity range, predeclared-id, dictionary | M4 |
| [04](phase-04-comments-embeddings.md) | Comment extraction (hidden channel) + Qdrant payload enrichment | M5 |
| [05](phase-05-verification-parity.md) | Verification + engine parity + benchmark + runbook + scope audit | M6, M7 |

### Risks

| Rủi ro | Severity | Mitigation |
|---|---|---|
| Accessor path Events/Declares/Attributes chỉ `inferred` (chưa chạy runtime) | High | Spike 1.1 là task đầu phase-01 (rebuild jar bắt buộc); fallback regex-lite trong worker (AD-04) |
| Giữ designer block làm lệch token/index cho .frm thật (fixture thí nghiệm là .cls tối giản) | Medium | Golden line-number test mở rộng sang .frm thật có nested control + BEGINPROPERTY; fallback env-guard strip (AD-02) |
| Event-handler naming convention false positive (`cmd_OK_Click` — control name chứa `_`, `Command1_Clicked`) | Medium | Match longest-control-name-first + suffix set đóng (`VB6_EVENT_SUFFIXES` config Python); đo false-positive trên fixture (M3) |
| Payload phình (controls/planes mới) → cache churn | Low | Bump version; benchmark watch; controls chỉ emit cho designer files |
| Merge plane mới làm vỡ message_scan compat / common analyzer registry | Low | Tests compat hiện có chạy mỗi phase; 13 external touchpoints zero-mod (research §6) |
| Maven build chưa chạy trong research | Medium | Task 1.1 rebuild jar trước mọi code Java; CI-doctor note trong runbook |

### Open Questions (validate — mặc định đã chọn, owner có thể override)

| # | Câu hỏi | Mặc định |
|---|---|---|
| Q1 | Controls có thành graph node không? | Không — chỉ Qdrant payload + note (AD-01/AD-06); plan schema riêng nếu cần cạnh sự kiện |
| Q2 | `declares[]` chứa gì? | Đủ signature: name, kind (function/sub), lib, alias, return type, visibility, line |
| Q3 | Event suffix set? | Set chuẩn VB6 đóng trong Python (`VB6_EVENT_SUFFIXES`): Click, DblClick, Change, GotFocus/LostFocus, KeyDown/KeyPress/KeyUp, MouseDown/MouseMove/MouseUp, Load/Unload/QueryUnload, Activate/Deactivate, Resize, Initialize/Terminate, Error, Scroll, Validate, DragDrop/DragOver, Timer (red-team F9); prefix form/loại đặc biệt: `Form`, `MDIForm`, `UserControl` (`MDIForm_Load`, `UserControl_Initialize`); `Class_Initialize/Terminate` KHÔNG match (không phải control event — đúng chủ đích) |
| Q4 | Chiến lược gắn comment? | Comment liền kề PHÍA TRÊN (chặn bởi dòng trống) + cùng dòng; nhiều comment khối thì nối `\n` |

### Cross-Plan Notes

- **260917-1200-vb6-antlr-call-graph** (complete-with-exclusions): tiền nhiệm trực tiếp. AD-02 ở đây **supersede có evidence** AD-03 cũ (strip designer). Nợ cũ KHÔNG nằm trong plan này: R10 (persist resolution_status trên CALLS), live-graph MCP smoke — owner đã chép vào exclusion, không mở lại ở đây.
- **260910-1400-csharp-roslyn-upgrade** (in_progress): disjoint files (roslyn_worker); chỉ đúng chung quy ước CLI `--vb6-*` — không đổi.
- **260909-0854-ignore-folders-config** (active): plan này không đụng `_SKIP_DIRS`/scan config.
- **260916-ladybugdb-graph-provider**: schema/provider — plan này READ-ONLY với schema.

### Red Team Verdict (dispositions tóm tắt — chi tiết trong `red-team.md`)

17 findings (0 Critical / 5 High / 12 Medium). Xử lý:

| Finding | Disposition |
|---|---|
| F1 plane shapes lệch dataclass contract (H) | FIXED — phase-01: shape chuẩn = dataclass `vb_common.py:110-174` (source of truth), inline lists chỉ indicative |
| F2 symbol_id convention conflict (H) | FIXED — phase-01: plane mới dùng đúng convention regex (`<Name>@<rel>`), không tự chế `<Mod>.<Enum>@<rel>`; parity gate khả thi |
| F3 cache bump gap (H) | FIXED — AD-05 đổi thành bump mỗi phase payload-đổi (01/02/04) + pin trùng contract test:27 |
| F4 hydration strips fields mới (H) | FIXED — phase-01/02: mở rộng `FunctionDef` dataclass với default (regex path không vỡ) |
| F11 cửa sổ scan 60 dòng mất .frm designer dài (H) | FIXED — AD-02: worker scan full content; fixture designer >60 dòng bắt buộc |
| F6 ParamArray phá min_arity (M) | FIXED — min_arity loại trừ arg ParamArray |
| F7 claim regex extract comment sai (M) | FIXED — phase-04 bỏ claim; ANTLR là comment-authoritative |
| F9 suffix set thiếu Timer/MDIForm/UserControl (M) | FIXED — Q3 mở rộng |
| F13 strip fallback giết controls im lặng (M) | FIXED — `parse_meta.designer_stripped` + summary note khi strip |
| F15 parity gate gồm `properties` không ai hydrate (M) | FIXED — phase-05: parity set = enums/constants/events; properties đã đại diện qua functions (kind property get/let/set) |
| F17 dictionary row thiếu noise filter (M) | FIXED — phase-01: dictionary rows đi qua cùng dedup/collapse pipeline |

### Delegation Receipts

- `[delegate] role=researcher mode=spawn scope="VB6 depth upgrade digest (parity, publication flow, test pins, ProLeap accessors, designer-block runtime experiment, external touchpoints)" status=done` → `research-digest.md`
- `[delegate] role=reviewer mode=spawn lens=assumptions+failure-modes scope="plan red-team" status=done` → findings + disposition trong `red-team.md`

### Close-out (2026-09-17)

**Kết quả:** tất cả phase 01–05 hoàn tất; M1–M7 đạt. Artifact: `spike-plane-accessors.md`, `benchmark-report.md`, `resync-runbook.md`; tests mới `test_vb6_engine_parity.py`, `test_vb6_qdrant_payload.py`; suite vb6 đầy đủ xanh (engine dispatch, worker contract, golden, graph contract, resolver, incremental, message_scan compat, provider wiring).

**Evidence-backed amendments (không đổi AD):**

1. **AD-04 — spike VERIFIED, không cần fallback regex-lite** (`spike-plane-accessors.md`): enums/constants/events/declares/attributes đọc được qua ASG getters + registry như thiết kế. Riêng **DICTIONARY_CALL: `getASGElement(DictionaryCallStmtContext)` trả NULL** (ProLeap không đăng ký ở parse path này) → dòng `obj!Field` được emit **từ ctx walk** trong `collectProcedureCalls` (cùng pipeline dedup/collapse — F17), worker đặt `resolution_status="undefined"`, resolver re-derive `external`/`late_bound` (đúng vocab pin) trước khi publish. Đây là biến thể ctx-walk đã dự liệu trong AD-04, không phải fallback regex.
2. **Declare publish qua functions lane** (`kind='declare'`, Qdrant point có `lib/alias/return_type`) — nhất quán "writer lanes đã có sẵn"; không node label mới (AD-01).
3. **Bổ sung nhỏ có lý do:** resolver nhận diện tên declare → `external` (tránh `unresolved` nhiễu cho API đã khai báo); `_split_callee` tách receiver `obj!Field`.
4. **Worker `note` giờ mirror `_build_note` python** (Summary/Comment/Code sections) — embedding text `note or code` tự hưởng comment; đây chính là mục tiêu phase-04.

**M7 scope audit — exclusions:**

- `git diff --name-only` toàn plan ⊆ `code-tiny/tools/vb/**` + `tests/**` + `docs/plans/260917-1628-*/**` **trừ 3 file**:
  - `docs/plans/260917-1200-vb6-antlr-call-graph/baseline.md` — artifact tự-ghi của `tests/test_vb6_baseline.py` (hành vi có sẵn của test): chạy suite với corpus mới (thêm modApi/clsEvents, frmMain designer >60 dòng) khiến reference-zero được ghi lại. Không phải edit thủ công; không thể tránh khi chạy suite.
  - `.cortext-harness/sync-state/sync-code.lock`, `doc-tiny/http:/.localhost:6333.cortex-owner.lock` — dirty TRƯỚC khi plan bắt đầu (runtime lock files, không thuộc plan, không commit).
- 13 external touchpoints (research §6): zero-mod giữ nguyên; `test_analyzer_provider_wiring` + `test_vb6_message_scan_compat` xanh.

**AD-02 final:** keep-designer là mặc định; golden line-number .frm thật (designer 99 dòng, nested Frame→TextBox, BEGINPROPERTY, Tab(n).Control(m)) XANH — **không dùng fallback strip**; guard `VB6_ANTLR_STRIP_DESIGNER=1` + `parse_meta.designer_stripped` + summary note vẫn giữ như bảo hiểm (F13).

### Review cycle (full mode, lens=code — 1 cycle, 7 findings: 0 Critical / 1 High / 6 Medium)

| Finding | Disposition |
|---|---|
| R1 (H) claim "declare → external" chưa implement | **FIXED** — `VB6ModuleRegistry.api_names` seed từ `payload["declares"]`; nhánh unqualified khớp tên declare → `external`; unit test `test_declared_api_call_external` |
| R2 (M) arity range làm mất exact-resolution (ambiguous nhầm) | **FIXED** — exact-arity hits ưu tiên trước, range chỉ là fallback (locals + public paths); test `test_exact_arity_beats_range_match` |
| R3 (M) interface dispatch chạy trước concrete type module | **REJECTED (plan-conformant)** — đúng semantics VB6 + plan 3.1: khi type_name đang được `Implements` thì biến đó là interface-typed, dispatch qua implementers là đúng đích; fallback concrete-type module chỉ chạy khi KHÔNG có implementer (đã vậy) |
| R4 (M) `_FORM_INTRINSIC_MEMBERS` dead code; missing member mọi loại → external | **FIXED** — external chỉ khi member ∈ intrinsic set; missing non-intrinsic (typo) → `unresolved`; test cập nhật |
| R5 (M) chained `rs!Field.Count` mất dict row | **MITIGATED + documented** — ProLeap parse-recovery gộp `!Field` vào members-call ở dạng chained (hạn chế grammar vendored, ngoài scope); bù: analyzer đặt `default_member` cho mọi row có `!` trong callee_name và resolver route qua dictionary branch (status vẫn đúng; M2 guarantee giữ ở mức row+prop) |
| R6 (M) dict display/dedup fragile | **FIXED** — display = ancestor nhỏ nhất chứa `!member` KHÔNG bắt đầu bằng `!` (fallback chuỗi `!…`); dedup key thêm display + column (`rs!A` vs `rs2!A` cùng dòng = 2 site) |
| R7 (M) fixtures bị `.gitignore tests/fixtures/**` nuốt (corpus cũ cũng vậy) | **FIXED** — `git add -f tests/fixtures/vb6-application` (tiền lệ aspnet force-tracked); không sửa `.gitignore` (ngoài M7 paths) |
