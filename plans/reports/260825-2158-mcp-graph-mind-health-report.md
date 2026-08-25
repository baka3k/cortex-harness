---
type: MCP health report
date: 2026-08-25
scope: graph_mcp, mind_mcp, project stock
status: fixed-and-retested
---

# Báo cáo điều tra, sửa lỗi và kiểm tra Graph MCP / Mind MCP

## Kết luận

Các lỗi triển khai làm `graph_mcp` và `mind_mcp` đọc sai hoặc không đọc được dữ liệu `stock` đã được sửa. Hai MCP đã được restart từ đúng config của project và toàn bộ **44 hàm duy nhất** đã được gọi lại trực tiếp: **39 hàm graph + 5 hàm mind**.

| Phân loại sau sửa | Số hàm | Ý nghĩa |
| --- | ---: | --- |
| `PASS_DATA` | 35 | Hàm chạy thành công và trả dữ liệu/plan/diagnostic có ý nghĩa |
| `PASS_EMPTY` | 2 | Hàm chạy đúng; corpus `stock` hợp lệ nhưng không có loại fact đó |
| `EXPECTED_UNAVAILABLE` | 6 | MCP trả lỗi capability có cấu trúc vì graph thật không có relationship chuyên biệt được yêu cầu |
| `SAFE_NEGATIVE` | 1 | Tool ghi dữ liệu được thử bằng node sentinel không tồn tại để tránh sửa graph thật; lỗi được báo đúng |
| Lỗi MCP chưa xử lý | **0** | Không còn lỗi routing/provider/serialization/contract trong phạm vi đã tái hiện |

`EXPECTED_UNAVAILABLE` không được đổi thành PASS giả và không được tạo relationship giả. Đây là giới hạn của corpus hiện tại, không còn là việc MCP đọc nhầm graph rỗng.

## Xác nhận lỗi mẫu đã được sửa

Payload gốc:

```json
{
  "query": "đăng nhập người dùng xác thực tài khoản phiên đăng nhập phân quyền",
  "mode": "combined",
  "top_k": 15,
  "show_snippet": true,
  "expand_graph": true,
  "graph_depth": 1,
  "project_id": "stock"
}
```

Kết quả sau sửa:

- MCP outer result: `isError=false`.
- Payload: `ok=true`, `query_engine=graph_generic`.
- Vector: **15 kết quả**; các hit chính gồm `AuthService`, `authenticate_session`, `create_session`, `root-login`, `SessionData`.
- Graph expansion: **12 seed, 44 node, 100 edge**.
- Relationship thực tế được dùng: `CALLS`, `CONTAINS`, `REFERENCES_TABLE`.
- Không còn lỗi “Parser generic requested relationships unavailable” khi relationship là mặc định ngầm.
- Nếu caller yêu cầu tường minh một relationship không tồn tại, MCP vẫn fail-closed với diagnostic đúng.

## Nguyên nhân gốc và thay đổi đã thực hiện

### 1. MCP bị route từ remote storage sang embedded graph rỗng

Config top-level `storage_backend=remote` và block `remote` bị mất trong quá trình dựng runtime env; lifecycle còn resolve storage lần hai từ một object không còn đủ config. Biến môi trường cũ và local path có thể tiếp tục lấn át remote URI.

Đã sửa:

- Giữ nguyên cấu hình remote khi resolve runtime.
- Chỉ resolve storage một lần từ config đầy đủ.
- Truyền `CORTEX_HARNESS_CONFIG_PATH` cho MCP, analyzer và sync process.
- Khi có remote URI, loại local Falkor path và các endpoint kế thừa không đúng.
- Registry code/doc ưu tiên config path tường minh thay vì tự dò từ working directory.

Các file chính: `scripts/mcp_runtime_config.py`, `scripts/mcp-lifecycle.py`, `cortex_harness/dev.py`, `code-tiny/tools/common/project_registry.py`, `doc-tiny/project_contract.py`.

### 2. Graph driver dùng sai instance/cache

Cache key của shared Falkor driver không chứa URI nên hai endpoint có thể dùng chung driver. Các backend cũng truyền `additional_paths` ngay cả khi dùng remote.

Đã sửa:

- Cache key chứa URI và SSL mode.
- Tất cả backend graph ưu tiên `FALKORDB_URI/FALKORDB_URL`.
- Chỉ truyền local path/additional paths trong local mode.
- Tách đúng storage role `code` và `document`.

### 3. Graph expansion không nối được vector seed với graph node

Qdrant dùng file ID dạng `file::<path>`, trong khi graph dùng `<path>`. Ngoài ra Falkor edge object chưa được serialize đúng domain ID/type/properties.

Đã sửa:

- Chuẩn hóa file seed ID trước khi expand.
- Giữ graph internal ID nội bộ để ánh xạ edge, nhưng không làm rò `_graph_id` ra output.
- Serialize đúng `_type`, `_start_id`, `_end_id` và edge properties.

### 4. Semantic/explore fail toàn bộ khi graph thiếu capability mặc định

Danh sách relationship mặc định của parser được coi như yêu cầu tường minh, khiến vector search thành công vẫn bị biến thành lỗi.

Đã sửa:

- Với relationship mặc định ngầm: trả vector result và degrade có quan sát nếu graph thật không hỗ trợ.
- Với `graph_rel_types` tường minh: tiếp tục trả `unsupported_capability` để không che giấu lỗi yêu cầu.
- `explore_graph` hybrid có cùng quy tắc degradation.

### 5. Cypher đường đi không tương thích FalkorDB

Một số tool dùng cú pháp Neo4j `MATCH p=shortestPath(...)`; FalkorDB không chấp nhận dạng này trong `MATCH`.

Đã sửa toàn bộ occurrence trong backend C/C++, Java, Android, FastMCP và graph expander thành variable-length path, rồi `ORDER BY length(p)` và giới hạn kết quả. `find_path_between_module` và `trace_flow_between_module` hiện đều trả 11 node/10 edge trên tuyến API → auth.

### 6. Mind MCP route sai shard và graph adapter sai API

Mind từng fallback sang code collection không đúng khi project/doc collection không tồn tại; document graph trả raw driver trong khi caller cần graph-store API. Boolean string như `"false"` cũng bị ép thành true theo quy tắc Python thông thường.

Đã sửa:

- Resolve Qdrant theo `project_id`; project chưa đăng ký hoặc collection doc thiếu sẽ fail rõ ràng, không đọc nhầm shard.
- Dùng document storage role và wrap Falkor driver bằng `FalkorDBGraphStore`.
- Ép boolean string đúng.
- Thêm doc source của `stock` và sync corpus: `stock_doc` hiện có 7 source ID khả dụng.

### 7. Metadata và error signaling không đúng

Catalog từng lặp `trace_flow_between_module`, có input `project_id` trùng, và đồng bộ signature làm mất cờ required logic. Payload `ok=false` đôi khi vẫn có outer `isError=false`.

Đã sửa:

- Catalog deduplicate theo tên; hiện `total_count=39`, array 39, unique 39.
- Loại input trùng; giữ `project_id` và `parser_type` là required cho project-context tools.
- Structured `ok=false` được chuẩn hóa thành MCP `isError=true` cho cả proxy và direct tools.
- `strict`/`conservative` được giới hạn rõ cho C/C++/Pro*C thay vì quảng cáo sai trên parser generic.

## Kết quả từng hàm Graph MCP

| # | Hàm | Trạng thái | Bằng chứng sau sửa |
| ---: | --- | --- | --- |
| 1 | `analyze_workflow_impact` | PASS_DATA | 2 impacted nodes, có risk/workflow diagnostic |
| 2 | `annotate_node` | SAFE_NEGATIVE | Sentinel không tồn tại trả `isError=true`; không ghi lên node thật |
| 3 | `compute_scc` | PASS_DATA | 2 SCC từ fixture có chu trình |
| 4 | `explore_graph` | PASS_DATA | 10 matched nodes cho truy vấn login/auth |
| 5 | `find_callers_of_endpoint` | EXPECTED_UNAVAILABLE | Thiếu `CALLS_API`, `MATCHES`, `ApiCall` trong corpus |
| 6 | `find_path_between_module` | PASS_DATA | 11 nodes, 10 edges |
| 7 | `find_paths` | PASS_DATA | 2 nodes, 1 `CALLS` edge |
| 8 | `find_screen_workflows` | EXPECTED_UNAVAILABLE | Không có `NAVIGATE` |
| 9 | `find_workflows_containing` | EXPECTED_UNAVAILABLE | Không có `HAS_STEP` |
| 10 | `get_api_call_chain` | EXPECTED_UNAVAILABLE | Không có `CALLS_API`, `MATCHES` |
| 11 | `get_endpoints` | PASS_DATA | 1 endpoint login |
| 12 | `get_framework_context` | EXPECTED_UNAVAILABLE | Không có `USES_FRAMEWORK` |
| 13 | `get_ipc_message` | PASS_EMPTY | Response hợp lệ, không có IPC fact cho `stock` |
| 14 | `get_module_architecture_summary` | PASS_DATA | Có summary/provenance/capability |
| 15 | `get_node_details` | PASS_DATA | 2/2 node được lấy |
| 16 | `get_project_modules` | PASS_DATA | 3 modules |
| 17 | `get_project_special_files` | PASS_DATA | 7 special files |
| 18 | `get_public_apis` | EXPECTED_UNAVAILABLE | Không có `EXPOSES_API` |
| 19 | `get_symbol` | PASS_DATA | Lấy đúng symbol `_logout_current_user` |
| 20 | `inspect_parser_capabilities` | PASS_DATA | 154 labels, 11 relationships; `CALLS` khả dụng |
| 21 | `list_databases` | PASS_DATA | 7 graph databases |
| 22 | `list_mcp_functions` | PASS_DATA | 39 entries, 39 tên unique |
| 23 | `list_parsers` | PASS_DATA | 88 aliases, 27 capability profiles |
| 24 | `list_possible_calls` | PASS_EMPTY | `nodes=[]`, `edges=[]`; corpus không có `POSSIBLE_CALLS` |
| 25 | `list_qdrant_collections` | PASS_DATA | 21 collections |
| 26 | `list_up_entrypoint` | PASS_DATA | 15 entry functions trong auth module |
| 27 | `listup_class_matching_path` | PASS_DATA | Tìm được class `AuthService` |
| 28 | `listup_symbols_matching_file_path` | PASS_DATA | 24 symbols trong `auth/service.py` |
| 29 | `plan_dependency_order` | PASS_DATA | 2 modules, 1 dependency, 2 waves |
| 30 | `plan_file_dependency_order` | PASS_DATA | 2 modules, mỗi module 1 file, có cross-module edge |
| 31 | `plan_function_dependency_order` | PASS_DATA | API 16 functions, auth 19 functions, 16 cross edges |
| 32 | `query_subgraph` | PASS_DATA | 2 nodes, 1 edge; edge domain IDs hợp lệ |
| 33 | `reconstruct_flow` | PASS_DATA | 1 high-confidence flow, 0 uncertainty |
| 34 | `search_by_code` | PASS_DATA | 5 results |
| 35 | `search_functions` | PASS_DATA | 20 results/IDs cho login/auth |
| 36 | `semantic_search` | PASS_DATA | 15 vector hits + 44 graph nodes + 100 graph edges |
| 37 | `topological_sort` | PASS_DATA | DAG 3 nodes, 3 waves |
| 38 | `trace_flow` | PASS_DATA | 2 nodes, 1 edge |
| 39 | `trace_flow_between_module` | PASS_DATA | 11 nodes, 10 edges; lỗi `shortestPath` đã hết |

## Kết quả từng hàm Mind MCP

| # | Hàm | Trạng thái | Bằng chứng sau sửa |
| ---: | --- | --- | --- |
| 1 | `list_qdrant_collections` | PASS_DATA | Trả `stock_doc` |
| 2 | `list_source_ids` | PASS_DATA | 7 source IDs |
| 3 | `semantic_search` | PASS_DATA | 5 passages từ `stock_doc` |
| 4 | `query_graph_rag_langextract` | PASS_DATA | 5 passages, 12 entities; 0 relation là trạng thái corpus hợp lệ |
| 5 | `get_paragraph_text` | PASS_DATA | Trả paragraph 15 của `deerflow_same_ec2_deploy.md`, 167 ký tự |

## Kiểm thử và trạng thái runtime

- Focused regression liên quan các thay đổi: **192 passed**, 42 subtests passed, 2 warning kết nối Qdrant giả lập.
- Direct MCP smoke: **44/44 hàm đã được gọi**, không còn lỗi MCP implementation chưa phân loại.
- Runtime đã restart thành công:
  - graph/code MCP: `http://127.0.0.1:8788/mcp`, PID tại lần retest `39703`.
  - mind/doc MCP: `http://127.0.0.1:8789/mcp`, PID tại lần retest `39716`.
- `git diff --check`: pass.
- Full repository suite không phải gate sạch sẵn có: chạy mặc định dừng ở 2 lỗi collection; chạy với `PYTHONPATH=doc-tiny --import-mode=importlib` cho **1229 passed, 2 skipped, 66 failed, 21 errors**. Các lỗi rộng này thuộc parser fixtures, lifecycle/platform, global test-module pollution và storage legacy; focused MCP suite ở trên vẫn sạch. Không coi full-suite hiện hữu là đã được sửa trong nhiệm vụ này.

## Giới hạn còn lại của dữ liệu `stock`

Graph hiện có 154 labels và 11 relationship types, nhưng không có các fact chuyên biệt sau:

- UI workflow: `NAVIGATE`, `HAS_STEP`.
- Frontend/backend bridge: `CALLS_API`, `MATCHES`, `ApiCall`.
- Framework/public API: `USES_FRAMEWORK`, `EXPOSES_API`.
- Inferred calls/IPC: `POSSIBLE_CALLS` và IPC records hiện rỗng.

Nếu các chức năng này được yêu cầu cho `stock`, cần mở rộng analyzer/ingest để sinh fact có bằng chứng từ source rồi sync lại. MCP hiện báo `isError=true` cùng `capability_diagnostics` đúng contract; không còn âm thầm trả rỗng hoặc đọc sai provider.

## Kết luận bàn giao

Tiêu chí quan trọng nhất đã đạt: truy vấn login bằng `semantic_search(expand_graph=true)` trả dữ liệu vector và graph thực. Các vấn đề được nêu trong report baseline về routing sai, graph rỗng, Mind không có corpus, duplicate catalog, query profile, error flag, seed ID, edge serialization và FalkorDB path syntax đều đã được xử lý và kiểm tra lại.
