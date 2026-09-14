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

## Re-evaluation 2026-09-14 — sau khi rust-full-migration P01–P14 code-complete

Bối cảnh: plan `260913-2130-rust-full-migration` đã port xong đúng các khoảng
trống mà decision record trên liệt kê. Replay harness được nâng cấp để kiểm
chứng lại full-surface, không còn strip gì:

**Đã làm (thay đổi code):**
1. Bỏ strip `operation` payload trong `replay_journal.rs` +
   `replay_journal_python.py` — manifest staging giờ chạy thật cả 2 side.
2. Wire các op P02 còn thiếu vào replay: `claim_reconciling`,
   `claim_reconciling_job`, `schedule_reconciliation_retry`,
   `seal_endpoint_audit` (trước đây rơi vào unsupported/skip).
3. `journal.rs`: thêm `canonicalize_iso` (parse ISO → microsecond UTC chuẩn
   `_iso` của Python, qua `days_from_civil`/`iso_from_epoch`) —
   `schedule_reconciliation_retry` canonical hoá `retry_at` fail-closed,
   sửa drift format `12:00:00+00:00` vs `12:00:00.000000+00:00`.
4. Fix bin theo signature P02: `mark_reconciling` nhận `error_code` optional.
5. Bench generator mang operation payload thật (shape từ capture).

**Bằng chứng parity full-surface (không strip):**
- Fixture P02 (45 ops — gồm mark_reconciling/schedule_reconciliation_retry/
  claim_reconciling/seal_endpoint_audit/conservation_summary): replay 0
  unsupported → **10 tables, 57 rows khớp 100% strict-time**.
- Capture executor-driven 30 ops với operation thật (72 manifest entries):
  **10 tables, 58 rows khớp 100% strict-time** — node_manifest/
  edge_manifest/producer_completion đều so được và khớp.
- Bench lại với staging thật: Rust 1.3–1.5x (thấp hơn lần đo operation rỗng)
  — xem `reports/bench-journal.md`.

**Điều kiện tiên quyết flip (theo danh sách 5 điều kiện ở trên):**
1. Manifest staging + conservation gate — ✅ P02 port + parity 100% qua harness này.
2. Node-first endpoint audit + close-run — ✅ `seal_endpoint_audit`,
   `endpoint_audit_status`, `conservation_summary`,
   `recover_run_leases_as_ambiguous` replay parity (fixture 45 ops).
3. Reader/reconciliation path — ✅ về core API (`claim_reconciling*`,
   `schedule_reconciliation_retry` parity qua fixture); ⚠️ consumer-side
   capture (drain stream) vẫn chưa có — recovery loop thực chiến chưa được
   capture toàn diện.
4. Dogfood full-ingest ≥ 1 tuần qua shadow — ⬜ còn mở, thuộc vận hành.
5. Bench lại — ✅ làm (1.3–1.5x, I/O-bound — hiệu năng không phải lập luận flip).

**Quyết định cập nhật: SẴN SÀNG FLIP về mặt kỹ thuật journal-core; flip
thực hành NHẬP VÀO P14B cutover của rust-full-migration** (đang ở gate vận
hành dogfood 1 tuần) thay vì plan riêng: `journal-shadow-diff` xanh hằng
ngày trong cửa sổ dogfood của P14B là gate đủ; không mở plan mới. Việc còn
lợi ích biên duy nhất ngoài P14B: xây consumer/drain-side capture nếu muốn
parity cả recovery loop thực chiến — không chặn flip (write path là đường
chính, drain vẫn có thể giữ Python hybrid theo điều kiện 3).

Gates re-eval: `make rust-check` exit 0, pytest 161 passed, `make rust-pyo3`
pass, fixture + capture diff 100% strict-time như trên.
