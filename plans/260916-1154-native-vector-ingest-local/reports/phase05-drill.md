# Phase-05 — flip + rollback drill — 2026-09-16

Plan: `plans/260916-1154-native-vector-ingest-local/plan.md` rev2, phase-05.
Drill: `scripts/rust_mcp/vector_local_drill.py` (probes:
`vector_writer_probe resolve`, `sidecar_guard_probe`).

## Flip

- `LOCAL_NATIVE_DEFAULT = true` (`cortex-storage/src/qdrant.rs`) — unset
  `CORTEX_VECTOR_BACKEND` ⇒ local lane native (writer JSON engine + reader
  không-lock). `=python` giữ nguyên ngữ nghĩa FROZEN/sidecar. **1 commit riêng**
  để revert 1 phát (commit tách trong git history).

## Drill legs — 3/3 GREEN

| Leg | Kịch bản | Kết quả |
|---|---|---|
| A — pre-quarantine rollback | `CORTEX_VECTOR_BACKEND=python` trên instance CHƯA quarantine: sidecar đọc pickle legacy (stale `LEGACY_STALE_SYMBOL` hiển thị rõ) + `open_native_store` → Unsupported "FROZEN at zero writes" | ✅ PASS — đúng semantics D5/red-team C1: python leg = stale read + writer frozen, KHÔNG có "delegate python children" |
| B — post-quarantine rollback | root legacy-only → guard chặn loudly lúc open (bước re-index bị chặn cho tới khi quarantine) → `mv collection collection.legacy-pickle.bak` → native re-index OK → python leg (`CORTEX_VECTOR_BACKEND=python` reader) **refuse LOUDLY** ("refuses … JSON vector store"), không serve empty | ✅ PASS — red-team H5 đóng |
| C — native convergence | 2 lần full_replace liên tiếp qua seam thật → ids/count converge (`id-1..3`, count 3) | ✅ PASS |

## Lưu ý trình tự phát hiện khi drill (sửa runbook tương ứng)

Guard compound-key chặn **cả writer** trên root còn nguyên pickle → re-index
trong place phải **quarantine TRƯỚC** (rename `collection/` → `.bak`), rồi mới
`dev sync code --full-scan`. Plan rev2 viết "quarantine sau re-index" chỉ đúng
cho biến thể fresh-root; runbook §0c đã chốt trình tự quarantine-first (guard
tự bắt buộc, lỗi trung thực kèm recipe — không phải hỏng hóc).

## Dogfood

**PENDING — chờ user** (gate: dogfood ≥1 kỳ HOẶC waiver ghi rõ). Chưa chạy
dogfood thật vì (a) cần re-index instance thật (`mv` subtree + full-scan —
đụng dữ liệu user, không tự ý làm), (b) precedent sync-plane: flip merge trước,
dogfood gate chờ user (commit `c404164` là waiver mẫu). Khuyến nghị chạy:

```bash
# per instance (xem docs/cutover-runbook.md §0c):
mv <qdrant-code-root>/collection <qdrant-code-root>/collection.legacy-pickle.bak
dev sync code --full-scan
# rồi dùng semantic_search thường ngày ≥1 kỳ trước khi mở plan xoá python children
```

Sau dogfood/waiver: plan `260915-2230-python-legacy-cleanup` mở khoá xoá
children vector code-lane (xem cross-update trong disposition.md A3).
