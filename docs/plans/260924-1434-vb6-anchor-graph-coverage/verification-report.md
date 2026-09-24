# Verification Report — 260924-vb6-anchor-graph-coverage

- date: 2026-09-24
- implementation: hi-craft --full (phases 01-04)
- corpus: `/Users/hieplq1.aip/test/Bookworm-VisualBasic/Revamp1` (27 .frm, 3 .bas)
- fixture: `tests/fixtures/vb6-application` (mở rộng +4 files: modState.bas,
  clsSource.cls, clsSink.cls, frmAnchor.frm)

## Full-mode review round (post-implementation)

Delegated adversarial review (lens: code + failure-modes) — score **6.5/10**
trước fix; 1 Critical + 1 High + 3 Medium. Toàn bộ đã fix + regression test:

| # | Sev | Finding | Fix | Test |
|---|---|---|---|---|
| C1 | Critical | `Dim cn As ADODB.Connection` (+member access, KHÔNG `New` nào) → USES_TYPE trỏ external Type node không tồn tại → endpoint preflight **abort cả relations batch**. Variant: `.bas` module khớp `type_node_id` nhưng .bas không emit :Type node | (1) `type_node_id` chỉ nhận kind cls/frm/ctl/pag; (2) guard mới trong write path: row `target_label=Type` bị drop khi `target_id` không thuộc `classes ids ∪ inst_type_rows ids` | `Vb6ComReceiverWithoutNewTest` (temp fixture end-to-end, không raise, 0 USES_TYPE mồ côi) + `test_type_node_id_excludes_bas_modules` |
| H1 | High | 4 fixture mới bị `.gitignore` (tests/fixtures/**) — diff không committable | `git add -f` khi commit (mirror fixture cũ) | — |
| M1 | Medium | Nhánh variable/param lookup của With-target là dead code (`unresolved_type` downgrade trước lookup) | lookup chạy khi kind ∈ {late_bound, unresolved_type} | `test_with_typed_variable_resolves` |
| M2 | Medium | Form/MDIForm/UserControl pseudo bị match trên cả .bas (mâu thuẫn comment; false `vb6_event`) | gate designer-only; chỉ `Class` match khi không có controls | `PseudoControlGateTest` (3 case) |
| M3 | Medium | Placeholder external functions publish `exported=true` —pollute public surface | `is_private=True` trên placeholder | `test_external_placeholders_not_exported`; corpus: exported 164→**42** (đúng bằng payload-level 42) |
| M4 | Medium | External com Type node dùng chung: file owner đầu tiên bị change → cleanup xóa node, mất cạnh của module khác | `anchor_files_by_id` map ĐA owner; incremental filter check `&` trên tập file | (logic-level; covered bởi filter rework) |

Post-fix corpus re-verify (embedded LadybugDB, write sạch không abort):
165 Control / 165 HAS_CONTROL / 76 WIRED_TO (72 Control + 4 Type) /
1 USES_TYPE / 204 USES / **42 exported** / 19 static / 0 placeholder exported.

## Gates

| # | Tiêu chí | Kết quả | Evidence |
|---|---|---|---|
| M1 | exported đúng Public/Private | ✅ | `asdict_function` derives `exported`/`is_public_api` từ `is_private` (Friend=exported). Corpus write: **164 exported procs** trong graph (`Function {exported: true}` = 164). Pins: `tests/test_vb6_anchor_graph.py::ExportedPublicationTest` |
| M2 | Control nodes + HAS_CONTROL + WIRED_TO + USES member/access | ✅ | Fixture (fake-driver contract): control nodes skip pseudo; HAS_CONTROL Type→Control; WIRED_TO handler→Control, Form_Load→Type, **Class_Initialize→Type** (Class pseudo mới). Corpus (real LadybugDB): **165 Control nodes, 165 HAS_CONTROL, 72 WIRED_TO→Control, 4 WIRED_TO→Type**; 0 preflight fail |
| M3 | New→Type; COM receiver annotated; With-member gắn target | ✅ (fixture-level; corpus With=0 do duplicate-module, xem Deviations) | resolver tests 9 case mới (COM typed, New project/external, With control/form/variable, const reference, shadowing, param-typed); INSTANTIATES `Form_Load→clsOrder@clsOrder.cls` trong golden test; USES_TYPE `external::vb6/adodb.recordset` cả fixture lẫn corpus |
| M4 | ReDim rows; is_static qua write path; Global→is_global | ✅ (với denominator đạt được, xem Deviations) | corpus **13/13 ANTLR-reachable ReDim Preserve sites** captured (26 tồn tại trong nguồn, 13 nằm ở module trùng tên bị guard từ chối — pre-existing); fixture `buffer` is_static=true qua `write_variables_full` SET (schema BOOL); `Global AppStatus` → is_global=true (S1) |
| M5 | Golden UI trace multi-hop không đứt mắt (driver-level) | ✅ | Fixture: `tests/test_vb6_graph_contract.py::Vb6GoldenUiTraceTest` (11 tests: 5 mắt + INSTANTIATES + COM + redim + multi-hop traversal). **Corpus (real graph DB)**: login.cmdSubmit_Click -WIRED_TO→ cmdSubmit → USES txtUsername.Text{read} → USES main.userStatus{write} → CALLS superadmin_menu.Init → superadmin_manage_loans.Init USES main.userStatus{read} — chạy trên embedded LadybugDB |
| M6 | Parity bảng asymmetry; benchmark không regress; pins xanh; cache bumped | ✅ | `test_vb6_engine_parity.py`: bảng ANTLR-only planes (instantiations/with_targets/ui_access/redim) + exported parity 2 engine; benchmark antlr **106.1 files/s vs baseline 106.4** (p50 9.43 vs 9.40ms — noise); `PARSE_CACHE_VERSION=vb-family-v2026-09-24-1`, cả 2 pins assert theo version mới |
| M7 | Scope hygiene | ✅ | `git diff` chỉ chạm tools/vb (worker/adapter/resolver/analyzer/common) + graph schema (ladybug_schema, manifest) + writer (surgical: write_controls_full + SET is_static + write_all plane) + tests + fixtures |

## Spike conclusions (P2 DoD)

- **S1 (Global mapping): RESOLVED — không cần rewrite.**
  `VisibilityEnum` có giá trị riêng `GLOBAL` và `ScopeImpl.determineVisibility`
  map token `Global` → `VisibilityEnum.GLOBAL` (ScopeImpl.java:2403-2404).
  Worker cũ chỉ check `== PUBLIC` nên mất flag; fix = chấp nhận cả GLOBAL
  trong `variableJson` (`is_global = moduleLevel && (PUBLIC || GLOBAL)`).
  Fixture pin: modState.bas `Global AppStatus` → is_global=true.
- **S2 (Local Const/Dim): RESOLVED.** `visitVariableStmt` →
  `findScope(ctx).addVariables(ctx)` — scope là procedure, nên
  `procedure.getVariables()` chứa MỌI variableStmt của proc (Dim lẫn Static).
  `is_static`/`with_events` đọc từ token `STATIC()`/`WITHEVENTS()` trên
  `VariableStmtContext` bao ngoài. (Local Const cũng chạy qua
  `visitConstStmt → scope.addConstants` — `procedure.getConstants()` có data,
  nhưng local-const anchor nằm ngoài scope plan này.)

## Corpus evidence (tests/scan_vb6_corpus_anchors.py)

- files=30 (30 flat), **21 ANTLR-ok / 9 fallback**: 8 do duplicate
  `Attribute VB_Name` (4 cặp: addbook/addbook2, admin_createdb/
  superadmin_createdb, admin_menu/admin_menu2, user_mainmenu/user_menu —
  guard pre-existing của worker), 1 do md5.bas syntax error (pre-existing).
- source-text ground truth: 28 `ReDim` (26 Preserve), 6 `With`.
- ANTLR-reachable: **redim_rows=13 (13 preserve)**, with_targets=0 (mọi khối
  With nằm trong file bị fallback), ui_access=652 (394 member + 258 state),
  instantiations=1 (external), static_locals=19, global_vars=36,
  exported=42/224 (payload level).
- Real-graph write (embedded LadybugDB): 165 Control, 165 HAS_CONTROL,
  72+4 WIRED_TO, 1 USES_TYPE (com), 204 distinct USES (100 Control +
  104 Variable), 164 exported Functions, 19 static Variables, 1 com Type.

## Deviations from plan (đã ghi nhận)

1. **M3/M4 corpus denominator**: "26 ReDim site" và "≥80% With-member rows
   trên corpus" giả định toàn corpus parse được. Thực tế 8/30 file bị
   duplicate-module guard từ chối (pre-existing, ngoài scope plan) → corpus
   denominator là 13 ReDim reachable (13/13 = 100%) và With-target gate chuyển
   xuống fixture (frmAnchor: 2 With blocks, cả control-config lẫn form-nav —
   `Vb6GoldenUiTraceTest` xanh). Corpus với=0 được scan script ghi rõ.
2. **USES rel merge collapse**: nhiều site USES cùng (Function,target) MERGE
   về 1 cạnh (last-write-wins trên properties) — log ghi 254+189 rows nhưng
   graph giữ 204 cạnh riêng biệt. Giống semantics CALLS (`count` aggregate);
   per-site props vẫn trace được qua POSSIBLE_CALLS/`_properties` spill khi
   cần. Không phá gate nào (mọi cạnh tồn tại).
3. **`baseline.md` của plan 260917 tự regen** khi chạy suite (thiết kế của
   `test_vb6_baseline.py`): số reference-zero regex cập nhật theo fixture mới
   (13→17 files, 38→48 functions, 51→69 call edges) — corpus fixture mở rộng
   là một phần plan này.
4. **Plan follow-ups giữ nguyên**: MCP profile registration (`Control` label
   vào GENERIC_LABELS) + `resolution_status` persistence — ngoài scope.

## Runbook — resync sau merge

```bash
# full resync (KHÔNG incremental — manifest fingerprint đã đổi,
# journal in-flight sẽ INCOMPATIBLE_SCHEMA; reindex từ đầu)
make sync   # hoặc pipeline sync-equivalent với vb6 analyzer

# corpus anchor evidence
.venv/bin/python tests/scan_vb6_corpus_anchors.py \
  --root /Users/hieplq1.aip/test/Bookworm-VisualBasic/Revamp1 --json report.json

# golden + contract
.venv/bin/python -m pytest tests/test_vb6_graph_contract.py \
  tests/test_vb6_anchor_graph.py tests/test_vb6_golden.py -q
```

- PARSE_CACHE_VERSION bump (`vb-family-v2026-09-24-1`) tự invalidate parse
  cache cũ.
- Rollback: revert commit + reindex — mọi schema/writer change đều additive.
- `VB6_ANTLR_STRIP_DESIGNER=1` → controls[] rỗng → Control/HAS_CONTROL/WIRED_TO
  (control) rows tự co về 0 (builders guarded); lifecycle Type-wiring vẫn chạy.

## Test suite

VB6 suite (11 files) + schema/manifest/writer + anchor tests: **all green**
(không tính 5 test `test_ladybug_driver.py` fail PRE-EXISTING trên cây sạch —
verified bằng git stash; không liên quan thay đổi này).
