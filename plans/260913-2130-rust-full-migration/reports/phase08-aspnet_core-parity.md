# Phase 08 — aspnet_core overlay parity (python vs rust)

- chạy: 2026-09-14 05:49:43
- fixture: `tests/fixtures/web-overlays/aspnet_core`
- rust bin: `/Users/hieplq1.aip/AI/cortex-harness/rust/target/release/analyzer-aspnet-core`
- base parser (prerequisite): `csharp` (python, journal-shadow)
- mask: `['_dst', '_edge_id', '_graph_id', '_src', 'created_at', 'last_updated', 'summary_updated_at', 'updated_at']` + `_start_id`/`_end_id`

### testdata_full

- stdout py: `[aspnet_core] modules=2 facts=46 relationships=39 diagnostics=3 coverage=partial | [aspnet_core] graph={'stage': 'applied', 'nodes': 46, 'relationships': 39, 'preserved_modules': 0}`
- stdout rust: `[aspnet_core] modules=2 facts=46 relationships=39 diagnostics=3 coverage=partial | [aspnet_core] graph={'stage': 'applied', 'nodes': 46, 'relationships': 39, 'preserved_modules': 0}`
- byte-identical: **True**

- nodes: py=77 rust=77
- edges: py=79 rust=79
- diff_total: **0**

- preview byte-identical: **True**

- diagnostics byte-identical: **True**


### inc_run

- stdout py: `[aspnet_core] modules=2 facts=44 relationships=41 diagnostics=3 coverage=partial | [aspnet_core] graph={'stage': 'applied', 'nodes': 44, 'relationships': 41, 'preserved_modules': 0}`
- stdout rust: `[aspnet_core] modules=4 facts=44 relationships=41 diagnostics=5 coverage=partial | [aspnet_core] graph={'stage': 'applied', 'nodes': 44, 'relationships': 41, 'preserved_modules': 0}`
- byte-identical: **False**

- nodes: py=74 rust=74
- edges: py=81 rust=81
- diff_total: **0**


## Kết luận

- FAILURES: ['inc_run: stdout byte-identical']

