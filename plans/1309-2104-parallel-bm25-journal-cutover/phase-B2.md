# Phase B2: Rust replay CLI + diff store Python↔Rust

**status: planned** · **track: B (ingest path)** · **depends: B1**

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

## Gates

- [ ] `cargo test --workspace` + `cargo clippy -D warnings` xanh (bin mới
      nằm trong workspace gate).
- [ ] Replay fixture committed → diff với store Python scenario = khớp.
- [ ] Replay JSONL capture thật (từ B1 dogfood) → diff khớp 100% row.
- [ ] `make rust-pyo3` vẫn xanh (không đụng retrieval crates nhưng khoá
      regression).

**Trạng thái:** planned
