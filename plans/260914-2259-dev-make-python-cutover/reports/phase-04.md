# Phase 04 report — Wire `dev sync` → `cortex-sync` (khoản cut-maximal lớn nhất)

**Ngày:** 2026-09-15 • **Branch:** `feat/change-db`

## Đã làm

1. **`sync code` / `sync code all` → binary `cortex-sync`** (`cmds/sync.rs`):
   - `cortex_sync_binary()`: `CORTEX_SYNC_BIN` → `rust/target/release` → `debug`; thiếu
     binary = error + hint `cargo build --release -p cortex-sync` (không fallback python).
   - cmd head `[python, incremental_sync.py, ...]` → `[cortex-sync, --python-bin, <venv>,
     --root, ...]`; mọi flag khác giữ nguyên (CLI của cortex-sync là port 1:1 của
     `incremental_sync.parse_args`). `--python-bin` giữ để phục vụ python-plane
     delegation seam + rollback `CORTEX_RUST_ANALYZER=python`.
   - **Xoá bảng `LANG_ANALYZERS`/`FRAMEWORK_ANALYZERS` .py** — banner `sync code all`
     dùng list tên ngôn ngữ registry-backed (`SYNC_CODE_ALL_LANGS`, 38 mục như cũ).
2. **`sync doc` → `cmds/docsync.rs` (fallback forced theo clause của plan)**:
   `cortex-doc` ingest chưa thay được python ingestor — **không có embedded-FalkorDB
   backend** (chỉ nhận `--falkordb-uri`) và **đã loại vector stage** (không có
   `--qdrant-path/--embedding-model`). Toàn bộ doc orchestration dời nguyên khối sang
   module riêng, spawn `graphrag_ingest_langextract.py` giữ nguyên.
3. **Journal consumer recovery → `journalx::recover_required_lane`** (forced):
   consumer live (replay artifact batches vào graph + ack) chưa có bản Rust —
   `cortex-sync` tự delegate required-lane về Python (`DELEGATE_SENTINEL ... is
   Python-plane`, orchestrator.rs:1121-1124); replay_journal.rs chỉ là capture-bench
   tool. Spawn `python -m tools.graph.journal.consumer` giữ nguyên behavior, tách khỏi
   sync.rs. Fix kèm theo: pre-step trước đây dùng `cmd[0]` làm python — sai khi cmd[0]
   là binary.
4. **Fix parser gap (pre-existing, bắt được nhờ e2e)**: group option `--project-dir`
   không thấy ở subcommand dispatch (`sync code all/stop/add`, `sync doc all/stop/add`)
   — dev.py đọc `ctx.parent.params`. Thêm `Matches::merged(parent, child)` +
   `merged_with_group()` trong dispatch cho 6 path đó.
5. **Torch probe**: giữ ở `env.rs` (đã centralise + cache từ phase-01) — cortex-sync
   nhận `EMBED_DEVICE` đã resolve; không cần probe riêng phía cortex-sync.

## Gate kết quả (phase-04.md)

- [x] `grep venv_python sync.rs` → **0**; `grep "incremental_sync\|graphrag_ingest\|
      journal.consumer" sync.rs` → chỉ còn 1 literal `incremental_sync_summaries`
      (tên thư mục cache chung với child, dùng cho `--summary-path` discovery —
      không phải spawn; ghi nhận như whitelist).
- [x] **e2e parity trên fixture ladybug isolate**: `dev sync code all` (rust →
      cortex-sync) và python orchestrator cho cùng kết quả: `SCAN_RESULT parser=python
      files=2 functions=3 classes=1`, graph writes `files=2 functions=3 calls=1
      projects=1 types=1 relations=5`, rồi cùng fail tại topology analyzer (bug
      ART-index ladybug **pre-existing của workstream change-db** trong code chưa commit
      — ngoài scope plan này).
- [x] Delegation seam verify: local-embedded (ladybug/falkordb-path) → cortex-sync in
      `[cortex-sync] python-plane delegation: ...` rồi chạy đủ pipeline qua python
      orchestrator do chính nó spawn (đã fix interpreter qua `--python-bin`). Remote
      backend (falkordb-uri/neo4j) chạy native 100% — đây là thiết kế umbrella phase-09.
- [x] `CORTEX_RUST_ANALYZER=python` rollback: registry cortex-sync giữ path python
      analyzer (`--python-bin` vẫn được truyền) — không đổi từ umbrella.
- [x] `cargo test -p cortex-dev` 23/23; parity harness 75/76 (chỉ delta `migrate`
      pre-existing).

## Forced-Python whitelist (cập nhật inventory plan)

| Python | Lý do còn lại |
|---|---|
| doc-tiny ingest spawn (`docsync.rs`) | cortex-doc thiếu embedded-falkordb + vector stage (fallback clause phase-04) |
| journal consumer recovery (`journalx.rs`) | consumer live chưa có bản Rust; cortex-sync required-lane là Python-plane (umbrella) |
| torch device probe (`env.rs`, cached) | chỉ torch biết MPS/CUDA (whitelist sẵn của plan) |
| `.harness/scripts/*` spawn (`harness.rs`) | không có bản Rust (whitelist sẵn) |

Ngoài các mục trên, `dev sync code/all` không còn spawn python nào từ tầng dev.py
bridge — orchestration đi qua seam `cortex-sync` binary.

## Kết luận

**PASS (với 2 forced có documented)** — sync code/all wire xong; doc + journal-consumer
giữ Python có lý do ghi rõ; parser group-option gap đã đóng.
