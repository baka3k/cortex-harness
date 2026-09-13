# Phase B3: Benchmark + decision record — hướng flip Rust writer

**status: planned** · **track: B (ingest path)** · **depends: B2**

## Mục tiêu

Số liệu + quyết định có căn cứ cho việc flip Rust thành primary journal
writer (thuộc plan native-binary sau — KHÔNG flip trong plan này).

## Việc cần làm

1. **Benchmark**: mở rộng `replay_journal` với `--bench` — ops/s và
   MB-ghi/s trên 3 size op-stream (100 / 1k / 10k ops, sinh từ capture
   thật + fixture phóng to). So cùng Python executor đo trên op-stream
   giống hệt. Ghi số liệu vào file report `reports/bench-journal.md`
   trong plan dir.
2. **Decision record** (cập nhật phase file này): flip hay không, điều
   kiện tiên quyết (vd. native ingest binary cần gì nữa: writer, reader,
   driver trọn vẹn), rủi ro còn mở (lock finding phase-06: `db.close()`
   Python không nhả flock đến GC — Rust writer phải chờ release thật).
3. **Nếu flip khả thi**: draft plan kế tiếp (native ingest binary) — chỉ
   outline, không triển khai trong plan này.

## Gates

- [ ] Bảng benchmark Python vs Rust ≥ 3 size, cùng op-stream byte-for-byte.
- [ ] Decision record viết xong trong phase file (flip / defer + lý do).
- [ ] Nếu flip: draft plan outline commit kèm.

**Trạng thái:** planned
