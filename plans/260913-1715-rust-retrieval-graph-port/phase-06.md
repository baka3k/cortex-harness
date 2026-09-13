# Phase 06: Driver LadybugDB Rust (`lbug`) spike + quyết định biên tích hợp

**phaseBlockedBy: 260913-1538-ladybug-graph-provider** (✅ đã implement phases 01–06)

**Trạng thái: DONE 2026-09-13 — roundtrip 2 chiều PASS, decision record bên dưới.**

## Kết quả spike

- Crate [`lbug` 0.20.4](https://crates.io/crates/lbug) (pin khớp PyPI `ladybug` 0.20.4 có trong venv) build OK, không cần cấu hình đặc biệt.
- **Python → Rust:** Rust mở store file do Python `ladybug.Database` tạo, đọc được node Python ghi (`rust_reads_python_store`).
- **Rust → Python:** Rust ghi node vào store Python tạo; `ladybug` Python đọc lại thấy đủ 3 nodes (`check_ladybug_roundtrip.py` → roundtrip OK).
- **Benchmark smoke (500 point-reads MATCH/ORDER, store 3 nodes):** Rust `lbug` ~0.186 ms/read ≈ Python `ladybug` ~0.191 ms/read. Chi phí nằm ở engine, overhead Python ở lớp DB-call không đáng kể — đúng như phân tích hi-predict (I/O-bound).
- Fixture + scripts: `gen_ladybug_store.py`, `check_ladybug_roundtrip.py`, `tests/cross_language.rs`.

## Dialect findings (ladybug 0.20.4, verify trên CẢ Rust `lbug` và Python `ladybug`)

1. **`IF NOT EXISTS` chỉ hợp lệ ở vị trí sau tên bảng** (`CREATE NODE TABLE IF NOT EXISTS <name> (...)` — cú pháp kuzu). Đặt ở cuối statement bị parser từ chối (`Invalid input <IF>`). **Hai engine hành xử nhất quán** (verify trực tiếp cả 2). Python `LadybugDriver` (`ladybug_driver.py:512`) dùng đúng vị trí sau tên bảng → side Python không cần thay đổi.
2. Store 0.20.4 là **FILE** (`<graph>.lbug`), không phải directory như kuzu cũ — khớp contract "one store file per named graph" trong driver docstring.
3. `QueryResult` Python không có `is_success()` — execute raise `RuntimeError` trực tiếp (khác kuzu Python cũ).
4. **Lock finding (real-world test 2026-09-13):** `db.close()` của ladybug Python **không nhả OS flock** — process khác vẫn không mở được store; chỉ nhả sau khi object bị GC (`del db, conn` + `gc.collect()`). Reopen trong cùng process có thể thành công nhờ handle nội bộ → dễ tưởng đã nhả lock. Rust side phải chờ Python process thật sự release (xem `scripts/rust_parity/realworld_stock_test.py`).

## Decision record: biên tích hợp Rust ↔ Python

| Phương án | Đánh giá spike |
|---|---|
| **1. Native query path** (Rust binary mở store qua `lbug`) | ✅ Chứng minh khả thi: cùng engine, cùng store format, roundtrip 2 chiều pass. Điều kiện: tôn trọng single-writer (ladybug OS file lock + `StorageLease` như Python side đang làm). |
| **2. PyO3** (Python MCP embed extension Rust) | Khả thi kỹ thuật nhưng thêm 1 biên build (maturin/abi3) mà chưa có nhu cầu đo được; defer đến khi hot path Python thực sự cần Rust nội bộ. |
| **3. Subprocess/IPC** | Phức tạp hơn PyO3 với ít lợi hơn cho use case hiện tại — loại. |

**Quyết định: Phương án 1 (native) cho retrieval/query binary Rust; PyO3 để dành cho analyzer hot-path nếu profiling sau này chỉ ra cần.**

Rationale:
- Roundtrip chứng minh store format tương thích 2 chiều cùng version pin → không cần migration giữa Python writer và Rust reader.
- Benchmark cho thấy không có lợi thế hiệu năng ở lớp DB-call → giá trị Rust nằm ở retrieval brain (đã port phase 02–04) + single-binary distribution, không nằm ở "thay Python gọi DB".
- Không thêm biên FFI mới khi chưa có nhu cầu đo được (đúng nguyên tắc hi-predict CAUTION).

## Gates

- [x] Cross-language store read/write smoke pass cả 2 chiều (cùng version pin 0.20.4).
- [x] Decision record cho biên tích hợp, kèm benchmark số liệu (0.186 vs 0.191 ms/read — tương đương).
- [x] Lệch dialect phát hiện được ghi lại (3 findings; finding #1 đã verify cả 2 engine + side Python không cần đổi).

## Việc còn treo cho Python side

- Không có blocker. Benchmark smoke chỉ là chỉ định (store nhỏ); benchmark thật theo SLO của ladybug plan khi có store lớn.
