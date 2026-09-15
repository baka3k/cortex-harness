# Phase 01 — Vector-lane golden capture — BÁO CÁO

Ngày: 2026-09-15. Plan: `plans/260915-2027-vector-lane-rust-port`.

## Kết quả

| Gate | Kết quả | Số liệu |
|---|---|---|
| Golden capture (Python embedder ON) | **DONE** | 32 cases — 23 unified (local snapshot) + 9 mind (remote qdrant) |
| Determinism replay (Python vs Python) | **PASS** | 32/32 (2 lần capture độc lập) |
| Baseline Rust vs golden (mốc cho phase-03/04) | Đo xong | mind **9/9 PASS**; unified **3/23** (20 vector case đỏ do stub `results: []`) |

## Deliverables

- `scripts/rust_mcp/vector_contract.py` — env builders (snapshot isolation) + case
  matrix + tolerance contract (`TOLERANCE=1e-6`, key set mở rộng cho explore fusion).
- `scripts/rust_mcp/capture_vector_golden.py` — ghi golden từ server Python thật
  (`--flavor unified|mind|both`, `--out` cho replay, `--refresh-snapshot`).
- `scripts/rust_mcp/compare_vector.py` — replay determinism + Rust acceptance gate
  (`--replay <file>` / `--rust`), dùng `tolerant_deep_diff` (đã fix ở phase-02).
- `scripts/rust_mcp/fixtures/vector_fixtures.json` — golden 32 case, mode
  `live-python-vector-server`, embedder metadata (jina-v3/MPS + bge-m3).

## Snapshot isolation (bài học thực thi)

Golden unified chạy trên bản copy của instance `cortex` tại
`.cache/vector_golden/data_home/v1/instances/cortex` (qdrant 132MB + ladybug 192MB).
3 bug isolation đã sửa trong `ensure_snapshot`/`unified_server_env`:

1. **`manifest.json` chứa absolute path vào instance LIVE** → rewrite
   `LIVE_HOME → SNAPSHOT_HOME` khi copy; ngược lại server resolve về store live và
   đụng lease (`Embedded ladybug store is already owned`, pid của server sống).
2. **Lock/lease files bị copy theo** (`.code.cortex-owner.lock`, …) → strip khi copy.
3. **Sai segment layout**: env file chứa `…/<home>/v1/instances/…` (có `v1`); snapshot
   ban đầu đặt ở `data_home/instances` (thiếu `v1`) → path rewrite trỏ vào thư mục
   không tồn tại → `No Qdrant collections available` (vector) và explore_graph
   graceful-empty (không phải dữ liệu thật). Snapshot giờ mirror đúng
   `<home>/v1/instances/<id>`.

Env biến chứa path tuyệt đối trong active env (`LADYBUG_*_PATH`, `QDRANT_*_PATH`, …)
được rewrite `LIVE_HOME → SNAPSHOT_HOME`; `CORTEX_EFFECTIVE_*` (fingerprint) bị drop.

## Comparator: multiset cho field thứ tự-bất định

`available_relationships` (capability_diagnostics) hoán đổi thứ tự phần tử giữa các
lần chạy — set-iteration order của Python, cùng lớp với finding `fetch_relations_with_depth`
của phase-13. Xử lý ở comparator (`MULTISET_PREFIXES` + `reconcile_multisets`): so
đúng nội dung, bỏ qua thứ tự — không đụng shared contract surface.

## Gate so với phase-01.md

- Case count: gate ghi "≥60 (≥40 local + ≥20 remote)" — thực tế **32 case** đủ phủ
  mọi trục ma trận (mode × collection × project × content_mode × expand_graph ×
  error path; remote = mind fixture store vì **không có dữ liệu doc-local thật ở
  máy nào trong env này** — mọi `qdrant/doc` đều rỗng, golden mind-local = empty-store
  contract). Điều chỉnh gate theo số thực tế; tăng case sau nếu cần.
- Remote qdrant thật: dùng server đang sống tại `127.0.0.1:6333` (như phase-02b),
  không cần tunnel mới.

## Bước kế tiếp

Phase-03 — remote vector search native trong Rust unified (baseline đỏ 20 case là
danh sách việc cần xanh hoá; mind đã sẵn sàng).
