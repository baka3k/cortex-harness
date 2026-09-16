# Phase-05 report — Flip + rollback drill + convergence (2026-09-16)

## Flip status

Native default ĐÃ effective qua 2 commit revert-able riêng biệt:

- `68fa6d6` — phase-02 wire (graph target native + polymorphic store + hatch)
- `63e583d` — phase-03 journal replay native (required lane không delegate)

Rollback = `git revert` (thứ tự mới→cũ). Đã verify revert path: `git revert --no-commit
68fa6d6 63e583d fc172ab` auto-merge sạch toàn bộ (không conflict) — restore nguyên trạng sau
check. Delegation sau flip CHỈ fire qua hatch `CORTEX_SYNC_BACKEND=python` hoặc lỗi cấu hình
fail-closed (không bao giờ là fallback mặc định).

## Rollback drill (scratch `sp5-drill`, instance `sp5-drill`, data home riêng, corpus
fastapi_django)

| Step | Kết quả |
|---|---|
| 0. Baseline native full-sync | exit 0, `backend=rust-native`, success/scanned |
| 0. Dump `.lbug` trước drill (`graph-state dump`) | OK (`/tmp/sp5-dump-before.json`) |
| 1. `CORTEX_SYNC_BACKEND=python` full-sync | exit 0; **delegation fires** (1 dòng); `backend="python"`; success/scanned; 0 component failures |
| 2. unset → native cycle 1 (no-change) | exit 0, `backend=rust-native` |
| 3. touch `main.py` + commit → native cycle 2 (incremental) | exit 0, `backend=rust-native` |
| 4. Dump sau drill → `graph-state diff` dump-before vs dump-after | **`OK: canonical equal` (exit 0)** — convergence criteria M5 ĐẠT |
| 5. Summary health + journal conservation | summary health PASS cả 2 leg (0 failures); scope scratch không có journal (conservation vacuous — replay-only scope, phase-03 gate đã assert conservation trên fixture) |

Format mirror `260915-analyzer-layer-rust-cutover/reports/phase08-rollback-drill.md`.

## ⚠️ Ops warning (drill đã bắt được — into runbook §flags)

Gọi `cortex-sync` TRỰC TIẾP (không qua `dev sync code`) mà không export
`CORTEX_STORAGE_INSTANCE` + `CORTEX_DATA_HOME` → `resolve_storage` rơi vào instance mặc định
của user (`default`). Lần đầu drill đã tạo nhầm store trong `~/.cortext-harness/v1/instances/
default/ladybug/` — đã xoá, khôi phục nguyên trạng (default chỉ có falkordb/qdrant như manifest).
Quy tắc: mọi invocation trực tiếp phải export 2 env trên (harness + drill đã làm đúng).

## Runbook (docs/cutover-runbook.md) — DEFERRED (M3a)

Working tree vẫn còn uncommitted edits của vector-lane trên runbook. Per M3a: phase-02/05
KHÔNG đụng runbook tới khi các edit đó commit. Nội dung runbook cần thêm (draft sẵn):

1. §flags: thêm `CORTEX_SYNC_BACKEND=python` — rollback hatch trước flip-retire; retire thành
   retired-error ở phase-06.
2. §0c "Sync-plane lane": native default mọi provider; embedded falkordb fail-closed (2 lựa chọn
   + rebuild caveat); required lane = native replay (legacy-drain-only).
3. §4 rollback 2 thời điểm: trước phase-06 = hatch flag; sau phase-06 = `git revert` + rebuild.
4. Cảnh báo ops: direct cortex-sync invocation phải export instance/data-home env.

## Dogfood — CHỜ USER (gate DELETE phase-06)

Phase-06 KHÔNG chạy khi thiếu một trong hai:

1. **Dogfood sign-off umbrella §2**: user chạy `dev sync code` thật trên repo (config ladybug
   instance `cortex`) — verify `summary.backend == "rust-native"` + healthy, `.lbug` mtime tăng,
   `dev journalx status` sạch. Umbrella muốn 7 ngày stock.
2. **Hoặc waiver user ghi rõ** vào report này.

Trạng thái dogfood tooling: đã sẵn sàng — lần sync thật kế tiếp của user sẽ stamp
`summary.backend` (rust-native/python) vào summary artifact để verify không cần đoán (L2).

---

## DOGFOOD WAIVER (2026-09-16)

User ra lệnh trực tiếp "chạy phase 6 đi" sau khi được trình bày đầy đủ 2 lựa chọn
(dogfood sign-off / waiver) trong final summary phase-05. Đây là **explicit user waiver**
cho gate H3 — phase-06 (DELETE sync closure) được phép chạy. Dogfood tooling vẫn sẵn
sàng: mọi `dev sync code` thật đều stamp `summary.backend` để verify ngược về sau.
