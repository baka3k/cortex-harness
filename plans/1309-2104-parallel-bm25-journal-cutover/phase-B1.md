# Phase B1: Capture harness — ghi op-stream ra JSONL khi ingest

**status: done** · **track: B (ingest path)** · **độc lập với Track A**

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

## Quyết định thiết kế (đã verify trên code thật)

- **Điểm capture duy nhất: `runtime.py::GraphWriteJournalRuntime.__init__`**
  — đọc code thấy `executor.py` là trusted Cypher *compiler* thuần, không
  chạm journal DB; mọi producer-side journal write của ingest đi qua
  `self.journal` của runtime. Runtime bọc `SQLiteJournal` bằng
  `ShadowCaptureJournal` (module mới `shadow.py`, proxy `__getattr__` ghi
  JSONL rồi delegate kết quả/exception thật). Consumer/drain path
  (`consumer.py`, self-contained `SQLiteJournal`) và guard path trong
  `config.py` không nằm trong capture — đó là recovery/finalize, không phải
  op-stream ingest.
- **Schema_version đọc từ `models.JOURNAL_SCHEMA_VERSION` (= 3)** — không
  hardcode; header: `{"_header": true, "schema_version": 3,
  "journal_config": {mode, path, metadata, limits, lease_seconds, ...}}`.
- **Tên file**: `<dir>/<stem(capture path)>.jsonl` (vd. `python.sqlite3` →
  `python.jsonl`); append semantics, `_seq` đánh số liên tục xuyên các runtime
  (khởi tạo từ số dòng op có sẵn), header chỉ ghi khi file mới.
- **Args serialize** theo đúng tên param `SQLiteJournal` (bảng
  `_PARAM_NAMES`); `retry_at` kèm `retry_at_epoch` (float) để Rust replay
  không phải parse ISO; result serialize khớp `batch_to_dict`/`run_to_dict`/
  `barrier_to_dict` của `gen_journal_scenario.py`. Key debug prefix `_`:
  `_seq`, `_captured_at`, `_captured_at_epoch`.
- **Overhead khi OFF**: 1 `os.environ.get` **mỗi lần khởi tạo runtime**
  (rẻ hơn "per write" như plan đề, cùng semantics vì capture là thuộc tính
  của process run).

## Capture thật (thay cho ingest dogfood nặng)

Capture là **executor-driven**, không phải full-ingest project thật:
`scripts/rust_parity/drive_journal_ingest.py` đẩy 1 run gồm op type thật của
`operation.py` — create-node (`files`, `functions`), update-node (batch
`files` thứ hai), edge ops (`relations:HAS_FILE`, `calls`, `calls:site`) —
qua `GraphWriteJournalRuntime` với capture bật → JSONL 30 ops + SQLite store
Python. Lý do: full ingest cần driver graph + infra sống; journal-level
op-stream là thứ Track B cần đối chiếu.

## Gates

- [x] Unit test: bật flag → file JSONL sinh ra, mỗi op 1 dòng, parse được
      bằng loader của `gen_journal_scenario.py` (`shadow.load_capture`;
      shape op/args/result khớp fixture loader; `_seq` liên tục; header có
      `schema_version` từ `models.JOURNAL_SCHEMA_VERSION`).
- [x] Unit test: OFF → không file, không đổi hành vi executor
      (journal không bị bọc, prepare/acknowledge chạy bình thường; env rỗng
      cũng coi như OFF).
- [x] Capture thật: executor-driven 1 run ingest mô phỏng (create/update
      node + edge ops) → `capture/python_store.jsonl` 30 ops hoàn chỉnh.
- [x] `PYTHONPATH=code-tiny pytest code-tiny/tests -q` xanh — 161 passed,
      45 subtests (gồm 3 test mới `test_journal_shadow_capture.py`).

**Trạng thái:** DONE 2026-09-13 — capture env-gated OFF mặc định; unit test
3/3 pass; suite 161 passed; capture executor-driven 30 ops (bằng chứng diff
B2: 38 rows khớp 100% strict-time).
