# Phase-04 — Per-parser retirement (drop-out purge graph + Qdrant collection)

Mục tiêu: khi một parser từng sync root này rơi khỏi effective set (full-scan/reconcile), dữ liệu cũ
của nó trong graph + Qdrant phải biến mất — không thì triệu chứng gốc (COBOL tồn đọng) không được sửa
và secret đã xoá vẫn searchable trong vector cũ.

**Điều kiện tiên quyết: 260915-2300-sync-plane-rust-cutover phase-02 đã commit** (cần
`Box<dyn GraphStore>` polymorphic qua `open_store_from_env()`; WIP Ladybug cùng vùng graph-writer).

## Changes

0. **Probe ownership (step 0, chặn trước khi viết purge query)**: xác định property đánh dấu parser
   origin trên node do analyzer children ghi (Function/File/Class…): grep writer của từng analyzer Rust
   + dump sample graph (`graph-state dump` từ cutover phase-04). Nếu không có property parser/language
   thống nhất → thêm stamp `parser_origin` vào write path các analyzer (mở rộng phase này, có mục riêng)
   và ghi nhận old-node caveat: node cũ không stamp chỉ retire được khi user chạy `--reconcile` rebuild.
1. **Retirement detection** (orchestrator, sau khi có effective set + grouping):
   - State: mở rộng state file hiện có (state.rs) lưu `last_full_parser_set` per root-hash.
   - Transition chỉ tính ở **full scan hoặc `--reconcile`** (D4): `retire = last_full_parser_set −
     current_effective_set` (current = parser có routed>0). Incremental không bao giờ trigger retirement.
   - **Two-strike hysteresis**: parser phải vắng mặt ở 2 full scan liên tiếp mới retire (state lưu
     `missing_strikes`); `--reconcile` bỏ qua hysteresis (retire ngay, user tường minh).
2. **Graph purge** (cortex-graph-writer — module mới `parser_retirement.rs` kế bên topology.rs):
   - Query `MATCH (n) WHERE n.project_id_normalized = $p AND n.parser_origin = $parser DETACH DELETE n`
     + biến thể Ladybug nếu dialect khác (mirror pattern CLEANUP_*_LADYBUG_QUERY đang WIP trong
     topology.rs).
   - Include message nodes (`language = parser`) khi parser nằm trong message_enabled_parsers.
3. **Qdrant collection delete**: `code_collection_name(project_id, root, parser)` (registry.rs:492) →
   drop collection qua vector store client đã có (vector_store.rs). Không delete collection của parser
   còn effective.
4. **Summary evidence**: `summary["retired_parsers"] = [{parser, graph_nodes_deleted, collection}...]` +
   log `[retire] parser=cobol nodes=42 collection=…` — mọi xoá dữ liệu phải audit được.
5. **Safety**: retirement chạy SAU khi các pass khác thành công (không retire khi run lỗi giữa chừng);
   dry-run in retirement plan mà không xoá.

## Gates

- Fixture 2-leg: sync cây có `prog.cbl` (leg 1) → xoá `.cbl`, full sync (leg 2, strike 1) → chưa xoá
  (hysteresis); full sync lần 2 (strike 2) → graph nodes COBOL + collection `…_cobol_functions` biến
  mất; summary có `retired_parsers`.
- `--reconcile` bỏ hysteresis: 1 leg là đủ xoá.
- Flicker test: parser vắng ở incremental → không bao giờ xoá.
- Idempotent: retire 2 lần → lần 2 là no-op với counts 0.
- Provider matrix: neo4j + ladybug (sau cutover) cùng pass.
- `cargo test -p cortex-sync -p cortex-graph-writer` + clippy xanh.
