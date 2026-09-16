# Phase 05: Flip + re-index + dogfood + rollback drill

## Mục tiêu

Đổi default local lane sang native (writer + reader code lane, 1 commit), hatch rollback
trung thực, drill + dogfood theo convention sync-plane phase-05.

## Scope

1. **Flip commit**: `CORTEX_VECTOR_BACKEND` default → `rust` (unset = native). `=python`
   → local lane TẮT: writer FROZEN (zero-writes — đúng hành vi pre-phase-08-đến-nay,
   red-team C1: KHÔNG resurrect đường "python children ghi local"), reader code-lane quay
   sidecar đọc pickle legacy. Remote lane không đổi. Stamp `summary["backend"]`
   (`rust-native`/`python-frozen`) để dogfood verify.
2. **Legacy detect + quarantine**: instance có legacy (compound key phase-02) → notice
   trung thực lúc sync/search: "legacy pickle store detected — chạy `dev sync code
   --full-scan` để re-index". Sau lần `--full-scan` thành công (JSON store đủ points theo
   summary): rename **subtree** `<root>/collection` → `<root>/collection.legacy-pickle.bak`
   (GIỮ `cortex-local-store.json` tại root — red-team H5). Không bao giờ tự xoá.
3. **Rollback guard** (red-team H5): python leg open-time check — `cortex-local-store.json`
   present + `collection/` absent → TỪ CHỐI loudly với message phục hồi ("dữ liệu sống
   nằm ở JSON store; flip lại native bằng revert/cập nhật flag"), KHÔNG tự init pickle
   rỗng phục empty results.
4. **Runbook**: `docs/cutover-runbook.md` mục "vector local lane" — flip, hatch semantics
   (frozen, không phải delegate), re-index lệnh, divergence list (tuning inert, hnsw_ef,
   score 1e-6, flush-crash-window), quarantine + recovery.
5. **Rollback drill** — 3 legs (red-team H5):
   - Leg A: `=python` trên instance CHƯA quarantine → writer frozen assert + sidecar đọc
     pickle legacy (stale) thành công.
   - Leg B: rollback SAU quarantine → loud refusal assert (không empty-success; data an
     toàn trong JSON store).
   - Leg C (convergence): native re-run `--full-scan` → parity fixture khớp + counts
     converge (mirror sync-plane M5).
6. **Dogfood**: user chạy ≥1 kỳ thật trên stock repo với default unset (verify qua
   `summary.backend=rust-native`); waive → waiver ghi rõ `reports/phase05-drill.md`
   (tiền lệ c404164).
7. **Cross-plan closure**: update `260915-2027-vector-lane-rust-port` (D2 code-lane
   superseded — đã note rev 2026-09-16), `260915-2230-python-legacy-cleanup` (disposition
   vector code-lane children chuyển retirable), umbrella `260913-2130-rust-full-migration`
   (wave vector closed).

## Gates

- Drill report 3 legs PASS + convergence criteria leg C.
- Dogfood hoặc waiver ghi rõ.
- Legacy notice + guard test đúng trên copy instance cortex (không đụng instance sống).
- Flip revert-able: `git revert` commit flip → trạng thái opt-in (phase-02..04).

## Không làm trong phase này

- Xoá python children / vector_worker.py — cleanup plan.
- Đổi mind/doc lane.
- Migration tool.
- Resurrect python writer cho local (red-team C1).
