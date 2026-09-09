# PROJECT_ID QUERY RULES — ĐỌC KỸ TRƯỚC KHI SỬA CODE QUERY

> **Tài liệu bắt buộc cho mọi agent** tham gia sửa code liên quan đến
> `project_id` (graph DB, vector DB, MCP tools, service layer).
> Sai rule ở đây = query trả thiếu kết quả hoặc lộ dữ liệu chéo project.
> Rule song hành cho `parser_type`: 
> [`docs/PARSER_TYPE_QUERY_RULES.md`](./PARSER_TYPE_QUERY_RULES.md).

---

## 1. Bốn rule bất biến (READ PATH)

Mọi truy vấn **đọc** (search / list / get / expand / workflow / explore) trên
**graph DB (FalkorDB/Neo4j)** và **vector DB (Qdrant local file hoặc remote)**
phải tuân thủ đầy đủ 4 rule sau:

| # | Rule | Ý nghĩa |
|---|------|---------|
| R1 | **Không truyền `project_id` → search TẤT CẢ** | Filter scope phải bị loại bỏ hoàn toàn (`None`), graph fan-out mọi graph đã đăng ký + default graph. |
| R2 | **Có `project_id` → scope theo project** | Graph: chỉ đọc graph/candidates của project đó. Vector: phải có payload filter `project_id_normalized`. |
| R3 | **Không phân biệt hoa/thường** | So sánh qua `casefold()`: `"Bank"` ≡ `"bank"` ≡ `"BANK"`. |
| R4 | **Match dạng LIKE (prefix)** | Query `"bank"` phải khớp **mọi project id có casefold-bắt-đầu-bằng** `"bank"`: `bank_android`, `bank_Cplus`, `bank_ios`… vì lúc scan, project id được tạo tự động theo quy tắc `{stem}_{platform}`. Exact match chỉ là trường hợp đặc biệt (prefix = toàn bộ chuỗi). |

Ví dụ R4: registry có `bank_android`, `bank_Cplus`, `other`.
Query `project_id="Bank"` ⇒ khớp `bank_android` + `bank_cplus`, **không** khớp `other`.

## 2. Rule bổ sung

- **R5 — Local = Remote.** Cùng một filter/must được đẩy qua
  `LocalQdrantStore` (file nhúng) lẫn `RemoteQdrantStore` (Qdrant server).
  Chỉ dùng các điều kiện Qdrant khả dụng ở cả hai chế độ: `match.value`,
  `match.any`. **Tuyệt đối không** dùng `match.text` (full-text) — local mode
  không hỗ trợ.
- **R6 — WRITE PATH giữ EXACT.** Các đường ghi/đồng bộ/xóa theo định danh
  project phải giữ so sánh `=` chính xác, **không bao giờ** dùng prefix, nếu
  không project A sẽ đè dữ liệu của project B:
  - `tools/graph/journal/*` (executor, reconcile, sqlite_store)
  - `tools/graph/writer/*` (language_writer, project_topology_writer, query_contract)
  - `tools/sync/incremental_sync.py` (MATCH count + SET `project_id_normalized`)
  - `tools/common/message_scan.py::_qdrant_delete_by_project` (delete-by-filter)
  - `tools/shell/shell_analyzer.py::_materialized_file_ids` (kiểm tra định danh đã materialize)
  - `code-tiny/scripts/backfill_project_scope_keys.py`, `doc-tiny/graphrag_ingest_langextract.py`, `doc-tiny/0_reset_all.py`
  - Reject-at-ingest: ingest của project chưa đăng ký vẫn phải từ chối
    (`ProjectNotRegisteredError` / `SystemExit`) — chỉ READ path mới fallback.

## 3. Thứ tự resolve cho một `project_id` scoped (READ)

```
project_id truyền vào
  1. Exact case-insensitive match trong registry  → 1 target duy nhất
  2. Không exact → prefix case-insensitive match  → N target (fan-out, merge)
  3. Registry không có match nào                  → dùng raw id theo naming
     convention (code_graph == project_id, doc = {project_id}_doc) để vẫn
     với được shard ngoài registry
  4. project_id rỗng/None                         → unscoped (R1)
```

Hàm chuẩn hóa trung tâm:

- `code-tiny/tools/common/project_scope.py`
  - `project_id_lookup_key(v)` → casefold key (R3)
  - `project_id_scope_keys(v, known_ids)` → tập key sau khi mở rộng prefix (R4);
    `None` = unscoped (R1)
  - `qdrant_project_filter(v)` → `{"must": [{"key": "project_id_normalized",
    "match": {"any": keys}}]}` hoặc `None` khi unscoped
  - `matches_project_scope(candidate, v)` → prefix post-filter cho payload
  - `prepare_project_scope_parameters(query, params)` → thêm `*_normalized`
    params cho Cypher (giữ nguyên raw `project_id` cho identity)
- `code-tiny/tools/common/project_registry.py`
  - `resolve_project_targets(v)` → exact match, raise nếu không thấy (dùng cho
    WRITE + resolve storage)
  - `resolve_project_scope_candidates(v)` → exact → prefix → `[]` (dùng cho READ)
- Bản mirror phía doc: `doc-tiny/project_contract.py`
  (`project_id_scope_keys`, `qdrant_project_filter`,
  `resolve_doc_candidates`)

## 4. Quy ước Cypher (graph READ predicate)

Mọi predicate đọc trên graph phải viết theo mẫu:

```cypher
WHERE ($project_id IS NULL OR n.project_id_normalized STARTS WITH $project_id_normalized)
```

- `STARTS WITH` chính là R4; khi query là id đầy đủ nó tự quy về exact.
- Khi `project_id` không truyền, guard `$project_id IS NULL` phải skip predicate
  (R1) — param `project_id_normalized` được inject kèm giá trị `None` nếu key
  `project_id` có trong params.
- `STARTS WITH` được cả FalkorDB và Neo4j hỗ trợ.
- **Cấm** dùng lại `n.project_id_normalized = $project_id_normalized` trong query
  đọc — đó là bug mất kết quả khi query theo stem (`bank` vs `bank_android`).

Các vị trí đã chuyển sang `STARTS WITH` (kể cả bản sao backend):

- `tools/graph/core/cypher_driver.py`, `tools/graph/driver/{falkordb,neo4j}_driver.py`
- `tools/common/graph_expander.py`, `tools/common/intelligent_retrieval.py`
- `tools/ts/workflow_finder.py` (param `$pid_normalized`)
- `mcp/{cplus,android,java}/…_mcp.py`, `mcp/fastmcp_server.py` (toàn bộ query template đọc)
- `mcp/semantic_graph_expansion.py`, `mcp/services/project_context_service.py`
- `doc-tiny/mcp_graph_rag.py` (entity/relation/paragraph fetch)

Ngoại lệ giữ `=`: danh sách R6 ở mục 2, và
`ServletJspAnalysisState {project_id: $project_id}` (định danh state theo raw id).

## 5. Quy ước Qdrant (vector READ filter)

- Filter chuẩn: `qdrant_project_filter(project_id)` → `match: {"any": keys}`.
  `MatchAny` hoạt động ở cả local embedded store lẫn remote server (R5).
- Tập `keys` = key của query + mọi registered project có casefold-prefix bằng
  query. Nguồn mặc định: `ProjectRegistry` (`list_registered_projects`); có thể
  truyền `known_ids` để pin nguồn cho test.
- Vector search luôn quét toàn bộ collection rồi lọc bằng payload filter
  (`qdrant_query_support.search_collection`); collection KHÔNG được dùng để
  chặn scope (một query stem phải xuyên qua nhiều collection `bank_android`,
  `bank_cplus`…).
- Resolver fan-out collection phía doc: `mcp_graph_rag._resolve_doc_collections`
  (exact → prefix → `{project_id}_doc`).

## 6. Graph fan-out theo candidates

Các hàm `_resolve_db_candidates(project_id)` trong
`mcp/cplus/cplus_mcp.py`, `mcp/android/android_mcp.py`, `mcp/java/java_mcp.py`,
`mcp/fastmcp_server.py` đều theo mẫu:

```
scoped  → resolve_project_scope_candidates(project_id) → graph từng target
          (rỗng → [raw project_id] để với shard ngoài registry)
unscoped→ mọi registered graph + DEFAULT_GRAPH_DB
```

Tương tự: `mcp/services/explore_service.py::_resolve_search_targets`,
`mcp/services/workflow_service.py` (fan-out per project + merge),
`doc-tiny/mcp_graph_rag.py::_graph_store_candidates`.

## 7. Checklist cho agent khi sửa code liên quan `project_id`

1. Route đọc có đi qua `project_id_scope_keys` / `resolve_project_scope_candidates`
   / `qdrant_project_filter` / `prepare_project_scope_parameters` chưa?
   Nếu tự viết filter mới ⇒ gần như chắc chắn sai — dùng lại helper.
2. Predicate Cypher đọc là `STARTS WITH`, có guard `$project_id IS NULL`?
3. Có đang thêm prefix vào đường GHI/XÓA/SYNC không? Nếu có → dừng (R6).
4. Hành vi local và remote có giống nhau không (không dùng `match.text`,
   không dùng tính năng chỉ có trên server)?
5. Unregistered id: read path phải fallback raw id (naming convention),
   write path phải raise.
6. Chạy test: `tools/common/test_project_scope.py`,
   `tools/common/test_project_registry.py` (`ScopeCandidatesTests`),
   `tests/test_qdrant_project_scope.py`, `tests/test_case_insensitive_project_scope.py`,
   `tests/test_unified_contract_doc_paths.py`, `doc-tiny/tests/test_project_contract.py`.

## 8. Vì sao có rule R4 (bối cảnh)

Khi scan, mỗi target/platform sinh một project id con theo mẫu
`{stem}_{platform}` (VD: stem `bank` → `bank_android`, `bank_Cplus`).
Người dùng/API thường chỉ biết stem. Nếu query so sánh exact, mọi kết quả của
các project con bị mất — đây là lỗi thực tế đã xảy ra trước khi rule này được
áp dụng (predicate `=` cũ trong toàn bộ backend). Xem thêm
`docs/PROJECT_REGISTRY.md` (naming contract) và
`docs/UNIFIED_INGEST_QUERY_CONTRACT.md`.
