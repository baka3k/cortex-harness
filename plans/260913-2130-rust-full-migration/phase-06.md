# Phase 06 — WAVE D batch 2: JVM analyzers (java, kotlin, android*)

## Scope

| Analyzer | Python LOC | Ghi chú |
|---|---|---|
| `tools/java/` | 2.6k | Class/Interface/Enum/Method, SpringBean detection để dành Phase 08 |
| `tools/kotlin/` | 2.4k | tree-sitter-kotlin |
| `tools/android/` | 6.7k | android_java + android_kotlin + android_mixed: AndroidManifest, Activity/Fragment, NavRoute, IntentAction, HandlerMessage, Resource — đầy đủ nhất về framework labels |

## Điểm rủi ro riêng

- Android analyzer scan `AndroidManifest.xml` + resources (XML parsing trong Rust —
  `quick-xml`); `android_mixed` cần kết hợp kết quả 2 analyzer java/kotlin — khớp
  thứ tự merge của Python.
- Grammar java/kotlin wheels vs Rust crates: so snapshot AST trên testdata trước.

## Parity

- stock không phải repo Android → bổ sung testdata: `code-tiny/testdata` android samples +
  (nếu có) 1 repo Android thật người dùng chỉ định.
- Harness Phase 04 dual-run cho từng analyzer; android_mixed test riêng pipeline gộp.

## Gate

- [x] 3/3 analyzer (java, kotlin, android×3 mode) parity pass trên testdata + repo mẫu.
- [x] Overlay labels (AndroidNavRoute, HandlerMessage...) đủ theo CODE_GRAPH_SCHEMA.

**Trạng thái 2026-09-14:** 3/3 PASS toàn bộ gate (reports/phase06-{java,kotlin,android}-parity.md)
— FULL diff 0 ngoài mask, incremental cleanup khớp, SCAN_RESULT byte-identical.
Grammar: java 0.23.5 cả 2 bên; kotlin-ng 1.1.0 (crates.io `tree-sitter-kotlin` stale ở
0.3.8, PyPI 1.1.0 build cùng grammar fwcd); android dùng kotlin-ng + quick-xml DOM với
namespace resolution cho AndroidManifest/res. Overlay labels (IntentAction,
HandlerMessage, NavRoute, UsesResource...) theo CODE_GRAPH_SCHEMA — port trong
`analyzer-android` (18 labels qua write_nodes_batch). Ghi chú: Python reference crash
(re.error) trên composable trailing-lambda — bug tham chiếu, fixtures dùng dạng
parenthesized; Rust xử lý no-match.
