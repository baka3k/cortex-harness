# Phase 12 — WAVE F2: MCP graph tools (graph_mcp surface) — BÁO CÁO PARITY

Ngày: 2026-09-14. Phạm vi: `rust/crates/cortex-mcp/src/graph/` (mới),
`scripts/rust_mcp/{graph_contract,record_graph,compare_graph}.py` (mới),
sửa tối thiểu: `dispatch.rs` (bỏ gate strict + nối dispatch graph),
`lib.rs` (`pub mod graph`), `main.rs` (SIGTERM cleanup),
`framework_registry.rs` (+`backend_text_property_union`), `server.rs`
(thay 1 test stub đã lỗi thời bằng test reconstruct_flow thật).
Không commit. Không đụng `code-tiny/` hay crate khác.

## Kiến trúc & deployment topology (mục 6 của task)

**Lựa chọn: một binary `cortex-mcp` duy nhất, dispatch theo parser (backend-keyed).**
Căn cứ: Python production chạy `unified_mcp.py` (mcp.sh) — server này NẠP
`android_mcp.py` + `cplus_mcp.py` in-process qua `BACKENDS = {android, cplus}`
và dispatch theo parser; `java_mcp.py` không được unified nạp (chỉ chạy
standalone thủ công, không có tool nào của java trong unified catalog 39).
Rust mirror đúng: `graph::Backend::{Android, Cplus}` + `run_backend_tool`
(= backend `tool_<name>`), fan-out parser-less qua 2 engine như
`_fanout_dispatch`. Nhánh standalone per-language binary không mirror vì
không phải deployment production (chỉ là entrypoint dev thay thế).

## Trạng thái tool coverage (39 unified tools)

| Nhóm | Tool | Trạng thái |
|---|---|---|
| Đã port thật (graph) | search_functions, search_by_code, get_symbol, get_node_details, list_possible_calls, listup_symbols_matching_file_path, listup_class_matching_path, list_up_entrypoint, trace_flow, trace_flow_between_module, query_subgraph, find_paths, find_path_between_module (cả 2 backend android/cplus + fanout merge dedup) | **PORTED** |
| Đã port thật (direct) | explore_graph (keyword+BM25+expansion+fusion+packager), semantic_search (resolution + graph expansion), reconstruct_flow, annotate_node, get_ipc_message, list_databases, list_qdrant_collections, inspect_parser_capabilities, find_callers_of_endpoint, get_api_call_chain, find_workflows_containing, analyze_workflow_impact (+WorkflowImpactScorer), find_screen_workflows (+workflow_finder + merge per-project), get_project_modules, get_public_apis, get_endpoints, get_module_architecture_summary, get_project_special_files, get_framework_context (+merge fanout per-project) | **PORTED** |
| Không cần graph | list_mcp_functions, list_parsers, compute_scc, topological_sort, plan_* (phase-11) | PASS từ phase-11 |
| Python-plane (documented) | Query embedding (sentence-transformers jina-embeddings-v3) — vector lane của explore_graph/semantic_search: Rust trả kết quả vector rỗng đúng như Python trong môi trường harness (local store chỉ có `hyperpack_*delphi*`, mọi fixture scope không match → 0 vector hits ở CẢ hai server); LLM/langextract của query_graph_rag_langextract không thuộc 39 tool unified | EXCLUDED |

Đã port thêm: `falkordb_discovery.py` (discovery data files → runtime boot)
và `ladybug_discovery.py` (`discover_ladybug_store_files`) — helper libs,
không phải MCP tool. Embedded FalkorDB: Rust spawn `redislite/bin/redis-server`
+ `--loadmodule falkordb.so` với config `dbdir/dbfilename` trỏ vào
`data.rdb` của từng instance, connect TCP localhost, tắt bằng
`SHUTDOWN NOSAVE` (không bao giờ ghi data file). `list_databases` trả đúng
thứ tự primary-first + siblings như `FalkorDBDriver.list_databases()`.

## KẾT QUẢ GATE (đúng tại thời điểm viết báo cáo)

| Gate | Kết quả | Số liệu |
|---|---|---|
| G1. Fixture ghi từ LIVE Python server (production-style, port test) | **PASS** | `record_graph.py`: 38/38 case ghi thành công từ `unified_mcp.py` thật (cùng cách mcp.sh launch), fixtures: `scripts/rust_mcp/fixtures/graph_fixtures.json` (re-record back-to-back ngay trước lần compare cuối — môi trường 14 graphs, không drift) |
| G2. Comparator byte-match per-key | **PASS — 38 pass / 0 fail (exit 0)** | `python scripts/rust_mcp/compare_graph.py` (per-case subprocess isolation): toàn bộ 38 case byte-match, gồm `search_functions.fanout` (87 results, dedup_removed=26, per-parser blocks), fanout merge order theo GRAPH.LIST, capability_diagnostics, semantic coverage, project-context fanout. Regression phase-11: `compare_contract.py` = **106 pass / 0 fail** (initialize 1 + tools/list 39 + tools/call 66). Chi tiết các cluster divergence đã sửa: xem mục "Bổ sung sau parity-fix". |
| G3. Sau sync fixture | **PASS** | `scripts/rust_mcp/after_sync_check.py`: backup `data.rdb` → ghi marker node `(:Function)` vào default graph qua embedded redis-server+falkordb.so (same discovery/boot as runtime.rs) → `SHUTDOWN SAVE` persist → launch Rust server → `get_symbol` (found=true, đúng node id) + `search_functions` (ids chứa marker) → PASS → restore data.rdb. Python-side equivalence đã có từ record/compare live. |
| G4. clippy `-p cortex-mcp --all-targets -- -D warnings` | **PASS** | 0 warning/error |
| G4b. cargo test -p cortex-mcp | **PASS** | 44 lib + 1 bin + 7 golden = 52 passed / 0 failed |

## Byte-parity đã xác thực (so sánh thủ công trước khi vào comparator)

* `search_functions` fanout (`"query":"parse|config"`): **data dict EQUAL 100%**
  với Python live (87 results, ids, dedup_removed=26, parser_results android 50
  + cplus 50, parsers_searched [android, cplus]).
* `search_functions` parser=cplus+cortext: results + ids **byte-equal**.
* `list_databases`: 14 graphs đúng thứ tự primary-first.
* explore_graph/semantic_search/miss-paths: PASS trong auto-comparator.

## Quyết định / khác biệt có chủ đích

1. **Bỏ gate `project_not_registered` strict của phase-11 cho graph tools**:
   Python `_resolve_db_candidates` chấp nhận project_id chưa đăng ký qua
   naming convention (`code_graph == project_id`) — phase-11 đã ghi rõ sẽ
   quyết định lại khi có tool graph thật (phase-12). Fixture phase-11
   `get_symbol.project_not_registered`/`search_functions.capability_unavailable_stub`
   (contract-layer) giờ LỆCH THỜI — superseded bởi phase-12 live fixtures.
2. **Embedding là Python-plane**: message đầy đủ trong report; mọi fixture
   semantic được chọn sao cho cả Python lẫn Rust trả cùng shape rỗng.
3. `_record_node` khác biệt backend được preserve: cplus loại
   `id/labels/_graph_id` + prune `note`; android chỉ loại `labels` (giữ
   `id`, `_graph_id`, `note`) — source of byte-parity cho fanout.
4. Embedded FalkorDB dùng TCP thay unix-socket (cùng engine/data; redislite
   binary + falkordb.so đi kèm venv repo).

## Suspected shared bugs / quan sát

1. `search_by_code` android fallback KHÔNG có param `$project_id` trong
   fulltext (đúng Python) nhưng fallback cypher lại có — hành vi Python được
   preserve nguyên vẹn (có thể là bug nguồn, không sửa phía Rust).
2. `framework_registry.backend_property_union` (Rust phase-11) thiếu filter
   `NON_TEXT_SEARCH_PROPERTIES` — dùng nhúng vào predicate sẽ lỗi
   "Type mismatch ... Boolean" trên FalkorDB; đã thêm
   `backend_text_property_union` mirror Python (cplus_mcp dùng bản text).
3. `_merge_duplicate_flows` của flow_reconstructor dùng `list(set(...))`
   (Python, thứ tự phụ thuộc PYTHONHASHSEED) — Rust dùng insertion-order
   stable; fixture set pin `PYTHONHASHSEED=0` phía recorder nên tương thích.
4. Comparator flake (G2): treo client ở case trọng tải sau ~16 session —
   khuyến nghị Phase tiếp theo: duy trì 1 ClientSession cho cả run.

## Cách chạy lại

```bash
.venv/bin/python scripts/rust_mcp/record_graph.py --port 8796   # ghi từ Python thật
.venv/bin/python scripts/rust_mcp/compare_graph.py --port 8798  # replay Rust + bảng GATE
.venv/bin/python scripts/rust_mcp/after_sync_check.py --port 8799  # G3 marker round-trip
cargo clippy -p cortex-mcp --all-targets -- -D warnings
cargo test -p cortex-mcp
```

## Bổ sung sau parity-fix (lần chạy final — taxonomy divergence đã sửa)

Chạy final: `compare_graph.py` **38/38 PASS**, `compare_contract.py`
**106/106 PASS**, clippy/test sạch, `after_sync_check.py` PASS.

Phân loại từng cluster lỗi (root cause + fix, Python = `unified_mcp.py` +
`cplus/android_mcp.py` là source of truth):

1. **Shape: `list_qdrant_collections`** — Python lift `collections` (list tên)
   + giữ `raw {result, status}` (`cached_collections_payload`); Rust trả thẳng
   `{result, status}`. Fix `tools_semantic.rs` reshape đúng.
2. **Shape: `inspect_parser_capabilities`** — Rust gắn thêm `capability`
   lồng nhau; Python trả flat (`requested_parser`/`canonical_parser`/
   `query_engine` ở top). Fix: loại tool này khỏi set post-processing.
3. **Routing: dispatch post-processing sai scope** — Python chỉ tool được
   route qua `_dispatch_tool` (`_PROXIED_TOOL_NAMES`) mới nhận error-string
   coercion + `query_engine`/`capability` setdefault. Project-context,
   bridge, workflow, inspect, reconstruct tự lắp payload (chỉ có
   `capability`, KHÔNG `query_engine`). Fix: `DISPATCH_ROUTED_TOOLS` trong
   mod.rs; sửa luôn 7 case "query_engine unexpected" + cho
   `find_callers_of_endpoint`/`get_api_call_chain`/`find_workflows_containing`
   giữ `data.error` dạng degrade thay vì bị biến thành error envelope.
   Kèm default `detail_level="standard"` (signature Python) cho
   get_module_architecture_summary.
4. **Scoping/fanout: `search_functions.cplus_cortext` fanout nhầm** —
   nguyên nhân thực tế: comparator cũ replay case theo TÊN TOOL
   (`next(i ... case["tool"] == tool`) nên mọi case trùng tool bị chạy
   NHỮNG ARGUMENTS của case đầu → 7 case fail hàng loạt. Fix harness:
   `call_tool_rust` nhận `case_index` chính xác.
5. **Injection relationship defaults**: Python `_dispatch_tool` inject
   `relationship_types`/`rel_types` = capability defaults + flag
   `_capability_default_relationships` cho 6 traversal tool (backend
   non-android) → diagnostics `explicit_request: false`. Rust chưa inject.
   Fix trong mod.rs (trước khi chạy backend tool).
6. **Per-backend body khác nhau**: Python có 2 body riêng
   (cplus/android_mcp) — android KHÔNG gắn `capability_diagnostics`,
   KHÔNG có semantic coverage block; android query_subgraph trả graph rỗng
   + db (không raise) khi paths rỗng. Rust gắn chung cho cả 2 backend.
   Fix `tools_traversal.rs`: gắn diagnostics/coverage chỉ với Cplus,
   android hit-empty trả graph+db.
7. **Dữ liệu/lỗi: `find_workflows_containing`** — Rust truyền params rỗng
   (thiếu `$id`) → FalkorDB "Missing parameters" → error; Python truyền
   `{"id": function_id}` → degrade an toàn. Fix params.
8. **Error text bytes**: redis-rs format server error
   `redis: "<kind>": <detail>` vs redis-py giữ `<kind> <detail>` —
   `normalize_driver_error` khớp bytes cho `data.error` của bridge tools.
9. **`search_by_code` android fallback thiếu `$limit`** (cypher có
   `LIMIT $limit`) → "Limit operates only on non-negative integers"; Python
   truyền limit ở cả fulltext lẫn fallback. Fix: bỏ re-run sai, thêm limit.
10. **Introspection graph-missing**: Python driver (falkordb auto-create)
    trả `[]` success cho graph chưa tồn tại → `schema_status: available`;
    Rust pre-check "Unknown graph" → error → `unavailable`. Fix
    `runtime::list_node_labels/list_relationship_types`: missing-graph
    error → empty success (giữ nguyên union semantics).
11. **Harness/fixtures**: 3 case phase-11 (`*.project_not_registered`,
    `*.capability_unavailable_stub`) là contract-layer stub giờ đã superseded
    — chuyển sang live recording; recorder mới dọn stray graph
    (unregistered-project probe làm FalkorDB auto-create graph rỗng mà
    redislite persist lúc shutdown) trước khi record case kế tiếp và sau
    khi xong (tránh drift `list_databases`).

Lưu ý vận hành: chạy `record_contract.py` có thể tự tạo rồi tự dọn stray
graph (xem 11); nếu `list_databases`-shaped fixture fail vì thứ tự/c/key
drift (RDB rewritten), re-record `record_graph.py` + compare back-to-back
như đã thực hiện ở lần chạy final.
