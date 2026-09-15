# Phase 04 — Wave A: Wire `dev sync` → `cortex-sync`/`cortex-doc` (khoản cut-maximal lớn nhất)

Red-team #1/#2/#5: `cmds/sync.rs` đang spawn Python trực tiếp từ binary — không phase nào
trong rev 1 đụng tới. Crate `cortex-sync` (~6.6k LoC) đã parity-PASS từ umbrella phase-09
và consume `CORTEX_RUST_ANALYZER` (`cortex-sync/src/registry.rs:269-279`); umbrella
phase-10.md:41 defer swap — phase này là nơi trả nợ đó.

## Scope

1. **`dev sync code` / `sync all`** (`sync.rs:918,939-941,1060,1095-1097`): thay spawn
   `python code-tiny/tools/sync/incremental_sync.py …` bằng invoke binary/API `cortex-sync`.
   Xoá bảng `LANG_ANALYZERS` chỉ tới `.py` (`sync.rs:31-60`) — analyzer selection là việc của
   `cortex-sync` registry.
2. **`dev sync doc`** (`sync.rs:1317-1321`): thay spawn
   `python doc-tiny/graphrag_ingest_langextract.py` bằng `cortex-doc` (umbrella phase-14
   Scope A đã port doc-tiny ingest — verify gate tại chỗ, fallback: giữ spawn + ghi forced).
3. **Journal consumer recovery** (`sync.rs:411-415`): thay `python -m tools.graph.journal.consumer`
   bằng journal core Rust (cortex-graph-driver — đã có replay/journal APIs).
4. **Torch device probe** (`sync.rs:1597-1600`): dời probe ra `cortex-sync` (nơi thật sự dùng
   device), thêm cache kết quả + skip khi `EMBED_DEVICE` set sẵn. Đây là Python **forced**
   (chỉ torch biết MPS/CUDA) — được whitelist trong gate tổng, mọi `venv_python` khác của
   cortex-dev phải về 0.
5. Giữ nguyên CLI surface/flags của `dev sync` (byte-compatible).

## Parity (D2 + shadow)

- Mở rộng parity harness: 1 sync run end-to-end Python orchestrator vs cortex-sync binary trên
  stock — diff graph node/rel counts + journal events (tận dụng `make journal-shadow-diff`).
- `dev sync code stop` đã cover ở phase-02.

## Gate

- [ ] `grep -n "venv_python" rust/crates/cortex-dev/src/cmds/sync.rs` → chỉ còn 0 (torch probe
      đã dời vào cortex-sync); `grep -n "incremental_sync\|graphrag_ingest\|journal.consumer" sync.rs` → 0.
- [ ] `dev sync code` end-to-end trên stock: graph counts + journal events khớp Python reference.
- [ ] `dev sync doc` end-to-end qua cortex-doc (hoặc fallback documented nếu gate verify fail).
- [ ] `CORTEX_RUST_ANALYZER=python` vẫn chạy được qua cortex-sync registry (rollback analyzer path).
- [ ] `cargo test -p cortex-dev -p cortex-sync` pass; parity harness mở rộng pass.

**Trạng thái:** draft (rev 2 — phase mới) — **DONE 2026-09-15** (reports/phase-04.md)
