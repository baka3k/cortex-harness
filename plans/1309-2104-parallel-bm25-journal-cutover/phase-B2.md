# Phase B2: Rust replay CLI + diff store Python↔Rust

**status: done** · **track: B (ingest path)** · **depends: B1**

## Mục tiêu

Replay op-stream capture được qua journal write core Rust (phase-05/07 đã
port, `cortex-graph-core/src/journal.rs`) thành shadow store, rồi diff với
store Python ghi thật — bằng chứng parity cấp DB-state, không phải chỉ
unit fixture.

## Thiết kế

1. **Replay CLI**: bin mới `rust/crates/cortex-graph-driver/src/bin/replay_journal.rs`
   (native — đúng decision record phase-06, không FFI). Input: file JSONL
   B1 + đường dẫn output SQLite. Dựng store qua `cortex_graph_core::journal`,
   bỏ qua key prefix `_`, verify header `schema_version` khớp crate.
2. **Diff tool**: `scripts/rust_parity/diff_journal_stores.py` — mở 2
   SQLite, so: schema objects, row count per table, checksum mỗi row
   (sort deterministic theo PK). Exit 1 khi lệch, in diff đầu tiên.
3. **Make target**: `make journal-shadow-diff PYTHON_STORE=… RUST_STORE=…`
   chạy replay + diff 1 lệnh.
4. **Fixture regression**: replay `journal_scenario.json` committed (đã
   22-op pass ở phase-07) phải vẫn xanh — chống format drift.

## Triển khai thực tế

- **Bin `replay_journal`** (native, `unsafe_code = "deny"`): args `--input`,
  `--output`, `--artifact-root` (mặc định `<output>.artifacts`), `--bench`
  (cho B3), `--skip-unsupported`. Verify `schema_version` header ==
  `JOURNAL_SCHEMA_VERSION` crate; clock được điều khiển bởi
  `_captured_at_epoch` từng op (fallback `now_epoch` header); op lỗi trong
  capture → verify Rust lỗi **đúng code** đã ghi (vd. `stale_fence`,
  `invalid_transition`); `fencing_token` cho op phụ thuộc lấy từ store đang
  replay (track theo job_id) vì token là random-per-claim của từng store;
  `claim_job` (producer path) map sang `claim_batch(run)` kèm cảnh báo nếu
  claim lệch job.
- **Fixture JSONL**: `gen_journal_scenario.py` giờ ghi `args` vào từng op
  của `journal_scenario.json` (Rust golden test serde bỏ qua field lạ —
  `journal_scenario_golden` vẫn xanh) và emit thêm
  `rust/crates/cortex-graph-core/tests/fixtures/journal_scenario.jsonl`
  (capture format B1, clock cố định) + `--emit-dir` copy store Python
  FIXED-clock. Python-replay side mirror:
  `scripts/rust_parity/replay_journal_python.py`.
- **Diff tool chuẩn hoá 2 nhóm cột biến thiên** (không thể giống giữa 2
  process độc lập): `fencing_token` (random per claim; NULL vẫn là NULL) và
  cột `*_at`/`*_until` (wall-clock). `--strict-time` tắt chuẩn hoá timestamp
  — dùng cho fixture/capture có clock điều khiển được (replay dùng epoch
  capture được ⇒ timestamp khớp tuyệt đối cả 2 side).
- **Giới hạn đã ghi nhận (không giấu):** Rust `enqueue_batch` chưa port
  manifest staging (phase-07) nên replay **strip payload `operation`** —
  CẢ HAI replay side (Python + Rust) làm như nhau, diff đối chiếu
  Rust-store vs **Python-replay**-store (cùng JSONL, cùng ràng buộc). Store
  Python live lúc drive (có manifest staging đầy đủ: `operation_json`,
  `node_manifest`, `edge_manifest`) được giữ làm tham chiếu — đây là khoảng
  trống port lớn nhất, ghi trong decision record B3.

## Gates

- [x] `cargo test --workspace` + `cargo clippy -D warnings` xanh (bin mới
      nằm trong workspace gate) — `make rust-check` pass.
- [x] Replay fixture committed → diff với store Python scenario = khớp:
      `make journal-shadow-diff INPUT=fixtures/journal_scenario.jsonl
      PYTHON_STORE=<gen --emit-dir> RUST_STORE=… STRICT_TIME=1` →
      **schema v3, 10 tables, 18 rows khớp 100% (strict-time)**.
- [x] Replay JSONL capture thật (executor-driven, phase-B1) → diff khớp
      100% row: `drive_journal_ingest.py --check` → 30 ops replay 2 engine →
      **10 tables, 38 rows khớp 100% (strict-time)**.
- [x] `make rust-pyo3` vẫn xanh (không đụng retrieval crates nhưng khoá
      regression) — "PyO3 parity: tất cả pass".

**Trạng thái:** DONE 2026-09-13 — bin `replay_journal` + `diff_journal_stores.py`
+ make target `journal-shadow-diff`; regression fixture 18/18 rows và capture
thực 38/38 rows khớp 100% strict-time; hạn chế manifest staging chưa port
được ghi rõ ở trên và trong phase-B3.
