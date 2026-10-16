# REPO QUERY RULES — ĐỌC KỸ TRƯỚC KHI SỬA CODE QUERY

> **Tài liệu bắt buộc cho mọi agent** tham gia sửa code liên quan đến tham
> số `repo` (graph DB, vector DB, MCP tools).
> Rule song hành với [`docs/PROJECT_ID_QUERY_RULES.md`](./PROJECT_ID_QUERY_RULES.md)
> (project shard selector) — `repo` là **property filter trong từng graph**,
> không phải shard selector.

---

## 1. Năm rule bất biến (READ PATH)

Mọi tool query **đọc** trên code MCP surface (unified + 4 backend
`fastmcp/cplus/android/java`) phải tuân thủ:

| # | Rule | Ý nghĩa |
|---|------|---------|
| RR1 | **Không truyền `repo` → search TẤT CẢ** | Predicate auto-no-op (`$repo = ''`), không đổi cấu trúc query. |
| RR2 | **Có `repo` → chỉ lọc node ANCHOR/kết quả** | Node láng giềng traversal **không** bị lọc — caller tự thấy cross-repo edges/callers (kết quả hợp lệ). Đặt predicate lên **cả 2 endpoint** của một quan hệ là BUG (giết cross-repo CALLS). |
| RR3 | **Match = exact HOẶC suffix-bắt-đầu-bằng `/`** | `n.repo = $repo OR n.repo ENDS WITH $repo_suffix`; `$repo_suffix = "/" + repo` do helper **tính sẵn** — cấm concat string trong Cypher (FalkorDB không hỗ trợ). Phủ cả 2 flavor lưu trữ: abspath (`/Users/x/s3`) lẫn `{project}/{basename}` (`projA/s3`); `s3` **không** khớp `xs3`. |
| RR4 | **Blank/whitespace = unscoped** | Mirror project_id R3: `""` / `"   "` ≡ không truyền. |
| RR5 | **Vector path: post-filter Python sau over-fetch** | Qdrant `match` không diễn đạt được suffix → over-fetch `limit × 4` rồi filter bằng Python; response báo `scope.pre_filter_hits` / `post_filter_hits`. |

## 2. Constraint LadybugDB (bắt buộc — đã live-verified 2026-10)

**CẤM dùng `$repo IS NULL` trong fragment.** LadybugDB bind parameter
`None` thành BOOL → `$repo IS NULL` "đốt" kiểu BOOL của `$repo`, và so sánh
chuỗi `n.repo = $repo` nổ `Conversion exception: ... is not a valid boolean`
 ngay cả khi nhánh OR đầu đã true. Pattern `project_id` chỉ sống sót vì
nhánh IS NULL của nó dùng **param khác** (`$project_id`) với nhánh so sánh
(`$project_id_normalized`).

Do đó fragment chuẩn là (unscoped = empty string, không bao giờ None):

```cypher
AND ($repo = '' OR n.repo = $repo OR n.repo ENDS WITH $repo_suffix)
```

`ENDS WITH` đã live-verify trên **cả FalkorDBLite 0.10.0 lẫn LadybugDB
0.20.4** (smoke 18/18 — xem `smoke-report.md` trong plan dir
`261016-1100-repo-scope-code-mcp-queries`).

## 3. Helper trung tâm duy nhất — `code-tiny/tools/common/project_scope.py`

| Helper | Vai trò |
|---|---|
| `prepare_repo_scope_parameters(params)` | Normalize `repo` (blank → `''`), inject sibling `repo_suffix`. Key vắng mặt → không inject gì. |
| `repo_scope_predicate(alias)` | Fragment Cypher chuẩn ở trên. **Cấm viết tay fragment khác.** |
| `matches_repo_scope(value, repo)` | Python twin của predicate (post-filter, test chung ngữ nghĩa). |

Response shaping dùng chung `code-tiny/mcp/repo_scope_support.py`:
`repo_scope_state` (scope block), `repo_scope_empty_warnings` (warning
"matched 0 nodes" + top-5 `DISTINCT n.repo` — chỉ chạy nhánh rỗng),
`raw_node_matches_repo` / `graph_anchors_in_repo` (post-filter anchor cho
tool đi qua driver method), `filter_vector_hits_by_repo` +
`vector_repo_scope_result` + `REPO_POSTFETCH_FACTOR = 4` (vector).

Chuỗi params: `prepare_repo_scope_parameters(prepare_project_scope_parameters(...))`
— đã cài sẵn trong `_run_cypher_first` của cả 4 backend.

**⚠ R7 — REPO PHẢI ĐỌC TỪ PAYLOAD, KHÔNG PHẢI KWARG (cplus/android).**
Unified dispatch gọi backend `fn(payload=merged)` — kwargs typed KHÔNG được
bind trên đường đó. Params Cypher trong tool kiểu payload-merge PHẢI dùng
`"repo": repo_value` (biến đã derive từ `payload.get("repo")`), **cấm**
`"repo": repo` (kwarg — luôn None → filter âm thầm no-op trong khi scope
block vẫn báo `filter_applied: true`). Regression:
`tests/test_repo_scope_cplus.py::CPlusPayloadDispatchRegressionTest`
(gọi tool đúng kiểu dispatch — payload-only). FastMCP standalone và planner
dispatch (`fn(**arguments)`) bind kwarg bình thường.

## 4. Bảng anchor semantics (đối chiếu trước khi thêm/sửa query)

| Nhóm query | Alias đặt predicate | Lý do |
|---|---|---|
| Search node (search_functions, search_by_code, listup_*) | node kết quả chính (`n`/`node`) | filter trực tiếp |
| Callers/entrypoint (list_up_entrypoint, find_callers_of_endpoint) | **f** (node bị hỏi) — caller free | cross-repo caller là kết quả hợp lệ |
| Path s/t (find_path_between_module, trace_flow_between_module, `_find_module_paths_directed`) | **chỉ s** (origin) | target + cross-repo CALLS free (plan RT#1) |
| Path theo id (find_paths, trace_flow có end_id) | **cả 2 endpoint** (a, b) | cả hai đều do caller chỉ đích danh bằng id |
| Subgraph (query_subgraph) | **f** (anchor id) — neighbors free | RR2 |
| Dependency planner (plan_file/function_dependency_order) | **caller** | callee ở repo khác = external dependency, vẫn hiện |
| list_possible_calls | **a** (caller) | callee free |
| get_ipc_message | **m** (Message node — có `repo` từ message_scan) | — |
| get_symbol / get_node_details / analyze_proc_data_impact | post-filter anchor qua `matches_repo_scope` | id-addressed, không có template |

**Loại khỏi surface (không nhận `repo`, đã verify):** `annotate_node`
(write — mirror R6); `list_*`, `inspect_*` (metadata); 6 project-context
tools (`ProjectModule`/`ApiEndpoint`… không có property `repo`);
`compute_scc`, `topological_sort`, `plan_dependency_order` (pure input);
`list_workflows`, `search_workflows`, `get_workflow_steps`,
`analyze_workflow_impact`, `find_workflows_containing` (**Workflow nodes
không có `repo`** — filter sẽ luôn trả rỗng); `reconstruct_flow` (thuần
JSON reshape); `find_screen_workflows` (query nằm ở
`tools/ts/workflow_finder.py` — defer khi writer tích hợp; **Function node
có repo**, chỉ là query layer nằm ngoài plan file list).

## 5. Response contract (additive — envelope v2.0 bất biến)

Thêm **chỉ bên trong `data`**:

```json
{
  "results": [...],
  "scope": {"repo": "s3", "filter_applied": true, "warnings": []},
  "warnings": []
}
```

- `scope.repo` = giá trị đã normalize (string hoặc null khi unscoped);
  `warnings` ở top-level là mirror của `scope.warnings`.
- Vector (`semantic_search`, `explore_graph`): `scope` thêm
  `pre_filter_hits`, `post_filter_hits`.
- Scoped query trả rỗng → warning `"repo '<x>' matched 0 nodes. Known repo
  values: ..."` (tối đa 5, từ 1 query phụ chỉ chạy nhánh rỗng).
- `get_ipc_message` trả bare list → chỉ có predicate, **không** scope block.
- Fan-out: `scope` được promote từ parser đầu tiên thành công; `warnings`
  gộp + dedupe.

## 6. Acceptance gate

`tests/test_unified_mcp_wrapper_signatures.py`:
`test_repo_scope_acceptance_gate_live_schema` assert **mọi** tool query đọc
trên unified registry expose `repo` trong JSON schema live (tool thiếu =
BUG, không skip); tool metadata/write/project-context KHÔNG được có.
Thêm tool query mới mà quên `repo` → gate fail → hoặc thêm param, hoặc
thêm vào `_REPO_EXCLUDED_UNIFIED_TOOLS` kèm lý do.

## 7. Không có index cho `repo`

Per-node property compare đủ ở scale hiện tại. Đo lại trước khi thêm index;
backfill `repo_normalized` (nếu cần equality thuần) follow theo pattern
`scripts/backfill_project_scope_keys.py`.
