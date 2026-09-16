# Phase 01 — Interface + verify (Pattern A on simplest analyzer)

## Mục tiêu

Establish the hoisting pattern (Pattern A) trên 1 analyzer đơn giản nhất
(`analyzer-delphi` — đã build `WriteAllPayload` trong main, chỉ thiếu hoist
emission outside writer block). Verify pattern works end-to-end trước khi
apply cho 7 analyzer Pattern A khác (java/sql/kotlin/vb/js/ts/php).

## Scope

### Patch 1 — Hoist Pattern A (analyzer-delphi)

Trong `analyzer-delphi/src/danalyzer.rs`:
- `run_build_call_graph()` function hiện emission nằm ở line ~734 (cuối writer block)
- Move emission statement block to OUTSIDE `if let Some(...) = writer { ... }` (sau
  flush_write_buffers cuối cùng)
- Populate `files_selected` từ `selected_paths` (đã có sẵn trong scope); `files_deleted`
  từ `deleted_set` (cũng có sẵn) — KHÔNG dùng `Vec::new()` placeholder như phase-02 của
  plan trước
- Bind `let payload = WriteAllPayload { ... };` TRƯỚC writer block (đã có sẵn ở line ~701)
- Bind `let embedding_categories = payload.embedding_categories();` cùng scope
- Move emission `if let Some(output) = args.embedding_input_output()` check ra ngoài
  `if let Some(writer) = writer { ... }` block

### Patch 2 — Unit test cho analyzer-delphi graphless mode

Trong `analyzer-delphi/src/danalyzer.rs` hoặc `analyzer-delphi/tests/`:
- Test 1: graphless mode (`CORTEX_DISABLE_GRAPH=1`) + `--embedding-input-output <tmp.json>`
  → exit code 0 + artifact exists at `<tmp.json>` + parseable + non-empty categories
- Test 2: graphless mode KHÔNG có `--embedding-input-output` → exit code 0 (no crash)
- Test 3: graph-enabled mode (default) + `--embedding-input-output` → exit code 0
  + artifact exists (regression check)

### Patch 3 — Per-analyzer integration smoke

Live run trên procsample (chỉ delphi facts có trong project):
```
$ CORTEX_LEGACY_VECTOR_EMIT=rust /Users/.../dev.sh sync code \
    --project-dir /Users/.../procsample --full-scan --verbose 2>&1 | grep -E "delphi"
```

Expect: `[embedding] delphi: artifact written path=/tmp/.../delphi_embedding_input_*.json`

## Deliverables

- Code changes:
  - `analyzer-delphi/src/danalyzer.rs` (~20-30 LOC hoist + populate files_*) 
- Tests:
  - 1 unit test file với 3 test cases PASS
- Reports:
  - `reports/phase01-delphi-hoist.md` — diff + before/after SCAN_RESULT

## Gate

- [ ] analyzer-delphi builds xanh (`cargo build --release -p analyzer-delphi`)
- [ ] 3 unit tests PASS
- [ ] Live run trên procsample: delphi analyzer emit artifact trong graphless mode
- [ ] No regression in primary pass (graph-enabled mode)
- [ ] Pattern A verified reproducible — comment trong code ghi rõ "Pattern A reference impl"

## Rollback

- `git revert` phase-01 commit → delphi falls back to `disabled-no-emitter` (graceful)
- Capability gate (`EMITTING_VECTOR_CLI_PARSERS`) vẫn giữ delphi trong list → orchestrator
  tiếp tục expect emission nhưng artifact missing → orchestrator logs `native pass failed`
  (status quo)

## Next phase

Phase 02 apply same Pattern A cho java/sql/kotlin/vb (chỉ khác scope vars khác nhau) + 
Pattern B cho cplus (refactor lớn hơn) + Pattern B cho dart (surface tuple).