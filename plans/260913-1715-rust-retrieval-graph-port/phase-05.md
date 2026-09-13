# Phase 05: Graph core port — journal + schema manifest

**phaseBlockedBy: 260913-1538-ladybug-graph-provider** (✅ ladybug đã implement phases 01–06)

**Trạng thái: DONE (increment đầu) 2026-09-13.** Write path đầy đủ của journal
(leases/barriers/fencing/manifest staging) vẫn là Python source of truth — đã
port: schema manifest toàn phần + journal DDL v3 + `inspect_journal` read path.

## Context

Bắt đầu port `tools/graph/` (18.3k LOC) từ phần độc lập DB-engine:
- `journal/sqlite_store.py` (3132 LOC) → `rusqlite` (mapping 1:1 SQLite).
- `journal/{runtime,artifacts,consumer,config,operation}.py` (~2.4k LOC).
- `schema/manifest.py` (329 LOC) — **phải align với `CODE_GRAPH_SCHEMA` mà ladybug driver bootstrap** (output của ladybug phase 04).

## Requirements

- Crate `rust/crates/cortex-graph-core/`; journal dùng `rusqlite` bundled.
- Golden test journal: tạo DB bằng Python (`uv run` không cần extra dep — sqlite3 stdlib), Rust mở đọc ghi idempotent; và ngược lại.
- Writer/operations port theo đợt sau khi journal + manifest ổn (sub-phase nội bộ, tách file phase riêng nếu cần).

## Gates

- [x] Cross-language SQLite: Python tạo → Rust đọc (`inspect_python_created_journal`); Rust tạo → Python đọc (`check_journal_roundtrip.py` — roundtrip OK, 2 runs khớp toàn bộ field).
- [x] DDL byte-for-byte khớp Python (`ddl_matches_python` — cả enum interpolation).
- [x] Schema manifest khớp: fingerprint `8613fc08894a26c2` (canonical JSON + sha256[:16]), driver_indexes, has_identity checks.
- [x] `cargo test` (11 binary ok) + `cargo clippy --all-targets -- -D warnings` sạch.

## Files

- `rust/crates/cortex-graph-core/src/schema_manifest.rs` — port toàn phần `manifest.py`.
- `rust/crates/cortex-graph-core/src/journal.rs` — enums v3, DDL, `create_schema`, `inspect_journal`.
- `scripts/rust_parity/gen_phase05_fixtures.py` — fixtures + `journal_python.sqlite` (tạo bằng SQLiteJournal thật).
- `scripts/rust_parity/check_journal_roundtrip.py` — roundtrip 2 chiều.
- `code-tiny/tests/test_journal_golden_notes.md` — (không tạo; golden nằm phía Rust).

## Lưu ý khi port

1. `RunMetadata` cần đủ 10 field bắt buộc trong `metadata_json` (inspect chỉ đọc `parser`) — fixture phải đầy đủ.
2. `ensure_safe_local_directory` từ chối path traverse symlink (macOS /tmp) — generator dùng `.resolve()`.
3. Python dict insertion order không có trong BTreeMap — thứ tự insert rows phải tường minh (runs → artifacts → batches → events) để thỏa FK.
4. `journal_bytes` (size file + wal + shm) phụ thuộc engine tạo file → chỉ so trong chiều Python→Rust; chiều Rust→Python skip.
5. `freshness`/`age` dùng `now` truyền vào (Rust) thay vì `datetime.now()` nội tâm (Python) — fixture monkeypatch clock để deterministic.
