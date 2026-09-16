# Red-team rev1 — native-vector-ingest-local plan (2026-09-16)

Lens: failure-modes. Reviewer: delegated worker (spawn), verified claims against source.
Verdict: 9 findings — 1 Critical, 4 High, 4 Medium. **Tất cả đã hấp thụ vào rev2** của
plan.md + phase-01/02/03/05.

| # | Severity | Finding (tóm tắt) | Absorption (rev2) |
|---|---|---|---|
| 1 | Critical | Hatch `=python` hứa "delegate python children" nhưng đường này không tồn tại (pre-flip local = zero-writes, F1); drill leg python không thể converge theo cách viết | D5 rewritten: `=python` = FROZEN (zero-writes) + sidecar đọc pickle legacy; drill 3 legs (A frozen, B refusal, C convergence); phase-02 message + phase-05 scope sửa khớp |
| 2 | High | Phase-02 trả `Local` ngay → flip-without-escape 3 phases trước khi có hatch/parity | Phase-02: `Local` chỉ khi `CORTEX_VECTOR_BACKEND=rust` opt-in; unset giữ `Unsupported`; gate byte-identical khi flag-off |
| 3 | High | Reader LOCK_SH lâu dài vs writer `LOCK_EX\|LOCK_NB` → sync fail khi MCP chạy; `write_atomic` đã torn-proof | D4 rewritten: reader `open_readonly` KHÔNG lock, revalidate mtime; writer giữ LOCK_EX\|LOCK_NB nguyên trạng; phase-01 scope 5 + concurrent test |
| 4 | High | "Flush coalesce" scoped là status quo (mỗi upsert đã flush 1 lần); O(n²) thật nằm giữa các ops; gate flush-counter tự lừa | Phase-01 scope 3: persist-mode bulk (`persist=false` + 1 flush cuối); gate = benchmark wall-time + bytes assertion; phase-02 map `upsert_wait` → bulk+flush |
| 5 | High | Quarantine ambiguity (`collection/` hay cả root?) + rollback sau quarantine → sidecar init pickle rỗng phục empty | Phase-05: quarantine đúng subtree `<root>/collection` → `.bak`, giữ JSON store; rollback guard open-time (`cortex-local-store.json` present + `collection/` absent → loud refusal); drill leg B |
| 6 | Medium | Phase-03 gate cần golden matrix nhưng tooling chỉ dựng ở phase-04 | Twin-capture mini-harness chuyển lên phase-03 (fixture nhỏ); phase-04 mở rộng full matrix |
| 7 | Medium | Phase-03 treo quyết định vào "audit phase-01" mà phase-01 không scope | Phase-01 scope 6: shape-audit `version` field + QdrantLocal `QDRANT_HNSW_EF`, output trong reports/phase01-engine.md |
| 8 | Medium | RSS chỉ có trong prose risk, phase-03 gate không đo | Phase-03 gate: ΔRSS < 300MB assertion trên store 132MB |
| 9 | Medium | Legacy detect `collection/*/storage.sqlite` khác compound key của research; false-negative → serving empty im lặng | Phase-02 scope 4: compound key (`collection/` OR root `storage.sqlite`) AND JSON absent → lỗi trung thực trong `open` (cả writer + reader); probe layout single-file cũ |

## Coverage (reviewer verified & cleared)

- must_not/has_id delete gap là thật và được gate đúng thứ tự (chưa có production caller
  của LocalQdrantStore ngoài factory/parity tests).
- Store durability: `write_atomic` (util.rs:297-312) — không có cửa sổ truncated-store.
- Facts F1-F10 của plan khớp source.
- `CORTEX_VECTOR_BACKEND` là tên flag mới, không collision.
- Multi-instance isolation: store roots + lease per-instance-path; same-instance sync
  fail-fast giữ nguyên.
- Reader filter (`project_scope_filter`) chỉ dùng `must`/`match.any` — nằm trong
  capability hiện tại, reader flip không bị chặn bởi filter gap.
- Mind/doc lane disjoint với mọi phase target.
