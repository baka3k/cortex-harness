# Phase-05 — Flip + dogfood + rollback drill — rev2

Mục tiêu: native default mọi provider; delegation chỉ qua `CORTEX_SYNC_BACKEND=python`; mirror
phase-08 convention (docs/cutover-runbook.md §3-4) **đủ convergence criteria (M5)**.

## Changes

1. **Flip commit (1 commit, revert-able)**: mọi provider native; `delegate_to_python` chỉ fire khi
   hatch flag. Commit message runbook convention.
2. **Runbook** (docs/cutover-runbook.md — chỉ sau khi vector-lane edits committed, M3a): bảng flags
   thêm `CORTEX_SYNC_BACKEND`; §0c "Sync-plane lane"; §4 rollback 2 thời điểm (trước flip = flag, sau
   flip = revert); lane policy journal (phase-03.2); embedded-falkordb end-state theo Q2.
3. **Dogfood** (operator = user):
   - User chạy ≥1 kỳ `dev sync code` thật trên repo (config ladybug instance `cortex` — workflow hiện
     có, upsert idempotent).
   - Verify qua artifacts: summary mới nhất `backend == "rust-native"` + healthy (L2 — không đoán từ
     component field), `.lbug` mtime tăng, `dev journalx status` sạch.
   - **Không thay được bằng scratch-only**: gate DELETE (phase-06) yêu cầu dogfood sign-off umbrella
     §2 (7 ngày stock) hoặc **waiver user ghi rõ** vào `reports/phase05-drill.md` (H3 — rev1 cho
     scratch-fallback là lỗ hổng so precedent phase-08).
4. **Rollback drill có convergence (M5)**:
   - Dump `.lbug` trước drill (`graph-state dump`).
   - Leg 1: `CORTEX_SYNC_BACKEND=python` → sync scratch PASS, `backend == "python"`.
   - Leg 2: unset → native sync PASS, `backend == "rust-native"`.
   - Convergence: `graph-state diff` dump-sau-drill vs dump-trước = 0 sau 2 sync cycles native; summary
     health + journal conservation assert. Ghi `reports/phase05-drill.md` (format mirror
     260915-analyzer-layer-rust-cutover/reports/phase08-rollback-drill.md).

## Gates

- Flip commit revert-able (test trong drill: revert → build → hatch-style chạy lại).
- Drill: cả 2 leg PASS + convergence diff = 0.
- Dogfood ≥1 kỳ thật hoặc waiver ghi rõ (chốt tình trạng gate DELETE cho phase-06).
- Runbook đủ flag + rollback cả 2 thời điểm.
