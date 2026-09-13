# Phase 01: FalkorDB Rust client spike — go/no-go cho toàn bộ graph path

## Mục tiêu

Chứng minh Rust nói chuyện được với **remote FalkorDB qua Redis protocol** (localhost:6379
SSH tunnel như procsample/stock đang dùng) đầy đủ enough cho writer + MCP queries.
Đây là điều kiện tiên quyết của Phase 03–08; fail thì cả chương trình đổi hướng
(graph path giữ Python, Rust gọi qua PyO3 bridge).

## Spike scope

1. `redis-rs` + `GRAPH.ROQUERY stock "MATCH (n:Function) RETURN n LIMIT 10"`.
2. Parse result set của FalkorDB: header (column names), records (Node/Relationship/Scalar
   — binary format của FalkorDB), statistics row. Ưu tiên dùng procedure `db.labels()`,
   `db.relationshipTypes()` như Python driver đang gọi.
3. Write path: `GRAPH.QUERY` với MERGE/CREATE + parameters (`$param`).
4. So parity với `falkordb_driver.py` trên graph `stock` (đã có dữ liệu thật 2714 nodes):
   - 10 queries đọc đa dạng (keyword search used by `_graph_keyword_search`, fulltext,
     id lookup, `db.labels()`).
   - 1 write MERGE → verify bằng Python client đọc lại.
5. Đo latency 200 queries Rust vs Python (tham chiếu: ladybug spike ~0.186 vs ~0.191 ms).

## Gate

- [x] Read parity: cùng query → cùng records (property-by-property) trên graph thật.
- [x] Write: Rust MERGE → Python thấy được (và ngược lại).
- [x] Latency Rust ≤ Python (hoặc ghi nhận số liệu và quyết).
- [x] **Decision record**: go (tiếp Phase 03) / no-go (graph path giữ Python + PyO3 bridge —
  khi đó Phase 03+ được thay thế bằng "PyO3 expose writer" scope nhỏ hơn nhiều).

**Kết quả 2026-09-13 — VERDICT: GO.** Crate `rust/crates/cortex-falkordb`; harness
`scripts/rust_parity/falkordb_spike_parity.py` (fixture `fixtures/falkordb_spike_stock.json`).
Đọc parity 11/11 query trên `stock` thật (2714 nodes, keyword search + procedures + edge +
path + scalar battery, exact property-by-property); write 2 chiều MATCH, graph sạch sau
cleanup; latency Rust 1.848 ms/query vs Python 1.981 (Rust nhanh ~7%). Lưu ý giao thức:
lệnh read thật là `GRAPH.RO_QUERY` + `--compact` (tên `GRAPH.ROQUERY` trong scope trên
không tồn tại trên server); server là Redis 8.6.3 query engine — legacy fulltext procedure
trả result set rỗng (không lỗi) và Rust tái hiện đúng; `DB.PROPERTYKEYS` yield
`propertyKey` (không phải `key`). Chi tiết: `reports/phase01-decision.md`.
