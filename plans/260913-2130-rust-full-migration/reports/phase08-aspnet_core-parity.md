# Phase 08 — aspnet_core overlay parity (python vs rust)

- chạy: 2026-09-14 06:03:30
- fixture: `tests/fixtures/web-overlays/aspnet_core`
- rust bin: `/Users/user/AI/cortex-harness/rust/target/release/analyzer-aspnet-core`
- base parser (prerequisite): `csharp` (python, journal-shadow)
- mask: `['_dst', '_edge_id', '_graph_id', '_src', 'created_at', 'last_updated', 'summary_updated_at', 'updated_at']` + `_start_id`/`_end_id`

### testdata_full

- stdout py: `[aspnet_core] modules=2 facts=46 relationships=39 diagnostics=3 coverage=partial | [aspnet_core] graph={'stage': 'applied', 'nodes': 46, 'relationships': 39, 'preserved_modules': 0}`
- stdout rust: `[aspnet_core] modules=2 facts=46 relationships=39 diagnostics=3 coverage=partial | [aspnet_core] graph={'stage': 'applied', 'nodes': 46, 'relationships': 39, 'preserved_modules': 0}`
- byte-identical: **True**

- nodes: py=77 rust=77
- edges: py=79 rust=79
- diff_total: **0**

- preview identical (diagnostics order normalized): **True**

- diagnostics identical (diagnostics order normalized): **True**


### inc_run

- stdout py: `[aspnet_core] modules=2 facts=44 relationships=41 diagnostics=3 coverage=partial | [aspnet_core] graph={'stage': 'applied', 'nodes': 44, 'relationships': 41, 'preserved_modules': 0}`
- stdout rust: `[aspnet_core] modules=2 facts=44 relationships=41 diagnostics=3 coverage=partial | [aspnet_core] graph={'stage': 'applied', 'nodes': 44, 'relationships': 41, 'preserved_modules': 0}`
- byte-identical: **True**

- nodes: py=74 rust=74
- edges: py=81 rust=81
- diff_total: **0**


## Kết luận

- FAILURES: không có — PASS toàn bộ


## Ghi chú parity (phase 08 — aspnet_core)

- **Semantic engine**: như aspnet_framework — dùng chung roslyn worker;
  `resolve_roslyn_evidence` port đủ kinds (Controller/RazorPage/Repository/
  Service/Model, Action/PageHandler, Middleware, minimal API endpoints/routes,
  Add* services, GetSection/GetValue/GetConnectionString config, PASSES_THROUGH
  pipeline, attribute routes).
- **Artifact parsers**: `parse_razor` (@page/@model/Layout/partial +
  PartialAsync), `parse_appsettings` (flatten_json với prefix ":", environment
  từ tên file, duplicate-key diagnostics) — đỏm SENSITIVE_KEY_RE redaction
  ("[REDACTED]" cho ConnectionStrings…) và `_CONNECTION_SECRET_RE`
  (`Password=[REDACTED]`). <!-- sensitive-guard:allow (ten flag / test sample, khong phai secret) -->
- **Base seeding (csharp)**: `--disable-roslyn` — xem giải thích ở report
  aspnet_framework.
- **Deleted module cleanup**: incremental xoá module SecondApp — detect_path →
  infer_deleted_module_path → module rỗng `evidence=[path:deleted]` → empty
  generation cleanup; `live_module_ids` cập nhật trong loop (tránh trùng
  cleanup module) khớp Python.
- **Redaction**: preview/diagnostics output byte-identical với redact_value
  chạy trên toàn `asdict(result)` như `to_dict()`.

