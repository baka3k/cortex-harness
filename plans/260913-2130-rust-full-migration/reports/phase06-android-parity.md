# Phase 06 — android analyzer parity (python vs rust)

- chạy: 2026-09-14 04:16:13
- testdata: `tests/fixtures/android-analyzer`
- mask: `['_dst', '_edge_id', '_graph_id', '_src', 'created_at', 'last_updated', 'summary_updated_at', 'updated_at']`
- grammar pins: PyPI tree-sitter-kotlin 1.1.0 ↔ crate `tree-sitter-kotlin-ng` 1.1.0 (cùng repo tree-sitter-grammars/tree-sitter-kotlin tag v1.1.0); runtime tree-sitter 0.26.0 (PyPI) ↔ 0.25 (Rust workspace)

### scan_result testdata_full

- py: `[SCAN_RESULT] parser=android-kotlin files=6 functions=36 classes=18`
- rust: `[SCAN_RESULT] parser=android-kotlin files=6 functions=36 classes=18`


### TESTDATA_FULL

- nodes: py=107 rust=107
- edges: py=181 rust=181
- diff_total: **0**


### scan_result inc_seed

- py: `[SCAN_RESULT] parser=android-kotlin files=6 functions=36 classes=18`
- rust: `[SCAN_RESULT] parser=android-kotlin files=6 functions=36 classes=18`


### INC_SEED

- nodes: py=107 rust=107
- edges: py=181 rust=181
- diff_total: **0**


### scan_result inc_run

- py: `[SCAN_RESULT] parser=android-kotlin files=3 functions=12 classes=2`
- rust: `[SCAN_RESULT] parser=android-kotlin files=3 functions=12 classes=2`

- cleanup: py=(20, 0) rust=(20, 0)


### INC_RUN

- nodes: py=101 rust=101
- edges: py=158 rust=158
- diff_total: **0**


## Kết luận

- FAILURES: không có — PASS toàn bộ


## Scope port

- `tools/android/android_kotlin_analyzer.py` (4.6k, entry `android` trong ANALYZERS) toàn bộ parse + row assembly + writer calls (`write_nodes_batch` per label + `write_relations_typed` + `write_calls_with_site`, node queries copy nguyên chữ).
- `tools/android/android_common.py` — phần được analyzer dùng: symbol-id helpers, `_parse_android_manifest` (DOM + serializer khớp `ET.tostring`), `_normalize_rel_path`, directory nodes/relations. `_extract_resource_ids_from_xml` không có hiệu ứng graph (regex broken phía Python ⇒ luôn rỗng).
- `android_java_analyzer.py` / `android_mixed_analyzer.py` KHÔNG thuộc entry này: orchestrator chỉ đăng ký `android_kotlin_analyzer.py` cho parser `android` và bản kotlin không import 2 file kia (mixed là script orchestrator riêng, chạy java+kotlin bằng subprocess).
- Qdrant/embedding, message scan, parse cache, neo4j resume: nhận cờ và bỏ qua (embedding/message là plane Python; quyết kiến trúc phase 04).

## Grammar pins

- PyPI `tree-sitter-kotlin==1.1.0` (repo tree-sitter-grammars/tree-sitter-kotlin; `_get_kotlin_parser` fallback sang grammar crate vì tree-sitter-languages 1.10.2 raise TypeError với tree-sitter ≥0.25) ↔ crates.io `tree-sitter-kotlin-ng==1.1.0` (cùng tag v1.1.0). crates.io `tree-sitter-kotlin` (fwcd) là grammar KHÁC — không dùng.
- Runtime: PyPI `tree-sitter==0.26.0` ↔ Rust `tree-sitter@0.25` (workspace pin, như analyzer-java với `tree-sitter-java@0.23.5`).

## Divergence đã ghi nhận (không ảnh hưởng graph parity)

1. **Call rows gắn `project_id` tường minh (Rust)** — bản Python build rows không có `project_id` và chỉ chạy được khi orchestrator set graph-journal env (writer stamp từ journal metadata). Rust gắn trực tiếp cùng giá trị; writer stamp như nhau 2 bên (props CALLS edge giống hệt). Không có journal env, Python crash ở `write_calls_with_site` (fail-closed) — parity harness set journal shadow mode cho cả 2. Limitation phía analyzer Python, không phải shared crate.
2. **Regex double-backslash của Python giữ nguyên semantics** — nhiều pattern trong analyzer gốc là raw-string \\s (backslash literal + `s*`) nên thực tế không match text thường: `_parse_gradle_file` (namespace/applicationId/dependency), `_extract_resource_refs`, `_extract_resource_ids_from_xml`, kotlin `_extract_class_refs`, `_extract_handler_tokens`, `_extract_intentfilter_*`, `_extract_component_name_target`, intent-var regexes, `_extract_route_from_args` nhánh 1, `startDestination` (⇒ `start_routes` luôn rỗng). Port copy từng chữ.
3. **Python crash khi `composable("route") { … }` trailing-lambda** — `_extract_compose_routes_from_tree` compile pattern lỗi cú pháp (unbalanced group, `re.error`) khi composable không extract được target từ lambda; trailing lambda không nằm trong subtree `call_expression` của grammar ⇒ targets rỗng ⇒ crash. Fixtures dùng dạng `composable(route=…, content = { … })` (Python chạy được); Rust treat pattern như no-match và tiếp tục (Python crash ⇒ không có graph để so).
4. **Incremental phải chạy trên graph đã seed** — analyzer infer AndroidComponent từ EXTENDS của mọi file (index_payloads) nhưng Class node chỉ ghi cho file selected; incremental trên graph rỗng làm cả Python lẫn Rust fail-closed ở endpoint preflight (hành vi giống nhau). Harness cho chạy full-seed trước rồi incremental trên cùng graph (production flow).

