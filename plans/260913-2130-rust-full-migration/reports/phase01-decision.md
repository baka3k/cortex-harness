# Phase 01 — FalkorDB Rust client spike: decision record

Ngày: 2026-09-13 · Server: `127.0.0.1:6379` · Graph: `stock` (stock thật, 2714 nodes)

## Verdict: **GO**

## Phát hiện giao thức quan trọng

1. **`GRAPH.ROQUERY` không tồn tại** trên server — lệnh read thật của client Python là **`GRAPH.RO_QUERY <graph> <query> --compact`**; Rust client dùng đúng lệnh này. (Tên lệnh trong phase-01.md là sai sót nhỏ, ý định spike không đổi.)
2. Server này là **Redis 8.6.3 query engine** (module list không lộ FalkorDB module) — không hỗ trợ `db.idx.fulltext.queryNodes` legacy (trả result set rỗng, không lỗi); Rust tái hiện đúng hành vi này vì parity so ở tầng result, không giả định procedure tồn tại.
3. Wire contract compact (khớp falkordb-py `ResultSetScalarTypes`): value = `[type, payload]`; 1=NULL, 2=STRING, 3=INTEGER, 4=BOOLEAN (payload chuỗi "true"/"false"), 5=DOUBLE (payload chuỗi), 6=ARRAY, 7=EDGE `[id, rel_type_id, src, dest, props]`, 8=NODE `[id, [label_ids], [prop_triples]]`, 9=PATH, 10=MAP; property = `[prop_id, value_type, value]`.
4. Label/rel-type/property đi trên dây dạng **id số** — client giữ bảng tên qua `DB.LABELS` / `DB.RELATIONSHIPTYPES` / `db.propertyKeys` (yield `propertyKey`, không phải `key`) và refresh khi gặp id lạ. Lưu ý DB.PROPERTYKEYS không yield `key` như falkordb-py tưởng.
5. Params đi trong header query: ``CYPHER `k`=v `` (chuỗi quoted, None→null, bool→True/False theo str() Python) + cờ `--compact`.

## Gate 1 — Read parity (Python driver vs Rust client)

Kết quả: **11 pass / 0 fail** trên 11 query đọc đa dạng (keyword search, legacy fulltext, id lookup, db.labels(), db.relationshipTypes(), filter+ORDER BY, aggregate count/labels(), multi-hop CALLS, edge return, path return, scalar battery gồm bool/null/double/int/string/list/map).

| Query | Kết quả |
|---|---|
| keyword_search | PASS |
| legacy_fulltext_queryNodes | PASS |
| id_lookup | PASS |
| db_labels | PASS |
| db_relationship_types | PASS |
| label_filter_order_limit | PASS |
| count_by_label | PASS |
| multi_hop_calls | PASS |
| edge_return | PASS |
| path_return | PASS |
| scalar_battery | PASS |

So sánh ở tầng record đã normalize (`_normalize_falkordb_value`): property-by-property exact, kể cả `_graph_id`, `_label` (sorted-first label), `_type`/`_start_id`/`_end_id` của edge.

## Gate 2 — Write cross-verify

- Rust `GRAPH.QUERY` MERGE probe (params int/float/bool/list/string): properties_set = 6 → **Python đọc lại: KHỚP**.
- Python MERGE probe → **Rust đọc lại: KHỚP**.
- Cleanup: còn lại 0 probe node (graph stock sạch như trước spike).

## Gate 3 — Latency

| Bên | Runs | Thực thi | Avg ms/query | Total ms |
|---|---|---|---|---|
| Rust (redis-rs) | 200 | 2200 | 1.848 | 4065.224 |
| Python (falkordb-py) | 200 | 2200 | 1.981 | 4357.684 |

Latency đo cùng bộ query đọc qua cùng tunnel, cùng lúc. Gate là «Rust ≤ Python hoặc ghi nhận số liệu để quyết» — số liệu trên là đầu vào của quyết định.

## Hệ quả chương trình

- **GO** → Phase 03 dựng trait `GraphStore` với backend `FalkorDbRemote` (crate `cortex-falkordb` này) và `Ladybug` (crate `lbug` đã có).
- Crate `cortex-falkordb` giữ nguyên làm nền: parser compact đã có unit test (9 test) không cần server; parity harness `scripts/rust_parity/falkordb_spike_parity.py` chạy được trong CI khi tunnel khả dụng.

## Cách tái chạy

```bash
cd rust && cargo test -p cortex-falkordb && \
  cargo clippy -p cortex-falkordb --all-targets -- -D warnings && \
  cargo build --release -p cortex-falkordb --examples
cd .. && .venv/bin/python scripts/rust_parity/falkordb_spike_parity.py
```

Parity script tự dọn probe residual và **sinh lại fixture từ Python driver** trước Gate 1 (bảng tên schema của graph là append-only — fixture cũ sẽ lệch `db_labels` nếu schema thay đổi).

Fixture: `scripts/rust_parity/fixtures/falkordb_spike_stock.json`.

## Review note cho Phase 03 (perf)

`FalkorDbClient` hiện refresh toàn bộ bảng tên schema (3 procedure round-trips)
sau **mỗi** lệnh ghi — an toàn nhưng sẽ nhân chi phí khi writer Phase 03 phát
hàng nghìn MERGE/batch. Phase 03 cần: (a) refresh schema theo *yêu cầu* (chỉ khi
parse gặp id lạ — đã có cơ chế refresh-on-mismatch ở đường đọc), hoặc (b) cache
schema với TTL/đếm lệnh ghi. Ngoài ra `build_params_header` chưa từ chối key rỗng
chứa backtick như falkordb-py (`ValueError`) — cần thêm khi phơi ra API rộng hơn.
