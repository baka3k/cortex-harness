# Phase-05 — CLI contract + escape hatches + UX

Mục tiêu: hợp đồng `--parsers` rõ ràng, có escape hatch khi user nghi presence-gating, dry-run nhìn
thấy trước những gì sẽ chạy/không chạy.

## Changes

1. **registry.rs `selected_parsers`**:
   - Thêm keyword `all` ≡ hành vi `auto` hôm nay (mọi parser + framework + topology,
     `parser_auto_mode = true`). `auto` giữ nguyên. Ghi doc-comment: `all` là semantic contract
     "không bao giờ bị thu hẹp bởi cơ chế tương lai"; `auto` được phép thay đổi (D6 — chốt trong
     report phase này nếu muốn phân biệt sớm).
   - Syntax add-back `auto,+cobol` (và `all,-cobol` subtract nếu trivial): base keyword + modifiers,
     force-include/exclude parser đơn lẻ mà không phải gõ 24 tên.
   - Error "Unsupported parser(s): X" bổ sung dòng liệt kê tên hợp lệ + aliases (hiện chỉ báo lỗi trần).
2. **Dry-run detect-only** (cortex-sync flag `--report-presence`; cortex-dev `--dry-run` gọi kèm):
   - Child chạy: walk + group + summary presence (mỗi parser: routed/changed/deleted + status dự kiến)
     rồi exit 0, KHÔNG spawn analyzer, KHÔNG ghi graph/Qdrant/state.
   - `dev sync code --dry-run` in per-folder bảng detected/skipped (kèm evidence counts) — giải quyết
     yêu cầu hi-predict UX "detection thấy trước run dài".
3. **Banner `sync code all`** (cmds/sync.rs:978): đổi từ list tĩnh 24 ngôn ngữ sang in per-folder
   detected/skipped sets từ presence report (khi có); fallback list cũ khi `--parsers` explicit.
4. **Compat**: explicit comma-list path giữ byte-identical (validation + prerequisite expansion —
   registry.rs:452-471 không đụng trừ error message).

## Gates

- `--parsers all` ≡ `--parsers auto` trên fixture (summary identical, masked timestamps).
- `--parsers auto,+cobol` → cobol được force-include dù routed=0 (chạy, parse 0 file, summary ghi rõ).
- `--parsers java,cobolzz` → error liệt kê tên hợp lệ.
- `--dry-run` trên 2 folder → 2 bảng presence riêng, không spawn children, không đổi state
  (verify state file mtime không đổi).
- Explicit list regression: byte-identical behavior.
- `cargo test -p cortex-sync -p cortex-dev` + clippy xanh; smoke `dev sync code --dry-run` thật.
