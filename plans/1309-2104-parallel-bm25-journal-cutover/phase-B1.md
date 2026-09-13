# Phase B1: Capture harness — ghi op-stream ra JSONL khi ingest

**status: planned** · **track: B (ingest path)** · **độc lập với Track A**

## Mục tiêu

Trong ingest Python thật, ghi ra JSONL toàn bộ op-stream mà journal
executor nhận — dữ liệu đầu vào cho Rust replay ở B2. **Không đổi behavior
ingest**, capture env-gated, mặc định OFF.

## Thiết kế

1. **Điểm capture**: biên `code-tiny/tools/graph/journal/executor.py` /
   `sqlite_store.py` — hook nơi op được ghi xuống DB (đọc 2 file, chọn 1
   điểm duy nhất đi qua mọi write path; nếu có nhiều entry point thì hook
   ở lớp `executor` vì mọi write đi qua nó).
2. **Format**: JSONL — mỗi dòng 1 op, shape khớp fixture của
   `scripts/rust_parity/gen_journal_scenario.py` (đã là format Rust test
   đọc được). Mỗi dòng thêm `"_captured_at"`, `"_seq"` phục vụ debug;
   Rust side bỏ qua key có prefix `_`.
3. **Flag**: env `CORTEX_JOURNAL_SHADOW=<dir>` — khi set, append
   `<dir>/<journal-name>.jsonl`; unset → overhead 1 `os.environ.get`
   per write (không đo được).
4. **Tính đúng của stream**: header line đầu file ghi
   `schema_version` + journal config (đọc từ `config.py`) để replay dựng
   đúng store.

## Gates

- [ ] Unit test: bật flag → file JSONL sinh ra, mỗi op 1 dòng, parse được
      bằng loader của `gen_journal_scenario.py`.
- [ ] Unit test: OFF → không file, không đổi hành vi executor.
- [ ] Capture thật: ingest 1 project nhỏ (dogfood) ra JSONL hoàn chỉnh.
- [ ] `PYTHONPATH=code-tiny pytest code-tiny/tests -q` xanh.

**Trạng thái:** planned
