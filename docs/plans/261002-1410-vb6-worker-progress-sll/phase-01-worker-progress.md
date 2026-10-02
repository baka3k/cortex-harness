# Phase 01 — Per-file progress + timing (worker + adapter)

> Gate ra: M1 (worker stderr per-file), M2 (meta fields), M3 (adapter relay + summary)

## Tasks

### 1.1 Worker subclass `WorkerRunner` (mới, package `io.cortex.vb6.worker`)

- Tạo `WorkerRunner extends VbParserRunnerImpl`:
  - constructor nhận `totalFiles` (int) để progress có mẫu số.
  - override `protected void parseFile(File vbFile, Program program,
    VbParserParams params)`:
    - bọc `super.parseFile(...)` bằng `System.nanoTime()`;
    - tăng counter, in stderr:
      `[vb6][worker] parsed <i>/<N> file=<fileName> ms=<ms>` (file name có thể
      chứa khoảng trắng — `ms` neo cuối làm anchor cho parser adapter, H1);
    - lưu timing vào `Map<String,Long>` keyed theo absolute path (public getter
      cho Vb6Worker đọc sau batch).
  - Vb6Worker thay `new VbParserRunnerImpl()` (:285) bằng `new WorkerRunner(entries.size())`;
    batch + retry call đều đi qua instance này (retry: timing lần cuối thắng — R4).
- Vb6Worker sau batch: đọc timing map →
  - `worker_meta.parse_ms_total`, `worker_meta.parse_slowest_file`,
    `worker_meta.parse_slowest_ms` (slot :387-398);
  - `entry.parseMs` → `filePayload` điền `parse_meta.worker_elapsed_ms` (thay
    hardcoded 0 tại :649).
- Ghi chú R1: prefix `[vb6][worker]` là filter key — KHÔNG in gì khác không
  prefix lên stderr.

### 1.2 Adapter relay + summary (`vb6_antlr_adapter.py`)

- Thay `subprocess.run` (:292-299) bằng `Popen`:
  - thread riêng drain stdout vào buffer (R2 — bắt buộc tránh deadlock);
  - loop chính đọc stderr theo dòng: nếu `verbose` → in live prefix giữ nguyên,
    flush=True; luôn thu thập vào list;
  - deadline tổng = `effective_timeout_sec`: quá hạn → `proc.kill()`, raise
    `subprocess.TimeoutExpired` (giữ nguyên hợp đồng lỗi cũ).
- Sau khi rc==0 và JSON parse OK:
  - lọc dòng khớp
    `^\[vb6\]\[worker\] parsed (?:\d+/\d+ )?file=(.*) ms=(\d+)( sll_fallback)?$`
    (chấp nhận có/không prefix `i/N`, file name chứa khoảng trắng, `ms` neo
    cuối — red-team H1);
  - in 1 dòng (luôn luôn, không verbose-gate):
    `[vb6][engine] parse: <N> files in <total>s; slowest: <file> (<ms>ms)`
  - verbose: thêm top-5 file chậm nhất.
- Dòng `[vb6][worker]` KHÔNG lọt vào stdout (protocol giữ nguyên).

### 1.3 Tests + build + pins

- Test mới `tests/test_vb6_worker_progress.py`:
  - chạy jar trực tiếp trên fixture nhỏ qua manifest temp, capture stderr:
    assert ≥ N dòng đúng format + `ms >= 0`;
  - assert `worker_meta.parse_ms_total` > 0 và `parse_slowest_file` non-empty;
  - assert `parse_meta.worker_elapsed_ms` > 0 cho ≥ 1 payload.
- Adapter: unit test relay bằng fake process (không cần java):
  - stderr nhiều dòng + JSON stdout → summary đúng, verbose relay không crash;
  - timeout path → `TimeoutExpired` + process đã kill.
- Chạy: `pytest tests/test_vb6_antlr_worker_contract.py tests/test_vb6_worker_progress.py -q`
  (contract cũ phải pass nguyên vẹn — stderr trước giờ bị discard nên zero
  breakage).
- `mvn -q -f code-tiny/tools/vb/antlr_worker/pom.xml -DskipTests package`.

## Định nghĩa xong

- [ ] `WorkerRunner` override `parseFile`, timing map + stderr per-file
- [ ] `worker_meta`: parse_ms_total / parse_slowest_file / parse_slowest_ms
- [ ] `parse_meta.worker_elapsed_ms` = giá trị thật (không còn 0 cứng)
- [ ] Adapter Popen + live relay (verbose) + summary slowest (mặc định)
- [ ] Test mới pass; contract test cũ pass; full `-k vb6` pass
- [ ] Jar rebuilt; không đổi stdout protocol
