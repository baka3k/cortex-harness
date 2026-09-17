# Phase 03 — Docs & Cross-Plan Updates

1. Cập nhật README/docs nơi mô tả `dev init` (nếu có) + ghi chú cách setup
   remote: chọn `remote` trong init, hoặc sửa tay `storage_backend`/`remote`
   trong `.cortext-harness/config/{env}.json`; nhắc secrets là plaintext —
   không commit config có credentials (kiểm tra `.gitignore` đã cover
   `.cortext-harness/config/` nếu phù hợp với现状).
2. Cross-plan updates (bidirectional):
   - `260817-storage-backend-adapter/plan.md`: thêm `relatedPlans` /
     ghi chú "init UX được hoàn thiện bởi 260820-dev-init-backend-selection".
   - `260818-infra-up-remote-support/plan.md`: tương tự.
3. Ghi implementation log theo convention `hi-log` của repo (xem
   `plans/reports` / các log entry gần đây).
