# Phase 07 (mở rộng sau phê duyệt "thực hiện toàn bộ"): Journal write core + PyO3

**Trạng thái: DONE 2026-09-13.**

## 1. Journal write core (low-level path) — Rust

Port của producer/consumer loop trong `sqlite_store.py`:

- `identity.rs` — canonical_json / sha256_hex / run_fingerprint / run_id / deterministic_job_id.
- `models.rs` — toàn bộ enums v3 + RunMetadata/BatchSpec/ArtifactRef/RunRecord/BatchRecord/BarrierRecord/JournalLimits + validation.
- `artifacts.rs` — ArtifactStore: write_jsonl (content-addressed + chmod 0400), verify (size + sha256), read_jsonl, path_for. Bỏ filesystem-type probing (documented).
- `journal.rs` mở rộng — `Journal::open` (pragmas/migrate/validate/recover/verify-artifacts như `__init__`) + open_run (quarantine-incompatible actives, resume events) + get/list/find_resumable + create_artifact + enqueue_batch (artifact ownership, idempotent job_id, admission, refcount, barrier production, events) + barriers + claim_batch (json_each required-barriers gate) + renew/ack + mark_reconciling/schedule_retry/block_batch + complete_producers + recover_expired_leases.

**Chưa port (Python giữ source of truth, Rust từ chối rõ ràng):** manifest staging cho spec có `operation` khác rỗng (node/edge manifests, conservation summary, endpoint audit, claim_reconciling*). Kịch bản low-level (`operation: {}`) parity 1:1.

## 2. Scenario-replay golden

- Python ghi kịch bản 22 ops bằng `SQLiteJournal` thật (clock cố định): open_run/resume/parser-khác/find_resumable/list/create_artifact/barrier open-close/enqueue+idempotent/claim/renew/ack×2/claim-rỗng/complete_producers/enqueue-sau-complete (lỗi)/stale-fence (lỗi)/inspect.
- Rust replay cùng chuỗi qua `Journal`, so từng kết quả (mask `fencing_token` vì random mỗi bên; mask `journal_bytes` trong inspect vì file size phụ thuộc engine).
- **Pass toàn bộ 22 ops** — chứng minh parity thật của canonical_json (run_id/job_id/artifact.sha256 khớp từng byte), state machine transitions, và durability pragmas.

## 3. PyO3 bindings — `cortex-retrieval-py`

- Expose: `classify_query`, `classify_query_explain`, `get_weight_profile`, `bm25_score` (stateless build+score, hỗ trợ `text_field`/`id_field`), `query_understanding`.
- Build + kiểm chứng: `bash scripts/rust_parity/build_pyo3.sh` → sinh `scripts/rust_parity/cortex_retrieval_py.so` và chạy `test_pyo3_parity.py` — **pass toàn bộ** (7 queries intent + explain, 5 weight profiles, 5 bm25 queries vs `rank_bm25`, 3 query-understanding dicts so exact).
- Python MCP servers giờ gọi retrieval brain Rust trực tiếp (không subprocess) — bước đầu của decision record phase 06 (PyO3).

## Gates

- [x] `cargo test` toàn workspace: 16 binary ok.
- [x] `cargo clippy --all-targets -- -D warnings` sạch (0 warning).
- [x] Scenario replay 22 ops khớp Python.
- [x] PyO3 parity pass; `.so` build reproducible qua script.
