# Phase 03 — dual-write graph diff

- fixture: `rust/crates/cortex-graph-writer/tests/fixtures/writer_rows_testdata.json`
- graph Python: `stock_pw2` / graph Rust: `stock_rw2`
- nodes: py=52 rust=52
- edges: py=37 rust=37
- diff_total (ngoài mask ['_dst', '_edge_id', '_graph_id', '_src', 'created_at', 'last_updated', 'summary_updated_at', 'updated_at']): **0**

**PASS** — diff rỗng ngoài mask.
