# Phase 03 — Port analyzer-csharp (Rust entry + Roslyn worker + bootstrap)

## Rationale (red-team finding 8 — ghi bắt buộc)

Directive "xoá toàn bộ Python analyzer scripts" khiến entry Python `csharp_analyzer.py` (2,059 LOC) không thể tồn tại sau phase-08 → port entry là hệ quả trực tiếp của scope user đã chốt, không phải scope creep. Carve-out alternative của digest bị loại vì lý do này.

## Mục tiêu

Crate `analyzer-csharp` spawn THẲNG Roslyn worker C# (`tools/csharp/roslyn_worker/` — giữ nguyên), thay cả entry Python lẫn `roslyn_adapter.py`.

## Scope code

| Thành phần | Ghi chú |
|---|---|
| `rust/crates/analyzer-csharp/` (mới) | [[bin]] `analyzer-csharp`; wire protocol với worker (tham chiếu `roslyn_adapter.py:39-99`); env plumbing (`DOTNET_ROLL_FORWARD=LatestMajor` như parity aspnet) |
| **Worker bootstrap** (red-team F6) | Port auto-build: adapter Python tự `dotnet build -c Release` (`roslyn_adapter.py:92-103`) — Rust entry phải làm tương tự (build nếu dll thiếu/stale) hoặc fail với hướng dẫn rõ; INSTALLER_GUIDE/runbook thêm prerequisite dotnet |
| `rust/crates/cortex-sync/src/registry.rs` | map `csharp` → `analyzer-csharp` |
| Giữ nguyên | `tools/csharp/roslyn_worker/` |

## Quyết định thiết kế: tree-sitter fallback

Entry Python có tree-sitter fallback per-file khi worker unavailable (`csharp_analyzer.py:1156-1163`). Rust entry KHÔNG port fallback — worker unavailable = loud error. **Gate (red-team A6)**: instrument parity run để ĐO fallback có được trigger thật trên corpus không; nếu có (worker không cover case nào đó) → escalate trước khi merge, không silent-port. Policy user-facing (dotnet requirement) ghi trong phase-07 docs.

## Parity gate

1. `scripts/rust_parity/analyzer_parity_csharp.py`: dual-run PY entry vs RS entry (CÙNG worker) trên testdata C# + repo .NET thật; graph diff 0 ngoài mask; `[SCAN_RESULT]` byte-identical; incremental counts.
2. Message-scan leg riêng cho csharp (message_enabled_parsers có csharp — message plane vẫn Python-children pin đến phase-05).
3. Fallback-usage measurement: log/tally fallback triggers trong baseline Python run.
4. Negative test: worker unavailable + bootstrap fail → exit code + message rõ.

## Exit criteria

- Parity PASS + fallback gate report `phase03-csharp-parity.md`; escalation (nếu có) resolved.
- Sync smoke opt-in: csharp children là binary Rust.
