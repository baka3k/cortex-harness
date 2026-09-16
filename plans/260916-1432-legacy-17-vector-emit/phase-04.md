# Phase 04 — DEFERRED (rev2 — 2026-09-16 15:10)

## Trạng thái

**DEFERRED** cho đến khi fix `260916-HHMM-legacy17-graphless-emit` (out of plan scope).

## Lý do architectural (verified)

Orchestrator's embedding pass sets `CORTEX_DISABLE_GRAPH=1`. Mỗi analyzer
main checks `graph_writes_disabled()` early. Trong graphless mode:
- `open_store()` returns `None`
- `if let Some(writer) = writer { ... }` block bị SKIP hoàn toàn
- analyzer không parse files, không accumulate data
- `--embedding-input-output` được pass nhưng emission code nằm trong block đã skip
- orchestrator logs `[upsert] exec:` → analyzer exit early → artifact missing

Để phase-04 PASS, mỗi analyzer cần refactor main flow:
```rust
// Always parse, only conditionally write to graph
loop { parse → buf_*.push() }
if let Some(writer) = writer { flush_write_buffers(...); }  // graph optional
if args.embedding_input_output().is_some() { emit artifact từ buf_* }
```

## Công việc cần làm (next plan)

`260916-HHMM-legacy17-graphless-emit`:
- 11 analyzer crates main refactor (move artifact emission OUTSIDE writer block)
- 4 carve-outs (csharp/cobol/android/plsql) wire luôn
- shell + framework overlays confirm
- Re-run drill 3 legs của plan này

## Trong plan này (đã done)

- Phase-01/02/03: phase-03 widening + 11 analyzer primary-pass emission wired
- Direct analyzer invocation verified (cplus 1517 docs, sql 17 docs emitted)
- 85 cortex-sync tests PASS

## Tại sao KHÔNG phải bug của plan này

Plan chỉ thêm `maybe_emit_embedding_artifact` calls BÊN TRONG `if let Some(writer)` blocks
(mirror pattern của shared-7 analyzers). Pre-existing architecture: legacy 17
parsers coi "graph store = None" nghĩa là "skip toàn bộ". Plan không thay đổi
control flow — chỉ thêm emission calls trong scope đã có.

Refactor cần thiết để phase-04 hoàn thành nằm ngoài phạm vi plan này.

## Original phase-04 spec (rev0 — preserved cho next-plan reference)

### Mục tiêu
1. Flip `CORTEX_LEGACY_VECTOR_EMIT` default ON
2. Dogfood 1 kỳ ở stock repo (hoặc waiver)
3. Rollback drill 3 legs PASS
4. Cross-update `260915-2230-python-legacy-cleanup` mở khóa xoá Python analyzer scripts
5. Update runbook `docs/cutover-runbook.md`

### Drill 3 legs (rev0)
- Leg 1: `dev sync code --full-scan` → vectors_upserted > 0
- Leg 2: `dev sync code` (incremental) → short-circuit skip child
- Leg 3: `CORTEX_LEGACY_VECTOR_EMIT=python dev sync code` → vector_status="disabled-no-emitter"