[inline:log-writer] — material session (7-phase plan implementation + review cycle); written inline because the full implementation context lives in this session and the entry is one logical event.

# Optional `repo` scope param cho code-side MCP query tools — 2026-10-16

## Context

Người dùng cần lặp lại query dạng `MATCH (n)-[e]-(m) WHERE n.repo ENDS WITH "s3"` qua các
MCP tool hiện có mà không phải viết raw Cypher. Thực thi plan
[`261016-1100-repo-scope-code-mcp-queries`](../plans/261016-1100-repo-scope-code-mcp-queries/plan.md)
(hi-craft --full: 7 phases + research + review).

## Change

- **Helper trung tâm** — `code-tiny/tools/common/project_scope.py`: `prepare_repo_scope_parameters`
  (blank → `''` + inject sibling `repo_suffix = "/" + repo`), `repo_scope_predicate(alias)`,
  `matches_repo_scope` (Python twin). Response shaping dùng chung ở
  `code-tiny/mcp/repo_scope_support.py` (scope block, empty-warnings với top-5 DISTINCT repo,
  anchor post-filters, vector over-fetch `REPO_POSTFETCH_FACTOR = 4`).
- **4 backend** (`fastmcp_server.py`, `cplus_mcp.py`, `android_mcp.py`, `java_mcp.py`) +
  `tools/graph/core/cypher_driver.py` (find_function_paths / query_function_subgraph /
  _find_module_paths_directed / list_possible_calls): `repo: Optional[str] = None` trên mọi
  tool query đọc, predicate đặt đúng phía anchor (RR2 — s-only cho module path, cả 2 endpoint
  cho id-addressed, caller-side cho planner/list_possible_calls).
- **Unified** (`unified_mcp.py`): 2 bridge tools mới nhận repo (find_callers_of_endpoint,
  get_api_call_chain); fan-out promote `scope` từ parser đầu tiên + merge `warnings` riêng
  (plain strings, dedupe); `explore_graph` over-fetch + post-filter `matched_nodes`.
- **Vector** — `semantic_search` trên cả 4 backend: over-fetch `limit×4` khi scoped rồi
  post-filter Python trước khi format content (RR5).
- **Surface gate** — `tests/test_unified_mcp_wrapper_signatures.py::test_repo_scope_acceptance_gate_live_schema`
  assert live registry: mọi query-read tool có `repo`, tool excluded không có;
  `tool_metadata.py` 20 catalog entries thêm input `repo`.
- **Docs** — `docs/REPO_QUERY_RULES.md` (RR1–RR5 + bảng anchor + constraint LadybugDB),
  `docs/mcp-output-contract.md` section scope block repo-side, `code-tiny/mcp/README.md`.

## Impact

- Mọi MCP client có thể scope theo repo trên ~24 query tool; unscoped không đổi hành vi
  (predicate no-op, chỉ thêm `scope`/`warnings` additive trong `data` — envelope v2 bất biến).
- Risk: medium→low — đã smoke 18/18 trên FalkorDBLite + LadybugDB thật; full suite
  1997 passed, 20 failures y hệt baseline (0 new); live-schema gate chặn drift tương lai.
- Tool mới thêm sau này quên `repo` sẽ fail gate — hoặc thêm param hoặc vào exclusion list kèm lý do.

## Decision

1. **Fragment `($repo = '' OR …)` thay vì `$repo IS NULL` (deviation khỏi plan, có verify).**
   Live smoke bắt được: LadybugDB bind parameter `None` thành BOOL → `$repo IS NULL` định kiểu
   `$repo` BOOL → `n.repo = $repo` nổ `Conversion exception` lúc runtime. Pattern `project_id`
   sống sót vì nhánh IS NULL dùng param khác (`$project_id_normalized`). Empty-string giữ mọi
   reference STRING trên cả 3 backend. Chi tiết: `docs/REPO_QUERY_RULES.md` §2.
2. **Exclusion thay vì apply-thật cho Workflow tools + project-context** — verify node không có
   property `repo` (language_writer/flow_ops/message_scan) → filter sẽ luôn rỗng.
3. **Post-filter thay vì đụng driver core cho id-tools** (get_symbol/get_node_details) — giữ
   blast radius nhỏ; module-path/id-path tools đi qua `cypher_driver` thì extend kwarg `repo`.

## Review

Reviewer độc lập chấm 7/10, 1 CRITICAL + 8 findings — **fix hết ở cycle 1**:
CRITICAL = fan-out scoped-empty crash `TypeError: unhashable type: 'dict'` (warnings bị tag
thành result items rồi bị hash trong dedupe) → merge `warnings` riêng trong
`_merge_fanout_results` + 5 regression tests (`tests/test_repo_scope_fanout.py`).

## References

- plan: ./docs/plans/261016-1100-repo-scope-code-mcp-queries/plan.md
- smoke: ./docs/plans/261016-1100-repo-scope-code-mcp-queries/smoke-report.md (18/18)
- rules: ./docs/REPO_QUERY_RULES.md
- commit: ad03e59
- tests: tests/test_repo_scope_{fastmcp,cplus,android_java,vector,fanout}.py + gate trong
  tests/test_unified_mcp_wrapper_signatures.py
