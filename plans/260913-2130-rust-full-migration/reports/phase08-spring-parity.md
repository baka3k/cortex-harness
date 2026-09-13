# Phase 08 — Spring overlay parity (python vs rust)

- chạy: 2026-09-14 06:20:44
- testdata: `tests/fixtures/java-spring-overlays`
- rust bin: `/Users/hieplq1.aip/AI/cortex-harness/rust/target/release/analyzer-spring`
- base seed: java analyzer Python (journal shared-shadow) trên CẢ HAI graph
- mask: `['_dst', '_edge_id', '_graph_id', '_src', 'created_at', 'last_updated', 'summary_updated_at', 'updated_at']` + `_start_id`/`_end_id`

### lines testdata_full

- py: `['[spring] modules=1 configs=7 language_facts=10 semantic_facts=29 relationships=25 diagnostics=0', '[falkordb] spring_facts 29/29', '[falkordb] spring_relationships 25/25']`
- rs: `['[spring] modules=1 configs=7 language_facts=10 semantic_facts=29 relationships=25 diagnostics=0', '[falkordb] spring_facts 29/29', '[falkordb] spring_relationships 25/25']`

- testdata_full spring_facts.json artifact: py=47017B rs=47017B =PASS=


### TESTDATA_FULL

- nodes: py=82 rust=82
- edges: py=105 rust=105
- diff_total: **0**


### lines inc_seed

- py: `['[spring] modules=1 configs=7 language_facts=10 semantic_facts=29 relationships=25 diagnostics=0', '[falkordb] spring_facts 29/29', '[falkordb] spring_relationships 25/25']`
- rs: `['[spring] modules=1 configs=7 language_facts=10 semantic_facts=29 relationships=25 diagnostics=0', '[falkordb] spring_facts 29/29', '[falkordb] spring_relationships 25/25']`

- inc_seed spring_facts.json artifact: py=47061B rs=47061B =PASS=


### INC_SEED

- nodes: py=82 rust=82
- edges: py=105 rust=105
- diff_total: **0**


### lines inc_run

- py: `['[spring] modules=1 configs=4 language_facts=1 semantic_facts=13 relationships=8 diagnostics=1', '[falkordb] spring_facts 13/13', '[falkordb] spring_relationships 8/8']`
- rs: `['[spring] modules=1 configs=4 language_facts=1 semantic_facts=13 relationships=8 diagnostics=1', '[falkordb] spring_facts 13/13', '[falkordb] spring_relationships 8/8']`

- inc_run spring_facts.json artifact: py=18829B rs=18829B =PASS=


### INC_RUN

- nodes: py=64 rust=64
- edges: py=86 rust=86
- diff_total: **0**


## Kết luận

- FAILURES: không có — PASS toàn bộ

