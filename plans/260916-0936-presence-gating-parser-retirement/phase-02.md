# Phase-02 — Observability: skipped-with-evidence + unmatched post-check

Mục tiêu: parser bị skip không còn "biến mất im lặng" — mọi skip có reason + counts, user/CI tranh
luận được; bảo vệ FN cuối cùng bằng post-check unmatched files.

## Changes

1. **Skip-with-evidence trong primary loop** (orchestrator.rs:1487-1489 — hiện `continue` đứng trước
   push :1529 nên skipped parser không vào summary):
   - Trước `continue`, push parser entry vào `parser_summaries` với `status: "not_detected"` (routed=0
     trên full scan) hoặc `"no_changes"` (incremental, routed>0 nhưng changed/deleted rỗng) + số liệu
     `routed/changed/deleted`. Tách khỏi status `"skipped"` hiện có (incremental-unsupported, :1542) —
     3 status, 3 ngữ nghĩa.
   - Dòng log `[impact] parser={name} routed={n} skipped ({reason})` cho mọi parser bị skip.
2. **Unmatched-files post-check** (orchestrator, sau khi group):
   - Đếm walked files route về `None`; nếu > 5% tổng walked files → `[detect] N files match no parser
     (top: .xyz x312, …)` + gợi ý `--parsers` add-back. Warn-only, không fail (tránh brick sync trên
     cây lạ).
3. **Summary schema**: các field mới machine-readable, thêm vào child summary JSON (consumer hiện có:
   cortex-dev cmds/sync.rs summary printer — mở rộng bảng in `not_detected` count).

## Gates

- Golden fixture: cây Android + 1 `.cpy` → summary chứa entry cobol `status: "not_detected"`,
  `routed: 0`; java/kotlin/android `status: "ok"` như cũ.
- Fixture cây có ≥5% file lạ (ví dụ `.foo`) → warning fire với top extensions đúng.
- Fixture cây bình thường → không warning (noise = 0).
- Summary JSON schema test: mọi parser entry có đủ {status, routed, changed, deleted, reason?}.
- `cargo test -p cortex-sync -p cortex-dev` xanh.
