# Ladybug runtime port — BÁO CÁO

Ngày: 2026-09-15. Tiếp nối `reports/phase03-04-vector-lane.md`: loại bỏ hạn chế
cuối cùng của MCP Rust trên instance ladybug.

## Kết quả

| Mục | Trước | Sau |
|---|---|---|
| Golden replay (compare_vector --rust) | 26/32 | **28/32** (expand_graph 2 case PASS thêm; 4 explore còn là fusion numerics) |
| Live mind graph tools (ladybug) | `storage_unavailable` | **OK** — `list_source_ids`, `get_paragraph_text`, graph-expansion của `query_graph_rag` |
| Live smoke toàn bộ | — | **44/44 tool** (39 graph + 5 mind) |
| `cargo clippy -p cortex-mcp -D warnings` / `cargo test` | — | **0 warning / 65 passed** |

## Triển khai

1. **`graph/ladybug.rs`** — `LadybugStore`: mở store FILE `<owner>.lbug/<graph>`
   in-process qua crate `lbug` 0.20.4 (cùng engine + format với PyPI `ladybug`
   mà Python dùng). `Connection<'a>` borrow `Database` → leak `Box<Database>`
   thành `'static` (server store sống cùng process; cả hai đều `Send + Sync`).
   - Query: `prepare` + `execute` với params; `Param` (falkordb) → `lbug::Value`
     (Null→LogicalType::Any, List infer child type, Struct/Map đầy đủ).
   - Rows → `Vec<Map>` cùng shape normalize như `cortex_falkordb::normalize`
     (Node → props + `_graph_id` + `_label`; Rel → props + `_type`).
   - Error text: strip prefix `Query execution failed: ` của `lbug::Error` để
     khớp error contract Python (binder exception text byte-equal).
   - Discovery: `LADYBUG_{CODE,DOC}_PATH` primary + sibling files trong cùng
     `.lbug` dir (`sibling_store_files`).
2. **`GraphRuntime`** (`graph/runtime.rs`) — thêm `ladybug_clients`;
   `boot_ladybug_stores` mở primary RW → fallback RO → skip + warning;
   `execute_query` route ladybug trước falkordb (exact name match, hoặc store
   duy nhất khi tên registry lệch tên file); `list_databases` merge tên.
3. **Schema introspection dialect** — `db.relationshipTypes()`/`db.labels()`
   là procedure FalkorDB; trên ladybug route sang `CALL show_tables() RETURN *`
   (type REL → rel types uppercase; NODE → labels giữ case) — mirror
   `LadybugDriver.list_relationship_types/list_labels`. Đây là mảnh ghép cuối
   làm `expand_graph` PASS (26 relationship types introspect đúng, expansion
   query chạy trên ladybug thật, error text byte-equal).
4. **mind `DocGraphStore`** — backend enum `Falkor | Ladybug`;
   `is_ladybug_provider()` (env `DOC_GRAPH_PROVIDER`/`GRAPH_PROVIDER`/
   `LADYBUG_DOC_PATH`); scoped project → store file `<dir>/<doc_graph>` (missing
   → skip candidate, parity với fail-soft fan-out); unscoped → primary env file.

## Còn lại (track riêng: fusion numerics)

4/23 golden case `explore_graph` lệch điểm fusion khi có nhiều tín hiệu thật:
`signals.semantic` của node hạng ≥2 (vd 0.978 vs 0.7029). Chuỗi nguyên nhân nằm ở
lớp query-understanding/normalize/weights của explore (phase-12 chỉ validate
fusion với vector seeds rỗng). Cần record fusion inputs từ cả 2 phía rồi so
từng bước normalize — việc kế tiếp của graph track, không chặn vận hành
(semantic_search + toàn bộ mind đã parity).

## Vận hành

- Binary release đã rebuild; `dev start` khởi động lại cả 2 server trên Rust.
- Rollback per-service: `CORTEX_MCP_BACKEND=python dev start`.
