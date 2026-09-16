# Phase 08 — fastapi_django overlay parity (python vs rust)

- chạy: 2026-09-14 06:02:32
- fixture: `tests/fixtures/web-overlays/fastapi_django`
- rust bin: `/Users/user/AI/cortex-harness/rust/target/release/analyzer-fastapi-django`
- base parser (prerequisite): `python` (python, journal-shadow)
- mask: `['_dst', '_edge_id', '_graph_id', '_src', 'created_at', 'last_updated', 'summary_updated_at', 'updated_at']` + `_start_id`/`_end_id`

### testdata_full

- stdout py: `[overlay] framework=fastapi_django endpoints=12 relationships=12 graph={'nodes': 12, 'relationships': 12, 'deleted': 0}`
- stdout rust: `[overlay] framework=fastapi_django endpoints=12 relationships=12 graph={'nodes': 12, 'relationships': 12, 'deleted': 0}`
- byte-identical: **True**

- nodes: py=36 rust=36
- edges: py=43 rust=43
- diff_total: **0**


### inc_run

- stdout py: `[overlay] framework=fastapi_django endpoints=5 relationships=5 graph={'nodes': 5, 'relationships': 5, 'deleted': 0}`
- stdout rust: `[overlay] framework=fastapi_django endpoints=5 relationships=5 graph={'nodes': 5, 'relationships': 5, 'deleted': 0}`
- byte-identical: **True**

- nodes: py=26 rust=26
- edges: py=27 rust=27
- diff_total: **0**


## Kết luận

- FAILURES: không có — PASS toàn bộ


## Ghi chú parity (phase 08 — fastapi_django)

- **Port**: `tools/web_framework/` (109 + 198 + models 77 LOC) → `web::pipeline`
  + `web::models` + `web::writer`; regex `_FASTAPI_RE`/`_DJANGO_RE` port với
  `(?is)`/`(?m)` đúng flags Python. `stable_id` = `web::` + sha256[:32] của
  `"\x1f".join(str(p).strip())`.
- **Semantic engine**: KHÔNG dùng `SemanticInferenceEngine` (overlay thuần regex
  + symbol index); handler resolution từ symbol index tự scan (def/class/function).
- **Writer**: `WebFrameworkWriter` qua `GraphStore.execute_query` — MERGE
  `ApiEndpoint {id}` + `SET node += row` + `HANDLES`/`SEMANTIC_OF` có điều kiện
  match handler (name/file_path/scope) như Python; `delete_paths` per framework.
- **Incremental**: manifest đọc key `paths` (dict) hoặc list — ĐÚNG chữ ký
  `_manifest()` của overlay (khác `load_manifest_paths` của orchestrator).
- **project_id**: node/relationship rows đều mang `project_id` tường minh
  (writer contract); Python overlay cũng tự điền nên không cần journal env.

