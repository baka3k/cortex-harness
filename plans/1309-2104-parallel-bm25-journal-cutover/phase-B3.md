# Phase B3: Benchmark + decision record — hướng flip Rust writer

**status: done** · **track: B (ingest path)** · **depends: B2**

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

## Benchmark (chi tiết đủ trong `reports/bench-journal.md`)

`--bench` trên `replay_journal` + mirror Python
`scripts/rust_parity/replay_journal_python.py --bench`; generator
`scripts/rust_parity/bench_journal.py` sinh stream capture-format với
job_id/artifact sha tính bằng identity function thật (replay được 100%).
3 lần/size, median:

| ops | Rust ops/s | Python ops/s | Rust faster |
|---:|---:|---:|---:|
| 100 | 7,143 | 4,545 | 1.6x |
| 1,000 | 7,042 | 5,155 | 1.4x |
| 10,000 | 6,258 | 4,297 | 1.5x |

MB-ghi/s: Rust 2.32–2.62 vs Python 1.60–1.89. Chi phí trội là fsync
(`synchronous=FULL`) + artifact file write — I/O hệ điều hành; phần CPU
Rust thắng nhưng không đủ tạo bậc độ lớn.

## Decision record: DEFER flip Rust thành primary journal writer

**Quyết định: DEFER — Rust shadow replay đạt parity DB-state 100% (B2) nhưng
chưa đủ điều kiện thành primary writer; flip chờ plan native-ingest-binary
sau khi đóng các khoảng trống port bên dưới.**

Rationale:
- **Parity đã chứng minh ở tầng journal core** (fixture 22-op + capture
  executor-driven 30-op, diff 100% strict-time) — nhưng trên đúng phạm vi
  phase-05/07 đã port. Hiệu năng chỉ hơn 1.4–1.6x (I/O-bound), không phải
  lập luận đủ mạnh để chịu rủi ro cutover.
- **Khoảng trống port lớn nhất: manifest staging** — Python `enqueue_batch`
  stage `node_manifest`/`edge_manifest`/`edge_endpoint` (row conservation,
  conflict/rejected admission, producer completion gate). Rust hiện từ chối
  non-empty `operation`; replay phải strip payload cả 2 side. Primary writer
  bắt buộc phải có staging này (nó là admission gate của ingest).
- **Node-first path chưa port**: `seal_endpoint_audit`, `endpoint_audit_status`,
  `close_run_production`, `conservation_summary`, `recover_run_leases_as_ambiguous`
  — cần cho parser cplus (`language-writer-node-first-v1`).
- **Consumer/reconcile layer** (`consumer.py`, `reconcile.py`,
  `schedule_reconciliation_retry`, `claim_reconciling_job`) chưa có tương
  đương Rust — recovery story của primary writer chưa trọn.

Điều kiện tiên quyết trước khi flip (draft outline plan kế tiếp —
"native ingest binary"):
1. Port manifest staging + conservation gate sang Rust (golden fixture từ
   `_manifest_candidates` + `_stage_manifests_locked`).
2. Port node-first endpoint audit + close-run production.
3. Reader path (consumer/drain + reconciliation) hoặc cam kết hybrid
   (Rust write, Python drain) với hợp đồng fence rõ ràng.
4. Dogfood full-ingest ≥ 1 project thật qua shadow ≥ 1 tuần không lệch
   (`journal-shadow-diff` xanh mỗi ngày).
5. Bench lại sau (1)+(3): staging là CPU-bound, kỳ vọng lợi thế Rust lớn hơn
   1.4x đo hiện tại.

Rủi ro còn mở:
- **Locking journal ≠ locking ladybug (phase-06 finding):** journal là
  SQLite — lock là POSIX advisory lock của SQLite, nhả **ngay khi connection
  đóng**, không dính hiện tượng "flock giữ đến GC" của ladybug `db.close()`.
  Nhưng connection Python giữ mở trong suốt run (`GraphWriteJournalRuntime`)
  + `busy_timeout` 5s → một Rust writer phải đi qua cùng `SQLiteJournal`
  contract (single-writer per run, WAL multi-reader) và tôn trọng
  `min_free_bytes`/size admission; cross-process write đồng thời vào 1 run
  vẫn phải tránh ở tầng orchestrator (như hiện trạng `StorageLease`).
- **Format drift**: `journal_scenario.jsonl` + diff tool đã khoá schema
  (user_version 3 + checksum row); mọi đổi Python store schema phải regenerate
  fixture + replay lại (make target có sẵn).
- **Capture tool hiện scope producer-side** (runtime); consumer-side op-stream
  (drain) chưa được capture — cần nếu muốn parity cả recovery loop.

## Gates

- [x] Bảng benchmark Python vs Rust ≥ 3 size, cùng op-stream byte-for-byte
      (100/1k/10k, generator deterministic, median 3 lần) —
      `reports/bench-journal.md`.
- [x] Decision record viết xong trong phase file (DEFER + lý do + điều kiện
      tiên quyết + rủi ro).
- [x] Flip bị defer nên "draft plan outline" nằm gọn trong decision record
      trên (5 điều kiện tiên quyết); không commit plan riêng — flip không
      khả thi trong hiện trạng.

**Trạng thái:** DONE 2026-09-13 — bench Rust ~1.4–1.6x nhanh hơn Python
(7.1k/7.0k/6.3k ops/s vs 4.5k/5.2k/4.3k ops/s ở 100/1k/10k ops); decision
record DEFER flip; `make rust-check` + pytest (161 passed) + `make rust-pyo3`
đều xanh.
