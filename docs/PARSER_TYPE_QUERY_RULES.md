# PARSER_TYPE QUERY RULES — ĐỌC KỸ TRƯỚC KHI SỬA CODE DISPATCH/QUERY

> **Tài liệu bắt buộc cho mọi agent** tham gia sửa code liên quan đến
> `parser_type` (unified dispatch, fan-out, capability registry, backend
> label profiles). Đây là rule song hành với `project_id` — xem kèm
> [`docs/PROJECT_ID_QUERY_RULES.md`](./PROJECT_ID_QUERY_RULES.md).

---

## 1. Hai rule bất biến (READ PATH)

| # | Rule | Ý nghĩa |
|---|------|---------|
| P1 | **Không truyền `parser_type` → search TẤT CẢ** | Unified dispatch fan-out **mỗi query engine một lần** và merge kết quả. Không được mặc định về một parser/engine duy nhất, không được cảnh báo lỗi (đây là mode có chủ đích). |
| P2 | **Truyền `parser_type` → scope theo parser** | Resolve qua capability registry → route đúng backend, dùng đúng profile (labels / searchable properties / default relationships) của parser đó. |
| P3 | **Không phân biệt hoa/thường** | `"CPlus"` ≡ `"cplus"` ≡ `"CPP"` (alias table). Mọi entry point chuẩn hóa bằng `_normalize_parser_type` / `capability_for_parser` (`.strip().lower()`). |

Ví dụ P1: `search_functions(query="login")` (không `parser_type`) → dispatch
song song sang `cplus` + `android`, mỗi engine search với **union label của
mọi parser profile mà engine đó phục vụ**, merge + dedup theo node id, kết quả
có `parsers_searched`.

Ví dụ P2: `search_functions(query="login", parser_type="Kotlin")` → resolve
thành profile JVM/kotlin → route backend tương ứng, chỉ search label của
profile đó.

## 2. Kiến trúc: 2 query engine, nhiều parser profile

- **Query engine (backend vật lý)** — `BACKENDS` trong
  `code-tiny/mcp/unified_mcp.py`: hiện là `"cplus"` và `"android"`.
  `_ALLOWED_BACKENDS` trong `code-tiny/mcp/framework_registry.py` phải khớp.
- **Parser profile (capability)** — `CAPABILITIES` trong
  `code-tiny/mcp/framework_registry.py`: mỗi profile có
  `name/aliases/labels/relationships/searchable_properties/backend`.
  Tra alias bằng tool `list_parsers` (vd profile `cplus` nhận alias
  `cpp, c++, c, clang, proc, pro*c, pro-c`).
- Một engine phục vụ nhiều profile ⇒ khi fan-out (P1) phải dùng
  `backend_label_union(engine)` / `backend_text_property_union(engine)`
  (`framework_registry.py`), **không** dùng label của một profile cố định.

## 3. Cơ chế fan-out khi omit `parser_type` (P1)

File trung tâm: `code-tiny/mcp/unified_mcp.py`.

1. `_dispatch_tool`: `parser_type` được `_normalize_parser_type`
   (strip + lower). Omitted ⇒ `None`.
2. Tool nằm trong `_FANOUT_SEARCH_TOOLS` (13 tool search:
   `search_functions, search_by_code, get_symbol, get_node_details,
   query_subgraph, find_paths, find_path_between_module,
   listup_symbols_matching_file_path, listup_class_matching_path,
   list_up_entrypoint, trace_flow, trace_flow_between_module,
   list_possible_calls`) ⇒ `_resolve_fanout_parsers(project_id)`:
   - project đã đăng ký và có `parser_type` trong config ⇒ **pin 1 parser**
     của project (không fan-out) — interplay với project_id rules;
   - ngược lại ⇒ danh sách representative **mỗi engine một tên**
     (`sorted(BACKENDS.keys())`).
3. `_fanout_dispatch`: payload gửi xuống từng engine **bị xóa
   `parser_type`** và gắn `_fanout=True`; chạy `asyncio.gather` đồng thời;
   merge + dedup qua `_merge_fanout_results` (node id); lỗi từng engine cô
   lập trong `parser_errors`, chỉ fail toàn cục khi **mọi** engine lỗi
   (`fanout_failed`). Kết quả merge có `parsers_searched`.
4. Trong backend: `payload["_fanout"]` ⇒ label predicate dùng union:
   - cplus: `_search_label_predicate(variable, profile_labels, fanout=...)`
     → `backend_label_union("cplus") | _LEGACY_SEARCH_LABELS`;
   - android: `_android_symbol_labels(fanout=...)`
     → `base | backend_label_union("android")`.

**Tool không nằm trong `_FANOUT_SEARCH_TOOLS`** (`semantic_search`,
`explore_graph`, `find_screen_workflows`, …) không fan-out engine — nhưng vẫn
tuân P1 vì chúng vốn parser-agnostic: vector search quét mọi collection
(không lọc parser), keyword/graph search bỏ hẳn mệnh đề label khi không có
profile (`_graph_keyword_search` — `labels=None` ⇒ khớp mọi node).
Tool không phải search (vd `inspect_parser_capabilities`) được resolve theo
thứ tự: parser truyền vào → parser của project đã đăng ký → `DEFAULT_BACKEND`.

## 4. Khi truyền `parser_type` (P2 + P3)

- `capability_for_parser(parser_type)` — `framework_registry.py` — tra
  `_ALIAS_INDEX` (đã lower). Không phân biệt hoa/thường, trim khoảng trắng.
- **Parser lạ (typo) khi truyền tường minh ⇒ lỗi fatal**
  (`_unsupported_parser_result`, envelope `unsupported_capability`) —
  **không** âm thầm fallback sang parser khác. Ngược lại khi OMIT, không bao
  giờ lỗi (P1).
- Unified route `BACKENDS[capability.backend]`; profile quyết định:
  labels (`searchable_labels`), text properties (`text_search_properties`),
  default relationships (`default_relationships(capability, tool)`;
  khi omitted ⇒ `CORE_RELATIONSHIPS`).
- **Project-context tools** (`get_project_modules`, `get_public_apis`,
  `get_endpoints`, `get_module_architecture_summary`,
  `get_project_special_files`, `get_framework_context`) **bắt buộc**
  `parser_type`: explicit → nếu omit thì lấy `targets.parser_type` của
  project đã đăng ký → cùng không có thì `DEFAULT_BACKEND` (cplus).

## 5. Interplay với `project_id` (đọc kèm PROJECT_ID_QUERY_RULES.md)

| truyền | `parser_type` | Hành vi |
|---|---|---|
| omit | omit | Fan-out mọi engine, mỗi engine search mọi parser + mọi project scope theo project_id rules |
| omit | có | 1 backend theo profile, scope project_id như thường |
| có | omit | Project đã đăng ký + có parser trong config ⇒ **pin parser của project**; project chưa đăng ký/không có parser ⇒ engine fan-out |
| có | có | `parser_type` thắng trong việc chọn profile/backend; `project_id` thắng trong việc chọn scope dữ liệu |

## 6. Checklist cho agent khi sửa code liên quan `parser_type`

1. Chuẩn hóa bằng `_normalize_parser_type` / `capability_for_parser` —
   **không** tự `.lower()` rải rác, **không** so sánh chuỗi parser trực tiếp.
2. Thêm tool search mới? Phải thêm tên tool vào `_FANOUT_SEARCH_TOOLS` để
   được fan-out khi omit (P1).
3. Sửa label predicate trong backend? Phải giữ nhánh `fanout=` union
   (`backend_label_union`), không hardcode label của 1 profile.
4. Thêm parser profile mới? Chỉ gán `backend` thuộc
   `_ALLOWED_BACKENDS = {"android", "cplus"}`; thêm backend vật lý mới thì
   phải cập nhật cả `BACKENDS`, `_ALLOWED_BACKENDS`, và
   `default_relationships`/label union tương ứng.
5. Parser lạ khi được truyền tường minh phải báo lỗi, không fallback im lặng;
   khi omit thì tuyệt đối không raise.
6. Không đổi text schema trong `code-tiny/mcp/tool_metadata.py` trái với hành
   vi thật (các description đang ghi rõ "Omit to …").
7. Chạy test liên quan: `tests/test_framework_mcp_flows.py`,
   `tests/test_qdrant_project_scope.py` (subtest fan-out),
   `code-tiny/tests/mcp/*`, `tests/test_unified_mcp_input_coercion.py`.

## 7. Vì sao rule này tồn tại (bối cảnh)

Trước đây server có trạng thái "active parser/active project" nên omit
`parser_type` dùng giá trị session — gây kết quả phụ thuộc thứ tự gọi tool.
Theo unified ingest/query contract, state đã bị xóa: **mọi call tự trị**,
omit = ý nghĩa tường minh "search tất cả" (P1), truyền = scope (P2). Xem
`docs/UNIFIED_INGEST_QUERY_CONTRACT.md` và `docs/PROJECT_ID_QUERY_RULES.md`.
