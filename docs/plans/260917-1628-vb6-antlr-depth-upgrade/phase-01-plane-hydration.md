# Phase 01 — Worker Plane Hydration + Payload Enrichment

> Gate ra: M1 (100% symbol gài enum/const/event/declare/attribute xuất hiện trong payload, shape hợp lệ); M2 (DICTIONARY_CALL row, 0 drop); AD-04 verify (accessor runtime); AD-05 (cache bump).

## Tasks

### 1.1 Rebuild jar + spike accessors (blocker đầu tiên)

- `mvn -q -f code-tiny/tools/vb/antlr_worker/pom.xml -DskipTests package` — research chưa chạy maven (digest §risk).
- Spike Java nhỏ (có thể là unit test trong worker module hoặc main tạm): với fixture .cls gài `Enum`, `Const`, `Event`, `Declare Function ... Lib "user32" Alias "..."`, `Attribute`:
  - verify ctx-walk + `ASGElementRegistry.getASGElement(ctx)` lấy được: event declaration (ctx `eventStmt`), declare (ctx `declareStmt`: `STRINGLITERAL()`/`ALIAS()`/`asTypeClause()`), attribute (`Attribute.getLiteral().getValue()`).
  - verify `Scope.getConstants()` (Scope.java:327) và `Module.getEnumerations()` trả đúng symbol gài.
  - Ghi kết quả vào `spike-plane-accessors.md` trong plan dir. **Nếu accessor inferred sai** → fallback AD-04: regex-lite trên `module.getLines()` trong worker (vẫn VB6-contained), ghi đè decision trong plan.md.

### 1.2 Worker serialize planes mới (Vb6Worker.java)

- **Shape chuẩn là dataclass hydration tại `vb_common.py:110-174` (source of truth — red-team F1).** Bảng dưới chỉ indicative; khi implement, mirror ĐÚNG field names/dataclasses (kể cả `qualified_name`, `class_name`, `namespace_name`, `start_line`/`end_line`, `line_number`). Chi tiết enum/constants/events ở §Findings 1-2 của research-digest.
- `enums[]`: từ `Module.getEnumerations()` + members — **symbol_id đúng convention regex** (bare `<Name>@<rel>` — red-team F2; KHÔNG dùng `<Mod>.<Enum>@<rel>`).
- `constants[]`: từ `Scope.getConstants()` (Scope.java:327) — mirror shape regex `constants`.
- `events[]`: `{name, args, visibility, module_name, file_path, line_number, ...}` — mirror shape regex `events` (dataclass là chuẩn).
- `declares[]`: shape VB6-ONLY mới (regex không emit — `vb_common.py:420`): `{symbol_id, name, proc_kind (function|sub), lib, alias?, return_type?, is_private, module_name, file_path, line_number, code}`. Python hydration thêm dataclass riêng; parse_meta ghi `declares_regex_support: false`.
- `parse_meta.module_attributes`: map attribute (lowercased key, giữ value gốc) → literal value (ít nhất `vb_name`, `vb_predeclaredid`, `vb_exposed`, `vb_globalnamespace`) — case-insensitivity bắt buộc (red-team F11-followup).
- Bỏ đoạn luôn-rỗng cho 4 plane đã điền (`Vb6Worker.java:438-444`); `namespaces`/`relations` giữ rỗng (không thuộc scope).

### 1.3 Enrich functions[] và calls[]

- `functions[]` += `min_arity` (số arg KHÔNG optional VÀ KHÔNG phải ParamArray arg — red-team F6), `has_optional_args`, `has_paramarray` — từ `Procedure.getOptionalArgs()/hasOptionalArgs()` (Procedure.java:38-42). Giữ `arity` nguyên (= argsList.size(), bao gồm optional — nên range check `args <= arity` đúng).
- **Mở rộng dataclass `FunctionDef` (`vb_common.py:27-46`) thêm các field mới VỚI DEFAULT** (red-team F4): `min_arity: int = 0`, `has_optional_args: bool = False`, `has_paramarray: bool = False` — nếu không, hydration drop field im lặng và phase-03 không có gì để đọc. Regex path hưởng default.
- `calls[]`: gỡ whitelist khỏi `DICTIONARY_CALL` (`Vb6Worker.java:89-95`): emit row `call_type="dictionary_call"`, `default_member=true`; `callee_name` = leaf member; `resolution_status` theo đích (`asg_resolved` nếu DictionaryCallImpl bind được, else `undefined`). Dictionary rows đi qua CÙNG pipeline dedup/`collapseReceiverOnlyRows` như rows khác (red-team F17). Python resolver re-derive ở phase-03.

### 1.4 Python hydration + cache bump + test pins

- `vb_common.py`: dataclasses mới cho 4 plane (mirror regex shape) + `Vb6DeclareRow`; mở `dataclass_from_payload` cho plane mới.
- `vb_analyzer_base.py`: cho planes mới điền qua node-row builders hiện có (Enum/Constant/Event đã có lane — research §2); Qdrant mapping (1249-1497) thêm plane mới vào embed set.
- **Bump `PARSE_CACHE_VERSION`** (`vb_common.py:24`) — update MỌI pin version: `test_vb6_engine_dispatch.py:38` VÀ bản trùng `test_vb6_antlr_worker_contract.py:27` (red-team F3). Phase-02 và phase-04 sẽ bump tiếp khi đổi shape.
- Update pins chính danh: contract test `start_line==31` frm (contract:101), callee/arity pins nếu arity mở rộng làm lệch (contract:113, golden:97-102).

### 1.5 Fixture + tests

- Fixture `tests/fixtures/vb6-application/` gài: 1 enum (+2 hằng số member), 1 module const, 1 class `Event`, 1 `Declare Function`, `VB_PredeclaredId` trên 1 form.
- `tests/test_vb6_antlr_worker_contract.py`: assert planes mới populated + DICTIONARY_CALL row (fixture thêm `rs!FieldName`).
- Golden/`expected.json`: chưa thêm idiom mới (để phase-03) — chỉ assert payload shape.

## Định nghĩa xong

- [ ] Jar rebuilt; spike-plane-accessors.md ghi kết quả accessor (verify/fallback)
- [ ] M1: 100% symbol gài xuất hiện trong payload, hydration không raise
- [ ] M2: DICTIONARY_CALL row + default_member=true; 0 drop
- [ ] PARSE_CACHE_VERSION bumped; engine-dispatch test xanh
- [ ] Regex engine không đổi hành vi (test registry + golden regex path xanh)
