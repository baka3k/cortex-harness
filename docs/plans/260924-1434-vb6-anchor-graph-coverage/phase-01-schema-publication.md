# Phase 01 — Schema, manifest, writer plane & publication

> Gate ra: M1 (exported đúng) + Control/rel mới đi qua được write path thật (typed rel path + node plane) — **trước khi** worker phát payload mới (P2).

## Tasks

### 1.1 Schema: `_NODE_SPECS` + `_REL_SPECS` (`code-tiny/tools/graph/schema/ladybug_schema.py`)

- `_NODE_SPECS["Control"] = ("id", ("qualified_name", "kind", "scope_name", "class_name", "line_number", "package_name"))` (AD-01).
- `_REL_SPECS["HAS_CONTROL"] = ((("Type", "Control"),), ())` — **Type, không phải Class**: form VB6 đi types lane (`vb_analyzer_base.py:1118-1134`, pin `test_vb6_graph_contract.py:194-199`).
- `_REL_SPECS["WIRED_TO"] = ((("Function", "Control"), ("Function", "Type")), ())` — rel **mới**; cấm đụng `HANDLES` (đã là Endpoint→Function, `ladybug_schema.py:394-396`, MCP đọc tại `framework_registry.py:84`). Pair (Function,Type) phục vụ pseudo-control lifecycle (`Form_Load` → form Type node).
- `_REL_SPECS["INSTANTIATES"] = ((("Function", "Type"),), ())`.
- `_REL_SPECS["USES"]`: mở thêm pair `("Function", "Control")` (:384-385); KHÔNG thêm (Function,Class).
- `Variable` node spec: thêm cột `is_static` (:211-213).

### 1.2 Manifest + writer plane (bắt buộc để node/rel sống sót — red-team C2)

- `schema/manifest.py`: đăng ký `Control` vào `_id_indexes` (:136-293) — nếu không, `group_typed_relations` raise ValueError cho mọi rel có endpoint Control (`query_contract.py:27-31`). Ghi chú fingerprint change vào runbook (journal in-flight sẽ `INCOMPATIBLE_SCHEMA` — chấp nhận, P4 full resync).
- `language_writer.py`: thêm **`write_controls_full`** + param `controls=` cho `write_all` — `write_all` KHÔNG có generic node plane, mọi label cần writer riêng (:2757-2836). Mirror pattern `write_constants_full`/`write_variables_full` (MERGE on id, SET columns từ node spec).
- Smoke test: 1 row `HAS_CONTROL` (Type→Control) + 1 `WIRED_TO` (Function→Control) + 1 `USES` (Function→Control) + 1 Control node ghi qua fake-driver **qua write path thật** — không raise, cạnh tồn tại.

### 1.3 Publication: `exported` (AD-04)

- `asdict_function` (`vb_common.py:1223`): `exported` derive từ `is_private` (False ⇒ True; Friend tính exported — pin test). Không cần bump cache — `is_private` đã hydrate cả 2 engine (`Vb6Worker.java:545,568`; `vb_common.py:790`), writer đã SET (`language_writer.py:1372-1374`).
- Điền `is_public_api` cùng nguồn.

### 1.4 `is_static` end-to-end (AD-03 / red-team H2)

- `VariableDef` thêm field `is_static: bool = False` (`vb_common.py:27-53`); `asdict_variable` emit key (:1429-1453).
- `write_variables_full` SET list thêm `is_static` (`language_writer.py:1631-1653`).
- Hydration default False — payload cũ vẫn đọc được (không bump cache ở phase này; bump chung ở P2).

### 1.5 Write-path khung cho P2/P3

- `vb_analyzer_base.py`: builders `control_rows()`, `wiring_rows()`, `instantiation_rows()` nhận payload plane (rỗng ở phase này) + unit test với payload giả. Builders **guarded**: chỉ emit row khi target node có trong batch (red-team M4 — `USES`/`WIRED_TO` không nằm `_OPTIONAL_EXTERNAL_RELATION_TYPES`, row lệch abort cả batch, `language_writer.py:44-46`).

### 1.6 Tests

- Schema/manifest: register `Control`, 3 rel mới (HAS_CONTROL, WIRED_TO, INSTANTIATES), USES pair mới; DDL compile test (`tests/test_ladybug_driver.py` blast radius đã verify: additive không phá).
- Publication: golden pin `exported=true` Public proc, `false` Private (fixture hiện có).
- Contract: `test_vb6_graph_contract.py` case Control node + 3 cạnh với payload giả, qua fake-driver.
- **Pin contract test `test_vb6_antlr_worker_contract.py:29`: đổi local constant thành assert-equality với imported `PARSE_CACHE_VERSION`** (đỡ đỏ oan khi P2 bump).

## Định nghĩa xong

- [ ] M1: golden exported xanh
- [ ] Control node + 3 rel đi qua được write path thật (fake-driver) — không raise preflight
- [ ] `is_static` nhìn thấy ở graph qua write path thật
- [ ] `git diff` chỉ chạm schema (ladybug_schema, manifest) + language_writer (controls plane, SET is_static) + vb_common + vb_analyzer_base + tests
