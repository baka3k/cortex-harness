# Phase-03 — Canonical parser set cho 3 pass + CLI plumbing

Mục tiêu: một `parser_filter` duy nhất gates cả primary/embedding/message — xóa divergence có sẵn
(message lane bypass filter) trước khi phase-04 gắn retirement vào cùng set.

## Changes

1. **Message lane intersect `parser_filter`** (orchestrator.rs:3041-3044): `enabled_parsers` =
   `PARSER_ITERATION_ORDER ∩ message_enabled_parsers ∩ parser_filter`. Giữ nguyên order-sensitive
   cleanup_all semantics (comment :3035-3040) — chỉ thu hẹp tập, không đổi thứ tự.
2. **Embedding pass** (orchestrator.rs:2062+): xác minh đã intersect `parser_filter` (persona báo có
   ở :2097 — verify lại khi mở file; nếu có rồi thì chỉ thêm assert/test khóa hành vi).
3. **Single-source comment + helper**: helper `effective_message_parsers(parser_filter)` trong
   registry.rs trả tập đã giao — 3 pass cùng gọi, không ai tự build set riêng nữa.
4. **CLI plumbing** (cortex-dev/src/cmds/sync.rs:1038 — `sync code all` hardcode `"auto"`): truyền
   giá trị `--parsers` của user xuống thay hardcode (multi-folder path dùng chung biến `parsers` như
   `sync_code` :894-895).

## Gates

- `dev sync code --parsers java --full-scan` trên cây đa ngôn ngữ → message lane chỉ xử lý java-eligible
  parsers; summary `native_message_scan` thể hiện đúng tập.
- `auto` full-run trên cùng cây → hành vi không đổi vs pre-change (regression golden).
- `sync code all --parsers java` → child nhận `--parsers java` (không còn hardcode auto).
- `cargo test -p cortex-sync -p cortex-dev` xanh; parity suite không red.
