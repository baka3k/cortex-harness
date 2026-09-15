# Phase 02 — Re-baseline embedder ONNX + flip default — BÁO CÁO

Ngày: 2026-09-15. Nhánh: `feat/change-db`. Plan: `plans/260915-2027-vector-lane-rust-port`.

## Kết quả

| Gate | Kết quả | Số liệu |
|---|---|---|
| Comparator mind với `CORTEX_EMBED_BACKEND=onnx` | **PASS** | 36/36 (initialize 1, tools/list 5, tools/call 30) |
| Rollback flag `CORTEX_EMBED_BACKEND=python` | **PASS** | 36/36 |
| Default mới (unset env, debug binary đã rebuild) | **PASS** | 36/36 |
| `cargo test -p cortex-embed` | **PASS** | 27 lib passed (golden `#[ignore]` giữ nguyên) |
| `cargo build -p cortex-mcp` sau flip | **PASS** | debug, 14.3s |

## Thay đổi

1. `rust/crates/cortex-embed/src/backend.rs` — `Backend::from_env()`: unset/unknown →
   **`Onnx`** (trước: `Python`); `python` giữ vai trò rollback flag. Docstring cập nhật.
2. `scripts/rust_mcp/mind_contract.py` — `TOLERANCE`: `1e-9` → **`1e-6`** với comment
   dẫn nguồn drift (spike phase-01/02).
3. `scripts/rust_mcp/compare_mind.py` — **fix bug harness tiềm ẩn**: tolerance trước đây
   quyết theo key của path cấp đầu (case id) nên **không bao giờ** áp dụng xuống leaf
   (`…passages[0].score`). Viết lại `tolerant_deep_diff`: post-process mọi diff line
   dạng `<path>: <a!r> != <e!r>`, áp tolerance theo leaf key
   (`_leaf_key` bỏ `[index]`). Trước fix: onnx run fail 14 case với drift 2.6e-7 dù
   tolerance đã nâng; sau fix: 0 fail.
4. `docs/cutover-runbook.md` — bảng env `CORTEX_EMBED_BACKEND`: default `onnx`,
   `python` = rollback; block "Trạng thái" cập nhật 2 lý do cũ đã xử lý (tolerance
   re-baseline + TTL cache fix latency từ phase02b).

## Decision: re-baseline = tolerance, không regenerate fixture

Golden fixtures (`mind_fixtures.json`, mode `live-python-mind-server`) giữ nguyên —
chúng là hợp đồng **hành vi** Python (cấu trúc response + hit set + thứ tự). Drift
1e-7 giữa ort và torch là đặc tính đã đo; đóng băng bằng tolerance tuyệt đối 1e-6 ở
comparator là đủ và tránh double-maintenance fixture. Đây là lệch nhỏ so với mô tả
ban đầu trong `phase-02.md` ("regenerate fixture") — ghi nhận tại đây.

## Lưu ý vận hành

- Sau flip, máy chạy binary Rust **cần ONNX artifacts** (`.cache/embed/`, `make
  embed-artifacts`) hoặc đặt `CORTEX_EMBED_BACKEND=python`. Thiếu artifact → lỗi tường
  minh lúc query embedding (fail-closed, không fallback im lặng).
- Ingest embedding (sync) vẫn Python — decision NO-GO của spike phase-05 không đổi.
- **Quan sát ngoài phạm vi**: `compare_graph` sau flip = 36 pass / 2 fail
  (`search_functions.fanout`, android parser — property drift kiểu `money.js` vs
  `mybatis-config.xml` trong graph store share `redis://127.0.0.1:6379`). Chạy lại với
  `CORTEX_EMBED_BACKEND=python` → **fail y hệt** → không liên quan flip; là drift dữ liệu
  graph share so với fixture phase-11 đã ghi (pre-existing, cần re-record fixture hoặc
  restore store — track ngoài plan này).

## Bước kế tiếp

Phase-01 (golden capture vector-lane với embedder ON cho unified + mind) — harness
`capture_vector_golden.py` / `compare_vector.py` / `vector_contract.py`.
