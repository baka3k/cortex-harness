# Benchmark journal write: Rust replay vs Python replay (Track B3)

**Ngày:** 2026-09-13 · **Track:** B (journal Rust shadow) · **plan:** 1309-2104-parallel-bm25-journal-cutover

## Setup

- **Op-stream:** capture format Track B (JSONL, header + op/chu kỳ). Mỗi chu kỳ
  producer 4 op đúng như loop thật: `create_artifact` → `enqueue_batch` →
  `claim_batch` → `ack_batch`; `job_id`/artifact sha256 tính bằng identity
  function thật (`deterministic_job_id`, `canonical_json`) nên stream replay
  được 100% trên cả hai engine. 100/1k/10k ops sinh bằng cùng generator
  (`scripts/rust_parity/bench_journal.py`, op shapes giống capture ingest thật
  ở `drive_journal_ingest.py`, id riêng từng batch).
- **Rust:** `replay_journal` (release, `cargo build --release`), journal core
  rusqlite — cùng pragmas với Python (`WAL`, `synchronous=FULL`).
- **Python:** `replay_journal_python.py` qua `SQLiteJournal` (implementation
  tham chiếu).
- **Đo:** 3 lần/size, lấy **median elapsed**; ops/s và MB-ghi/s tính trên
  byte của file JSONL input. Cùng 1 máy, cùng SSD, 2026-09-13.

## Kết quả

| ops | input bytes | Rust ops/s | Python ops/s | Rust MB/s | Python MB/s | Rust faster |
|---:|---:|---:|---:|---:|---:|---:|
| 100 | 38,439 | 7,143 | 4,545 | 2.62 | 1.67 | 1.6x |
| 1,000 | 384,619 | 7,042 | 5,155 | 2.58 | 1.89 | 1.4x |
| 10,000 | 3,893,704 | 6,258 | 4,297 | 2.32 | 1.60 | 1.5x |

Đọc thô (1 trong 3 lần, size 10k): Rust `elapsed_s≈1.60`, Python `elapsed_s≈2.33`.

## Re-evaluation 2026-09-14 — bench VỚI manifest staging thật

Sau khi rust-full-migration P02 port manifest staging (`journal_manifest`),
bench generator được sửa để mang operation payload thật (shape từ capture
executor-driven; trước đây `operation: {}` nên staging không chạy — che mất
phần chi phí). Generator chỉ sinh node batches (files/functions) vì
relationship/calls staging đòi endpoint identity thật giữa các node đã stage
— row tổng hợp không có → staging rejected đúng contract admission gate.

| ops | input bytes | Rust ops/s | Python ops/s | Rust MB/s | Python MB/s | Rust faster |
|---:|---:|---:|---:|---:|---:|---:|
| 100 | 45,638 | 5,000 | 3,704 | 2.18 | 1.61 | 1.3x |
| 1,000 | 456,579 | 5,952 | 4,016 | 2.59 | 1.75 | 1.5x |
| 10,000 | 4,613,079 | 4,065 | 3,109 | 1.79 | 1.37 | 1.3x |

**Kết luận:** với staging thật, lợi thế Rust thậm chí HƠI THẤP HƠN lần đo
operation rỗng (1.3–1.5x vs 1.4–1.6x) — xác nhận chi phí trội là fsync
(`synchronous=FULL`) + artifact I/O của CẢ HAI side; hiệu năng không bao giờ
là lập luận flip. Parity DB-state full-surface (kể cả bảng manifest) đã được
chứng minh ở phase-B3 addendum: fixture 45 ops → 57 rows, capture thật 30 ops
→ 58 rows, khớp 100% strict-time.

## Phân tích

- Rust nhanh hơn **~1.4–1.6x** — có lợi thế nhưng KHÔNG phải bậc độ lớn.
  Đúng như lock finding phase-06 (ladybug DB-call) và nhận định hi-predict:
  chi phí trội nằm ở **fsync của `synchronous=FULL` + artifact file write**
  (mỗi artifact 1 file + fsync directory), phần đó thuộc I/O hệ điều hành mà
  cả hai engine đều phải trả. Phần CPU (parse JSONL, canonical identity,
  SQL plan) là phần Rust thắng.
- Throughput ổn định ~6–7k ops/s (Rust) trên stream ~380 byte/op — dư sức
  shadow replay sau ingest; chưa đủ làm lập luận "flip vì hiệu năng".
- Overhead capture B1 (env-gated, JSONL append) không đo được ở bảng này vì
  bench chạy replay, không chạy capture — và theo thiết kế capture mặc định OFF.

## Điều kiện để bench thêm giá trị

1. Sau khi port manifest staging sang Rust (đang là khoảng trống lớn nhất,
   xem decision record phase-B3), đo lại trên stream có manifest stage —
   phần đó là CPU-bound và lợi thế Rust dự kiến lớn hơn.
2. Benchmark Native-binary ingest thật (không qua JSONL) — JSONL parse là
   chi phí chỉ có ở shadow phase.

## Tái chạy

```bash
cargo build --release -p cortex-graph-driver --bin replay_journal --manifest-path rust/Cargo.toml
.venv/bin/python scripts/rust_parity/bench_journal.py --emit-dir /tmp/journal-bench
```
