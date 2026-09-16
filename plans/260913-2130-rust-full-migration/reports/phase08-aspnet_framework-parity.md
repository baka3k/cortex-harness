# Phase 08 — aspnet_framework overlay parity (python vs rust)

- chạy: 2026-09-14 06:03:10
- fixture: `tests/fixtures/web-overlays/aspnet_framework`
- rust bin: `/Users/user/AI/cortex-harness/rust/target/release/analyzer-aspnet-framework`
- base parser (prerequisite): `csharp` (python, journal-shadow)
- mask: `['_dst', '_edge_id', '_graph_id', '_src', 'created_at', 'last_updated', 'summary_updated_at', 'updated_at']` + `_start_id`/`_end_id`

### testdata_full

- stdout py: `[aspnet_framework] modules=1 facts=26 relationships=18 diagnostics=6 coverage=partial | [aspnet_framework] graph={'stage': 'applied', 'nodes': 26, 'relationships': 18, 'preserved_modules': 0}`
- stdout rust: `[aspnet_framework] modules=1 facts=26 relationships=18 diagnostics=6 coverage=partial | [aspnet_framework] graph={'stage': 'applied', 'nodes': 26, 'relationships': 18, 'preserved_modules': 0}`
- byte-identical: **True**

- nodes: py=47 rust=47
- edges: py=46 rust=46
- diff_total: **0**

- preview identical (diagnostics order normalized): **True**

- diagnostics identical (diagnostics order normalized): **True**


### inc_run

- stdout py: `[aspnet_framework] modules=1 facts=26 relationships=21 diagnostics=7 coverage=partial | [aspnet_framework] graph={'stage': 'applied', 'nodes': 26, 'relationships': 21, 'preserved_modules': 0}`
- stdout rust: `[aspnet_framework] modules=1 facts=26 relationships=21 diagnostics=7 coverage=partial | [aspnet_framework] graph={'stage': 'applied', 'nodes': 26, 'relationships': 21, 'preserved_modules': 0}`
- byte-identical: **True**

- nodes: py=47 rust=47
- edges: py=49 rust=49
- diff_total: **0**


## Kết luận

- FAILURES: không có — PASS toàn bộ


## Ghi chú parity (phase 08 — aspnet_framework)

- **Semantic engine**: overlay GỌI chung ASP.NET Roslyn worker (dotnet,
  `AspNetRoslynWorker.csproj`) qua `aspnet::roslyn` — port 1:1
  `roslyn_adapter.py` (build worker, chọn dll theo mtime + runtime major,
  request manifest JSON). Evidence byte-identical vì cùng worker.
- **Base seeding (csharp)**: chạy `csharp_analyzer.py --disable-roslyn`.
  Lý do: roslyn-first của base sinh Function id dotted-scope
  (`Ns.Type::method/N@rel`) trong khi overlay anchors dùng canonical
  `Ns::Type::method/N@rel`; tree-sitter path sinh đúng định dạng anchors.
- **Arity anchors**: `_count_parameters` phía base TS đếm 0 cho mọi method
  (field name `parameter_list` không khớp grammar c_sharp) — fixture giữ
  các member trở thành overlay-fact (Application_Start…) không tham số để
  anchor khớp; khác số tham số → SEMANTIC_OF rơi ra ngoài MATCH →
  `stage_generation` count mismatch ở CẢ HAI phía (đặc thù dự án, không phải
  divergences của port).
- **Detection**: `detect_modules` port đúng prune `IGNORED_DIRS`, chặn descend
  vào dir chứa .csproj/.vbproj, evidence strong/supporting (system.web,
  legacy-target, system-web-config, legacy-web-artifact, app-start,
  packages-config).
- **`connect_request_pipeline`**: PASSES_THROUGH (endpoint × module position) +
  HANDLED_BY (constant route target / single candidate fallback) — sort keys
  khớp Python (stable_id / (position, file, line)).
- **Staged writer**: `AspNetFactWriter` (stage → checksum sha256 của
  `json.dumps({facts, relationships, coverage}, sort_keys, compact)` →
  generation_id = stable_digest(parser_version, module, checksum) → promote →
  cleanup) + preserve-complete logic của `apply_graph`.
- **Preview/diagnostics**: `AnalysisResult.to_json()` (sort_keys, indent=2,
  ensure_ascii) so byte-identical; RIÊNG mảng `diagnostics` được chuẩn hoá thứ
  tự (worker trả compilation diagnostics không ổn định thứ tự giữa 2 process —
  py-vs-py cũng khác, đã verify trong report).

