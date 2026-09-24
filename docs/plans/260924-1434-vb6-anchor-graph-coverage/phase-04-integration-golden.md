# Phase 04 — Integration, Golden UI trace, parity, benchmark

> Gate ra: M2, M5, M6, M7 — plan đóng.

## Tasks

### 4.1 Write-path integration (nối P1 khung + P2/P3 dữ liệu)

- `vb_analyzer_base.py` write path: đổ payload thật vào builders P1:
  - `Control` rows từ `controls[]` (qualified_name = `<Module>.<Control>`; kind = control type; class_name = form) → `write_all(controls=...)` (plane P1). Pseudo-control `Form`/`MDIForm`/`UserControl` **không tạo Control node**.
  - `HAS_CONTROL` từ **Type node** của form (types lane — `vb_analyzer_base.py:1118-1134`) → control rows (control id: `<Module>.<Control>@control`).
  - `WIRED_TO` từ `match_event_handlers` (`vb_analyzer_base.py:744` — trả cặp (function, control) thay vì int; caller duy nhất là summary counter `:1007`, đã verify contained): handler control thật → Control node; pseudo-control/lifecycle (`Form_Load`, `Class_Initialize/Terminate` — mở suffix set) → **Type node** của form/module. Anti-FP giữ nguyên (evt phải thuộc suffix set, ctrl phải tồn tại).
  - `USES` (Function→Control) từ `ui_access[]` resolved; properties `{member, access}`.
  - `INSTANTIATES` / `USES_TYPE` từ P3.
  - `USES` mutation rows từ `redim[]` (`{mutation:"redim_preserve"}`); const USES rows.
  - Mọi builder **guarded theo target-in-batch** (P1): STRIP_DESIGNER → controls[] rỗng → 0 Control/WIRED_TO control-rows, lifecycle Type-wiring vẫn chạy.
- **Resync filter mở rộng** (red-team M1): predicate source-or-target (:1332-1349, hiện chỉ calls/possible) áp cho cả rows HAS_CONTROL/WIRED_TO/INSTANTIATES/USES/USES_TYPE mới — nếu không, incremental reindex mất cạnh từ source unchanged.
- Qdrant mapping: payload function/control mở rộng (mirror pattern `vb6_event`).

### 4.2 Golden UI trace (M5 — driver-level)

- Graph-contract test mới: fixture tái hiện user story login-style: `cmdSubmit_Click -WIRED_TO→ cmdSubmit` → `-USES→ txtUsername {member:"Text", access:read}` → `-USES→ main.userStatus {access:write}` → `-CALLS→ superadmin_menu.Init` → menu `-USES→ main.userStatus {access:read}`; `Form_QueryUnload -WIRED_TO→ <form Type>`.
- Multi-hop query helper (trong test): từ control name đến tập state/proc bị ảnh hưởng — khép kín không đứt mắt. Truy vấn dùng driver trực tiếp (MCP profile là follow-up — plan.md Follow-ups).

### 4.3 Parity + benchmark (M6)

- `test_vb6_engine_parity.py`: bảng asymmetry chính thức — anchors ANTLR-only (control node, redim, static, with-target, com_type, instantiations, with_events) vs regex shared (decl/call/exported-sau-P1).
- Benchmark: **`tests/benchmark_vb6_parse_quality.py`** (duy nhất — không có bản tools/vb, red-team M5c): chạy trước/sau, gate không regress quá ngưỡng hiện hành.

### 4.4 Reindex corpus + verification (M7)

- **Full resync** (không incremental — manifest fingerprint đã đổi ở P1, journal cũ `INCOMPATIBLE_SCHEMA`): chạy analyzer trên Bookworm Revamp1; verification report ghi counts per anchor (controls, handlers wired, instantiations, redim=26, static, exported) + golden trace output.
- `git diff --stat` spot-check scope (M7); runbook: lệnh resync cụ thể + lưu ý fingerprint/journal.

## Định nghĩa xong

- [ ] M2: HAS_CONTROL đầy đủ; WIRED_TO ≥95% planted, 0 false-match; lifecycle → Type wiring; USES control rows có member/access
- [ ] M5: golden multi-hop trace xanh (driver-level)
- [ ] M6: parity bảng + benchmark report trong plan dir; cả 2 pins xanh; cache bumped (P2)
- [ ] M7: diff scope sạch (tools/vb + schema + writer surgical + tests); verification report trong plan dir
