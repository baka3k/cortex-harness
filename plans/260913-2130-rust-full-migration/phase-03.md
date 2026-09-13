# Phase 03: Graph writer + operations port (Wave W0 đóng)

## Scope

- `tools/graph/writer/language_writer.py` (3.0k): node/rel upsert pipelines, batch write
 Contract, query_contract (319) — replicate `SET n = map` rewrite rules và dialect notes
 của từng provider (falkordb/ladybug) đã ghi trong `ladybug_driver.py`.
- `tools/graph/writer/project_topology_writer.py` (523) + `tools/project_topology/` (3.3k).
- `tools/graph/operations/` (class_ops, infra_ops, ... ~1.7k).
- `tools/graph/schema/preflight.py` + bootstrap/auto-DDL (đối chiếu ladybug plan phase-04).
- Driver abstraction: trait `GraphStore` với 2 backend — `FalkorDbRemote` (Phase 01) và
  `Ladybug` (crate `lbug`); provider selection theo env như `GraphDriverFactory`.

## Parity (mẫu chuẩn cho mọi phase sau)

**Dual-write graph diff**: script chạy writer Python và writer Rust trên 2 graph riêng
(`stock_pw` / `stock_rw`) với cùng input rows (lấy từ sync thật của stock), sau đó dump
cả 2 graph (nodes + rels + properties) và so exact — khác biệt duy nhất cho phép:
`updated_at`/timestamp fields (khai báo trong mask list).

## Gate

- [ ] Dual-write diff = rỗng (ngoài mask) trên ≥2 bộ input thật (stock files subset + testdata).
- [ ] Auto-DDL: property lạ được ALTER đúng như Python (verify bằng introspection 2 graph).
- [ ] clippy sạch; benchmark write 1k nodes Rust vs Python (ghi số liệu, không đặt gate tuyệt đối).
