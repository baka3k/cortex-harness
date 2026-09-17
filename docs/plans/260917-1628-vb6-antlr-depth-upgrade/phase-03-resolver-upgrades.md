# Phase 03 — Resolver Precision: Interface Dispatch, Arity Range, Predeclared-Id, Dictionary

> Gate ra: M4 (recall ≥80% trên golden mở rộng; interface-typed receiver không còn `unresolved`; optional-args hết mis-filter; predeclared-id resolve; dictionary không drop).

## Tasks

### 3.1 Interface-typed receiver dispatch

`vb6_resolver.py` — mở rộng `find_late_bound_variable` logic (hiện chỉ `object/variant` → late_bound, `vb6_resolver.py:62,172`):

- Receiver var có `type_name` ∈ interfaces (từ `implements_map` của payload — module được implement) → **resolver tự dựng reverse map interface→implementers từ implements_map lúc build registry** (worker không trả reverse — red-team F4-context) → tra implementers:
  - Duy nhất 1 implementer có member khớp tên → `callee_id` + `resolution_status="name_resolved"` (chỉ khi method tồn tại trên implementer).
  - Nhiều implementer / nhiều member trùng tên → `resolution_status="ambiguous"` + `candidate_ids` (đi vào POSSIBLE_CALLS như hiện trạng).
  - Không implementer nào có member → giữ `unresolved` (đúng thực tế: member không thuộc interface).
- `type_name` là class khác trong project (không phải interface) → nếu module type_name tồn tại và có member khớp → resolve qualified-style; else behavior cũ.

### 3.2 Arity range (optional/ParamArray)

- Thay exact-arity filter (resolve unqualified + project-public) bằng range check dùng `min_arity`/`has_optional_args`/`has_paramarray` từ phase-01:
  - hợp lệ khi `args == callee_arity` HOẶC (`has_optional_args` và `args >= min_arity` và `args <= arity`) HOẶC (`has_paramarray` và `args >= min_arity`) — với `min_arity` đã loại trừ ParamArray arg (red-team F6) nên `F(a, b, ParamArray rest)` cho `min_arity=2`, call `F(1,2)` hợp lệ.
  - Fallback regex path (không có min_arity — defaults 0/False): giữ exact-arity cũ (không regress).
- Call site nhiều candidate: range check chạy TRƯỚC khi kết luận `ambiguous` — giảm ambiguous nhầm.

### 3.3 Predeclared-id form instance

- Đọc `parse_meta.module_attributes["VB_PredeclaredId"] == "True"` (phase-01) → module form đó đăng ký bản default instance.
- Qualified receiver `Form1.<member>` khi `Form1` là form predeclared-id: resolve member trong form module (`functions_named`), kể cả caller ở module khác; không còn rơi `external` (hiện trạng: form/ctl/pag receiver không có member → external, `vb6_resolver.py:286-300`).
- Ưu tiên: member khớp trong form module → member không có → `external` (form intrinsics `.Show`... vẫn external — giữ hành vi, bổ sung `_BUILTIN`-style intrinsic member set cho form: Show, Hide, Refresh, Cls, Print... để nhất quán).

### 3.4 Dictionary calls

- Rows `call_type="dictionary_call"` từ phase-01: resolver giữ `default_member=true`, status:
  - receiver kiểu recordset/collection-known → `external` + giữ prop;
  - receiver late-bound → `late_bound`.
- KHÔNG bao giờ drop row; không cho vào CALLS tier (chỉ POSSIBLE_CALLS) vì default member không resolve tĩnh được trong scope này.

### 3.5 Golden mở rộng + tests

- `expected.json` += idioms mới: 1 interface dispatch (IShip pattern đã có từ plan trước — thêm call site qua interface var), 1 optional-args call, 1 predeclared-id cross-module call, 1 `rs!Field` (expected `possible`, không vào mẫu số resolved).
- `tests/test_vb6_resolver.py`: từng case 3.1-3.4 (unit-level, payload giả lập).
- `tests/test_vb6_golden.py`: recall metrics chạy trên tập expected-resolvable mở rộng; zero-drop gate giữ nguyên.
- Graph contract: dictionary rows xuất hiện ở POSSIBLE_CALLS với `default_member` prop — vocab status không đổi (AD-07).

## Định nghĩa xong

- [ ] M4: recall ≥80% extended; 4 idioms mới đúng table trên
- [ ] Exact-arity regress guarded: regex fallback path giữ hành vi cũ (test)
- [ ] Form intrinsics nhất quán (Show/Hide... external) — test
- [ ] 0 call site drop (CALLS ∪ POSSIBLE_CALLS) giữ nguyên
