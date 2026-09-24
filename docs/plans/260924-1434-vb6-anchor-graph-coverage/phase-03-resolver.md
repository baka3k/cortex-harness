# Phase 03 — Resolver upgrades (type-aware, With-target, New→Class)

> Gate ra: M3 (New→Class đúng; COM typed receiver không còn unresolved-mù; ≥80% With-member rows gắn target trên corpus).

## Tasks

### 3.1 Typed receiver resolution (`vb6_resolver.py`)

- Bảng type→members nguồn: `variables[].type_name` (module + local, từ P2) + `controls[].type` + params/return types.
- Receiver có type khai báo tường minh:
  - Project class/interface → resolve như hiện tại (đã có interface-typed từ 260917-1628 phase-03).
  - **COM type** (`ADODB.|DAO.|RDO.|Scripting.` prefix) → KHÔNG còn route mù vào late_bound/unresolved: annotate row `com_type`, phát `USES_TYPE` (Function→Type kind="com"), member vẫn `possible` nhưng có candidate-set annotation (không bịa resolve).
- Thứ tự receiver-lookup: control name (cùng module) → variable type → param type → with-target → late-bound (fallback giữ nguyên).

### 3.2 New→Type resolution

- `instantiations[]` (có `proc` từ P2): tên class trùng project module (case-insensitive, mirror `moduleNames` hiện có) → target `symbol_id` của **Type node** (form/class đi types lane — `vb_analyzer_base.py:1118-1134`); không trùng → node Type kind="com"/external + USES_TYPE.
- Kết hợp arity khi `New` có args (Rare trong VB6 — ghi chú nếu grammar không cho).

### 3.3 With-target post-process

- Với mỗi `with_targets[]` (có `proc` + `block_end_line` từ P2): classify `expr_raw` theo thứ tự 3.1 (control → variable/param type → form/class name → unknown).
- Gắn cho mọi `ui_access[]`/call row của cùng `proc` nằm trong `[line, block_end_line]` và `via_with:true`: target_symbol + kind. Nested `With` resolve block trong cùng nhất (block_end_line của block con win). Hai flavor corpus phải xanh:
  - control config: `With cboType … .AddItem` (control member → USES Function→Control).
  - form nav: `With superadmin_createdb … .Show` (`addbook.frm:529`) → reference tới **Type node** của form (USES Function→Type) + `.Show` không tạo CALLS sai.

### 3.4 Const reference + surface list

- Const: reference trong proc → row `USES` (Function→Constant) khi tên khớp module-const (case-insensitive; shadowed local win).
- public_surface: list proc `exported=true` per module xuất trong summary payload (đầu vào M1 đã có rows).

### 3.5 Tests

- `test_vb6_resolver.py`: bảng case mới (COM typed, New project class, New external, With control, With form, With variable typed, const reference, shadowing).
- Corpus scan: % with-member rows có target ≥80% (M3); ADODB receivers trên corpus (main.bas/users.csv IO) annotated.

## Định nghĩa xong

- [ ] M3 xanh: resolver tests + corpus scan evidence ghi vào plan.md
- [ ] 0 regress: call resolution cũ (unqualified/qualified/interface) golden vẫn xanh
