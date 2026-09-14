# Rust full-migration code-complete (P01–P14) — 2026-09-14

## Context

Chương trình `plans/260913-2130-rust-full-migration/plan.md` — chuyển toàn bộ
CortexHarness Python (~160k LOC, 8 khối) sang Rust theo strangler-fig với parity gate
per-phase. Session này chạy `hi-craft --full` từ P05 đến hết.

## Change — 14/14 phases code-complete

- **P05** (5/5): shell (shlex byte-exact), ts (Navigation Intelligence V2.0 + ApiCall
  bridge), js, php, perl (tree-sitter-perl 1.2.1 vendored — không phải regex).
- **P06** (3/3): java, kotlin (kotlin-ng 1.1.0), android (XML manifest/res + 18 labels).
- **P07** (8/8): cplus (16.5k — clang plane giữ Python subprocess), go, rust, swift,
  delphi (pascal 0.10.2 chỉ parse_meta), cobol (native grammar dlopen), vb×4 (Roslyn
  subprocess thật), jp1 (cp932 decode 18,381 pairs từ CPython).
- **P08** (12/12 overlays): jvm-overlays (spring/struts/servlet_jsp ~22.5k), sql-family
  (mybatis/database_schema/sql/plsql + vendored sql grammar), web-overlays
  (fastapi_django/express_js/laravel/aspnet×2, Roslyn worker chung).
- **P09**: `cortex-sync` orchestrator (change detection hybrid/committed/hash,
  manifests, summary, log lines) — 12 runs gates a-e PASS.
- **P10**: `cortex-dev` CLI (67/67 checks) + `cortex-storage` (stress 4/4: lease race,
  generation pinning, BoundedLane, kill -9 recovery — 16/16 behaviors khớp Python).
- **P11–13**: `cortex-mcp` rmcp 3.3.0 — framework (106/106 contract), graph tools
  (38/38 byte-match sau khi fix deadlock AWI + 10 nhóm divergence), mind tools
  (38/38, latency P95 49.8ms < Python 57.0ms).
- **P14**: doc-tiny `cortex-doc` (graph diff 0) + cutover: `cortex-migrate`
  (bakatrans thật 97,559n/268,548r → ladybug 754MB, 37/37 breakdowns khớp),
  flip defaults auto-Rust với rollback `=python`, runbook `docs/cutover-runbook.md`.

## Bug đáng nhớ nhất

`serde_json/preserve_order` feature unification (từ cortex-dev/web-overlays) làm
`canonical_json` của graph-core mất sort → stable-id/fingerprint không deterministic.
Fix: canonicalize đệ quy BTreeMap tường minh. Bài học: feature additive của crate dùng
chung phải được audit ở mức workspace.

## Impact

- Risk: **medium-high** cho đến khi dogfood 1 tuần hoàn tất (gate vận hành duy nhất còn
  lại). Python giữ full rollback: `CORTEX_RUST_ANALYZER=python`, `CORTEX_MCP_BACKEND=python`.
- ~40k LoC Rust mới, 24 crates trong workspace, 398 tests + 20+ parity harnesses.
- Latency: analyzers Rust nhanh hơn Python ở mọi measurement đã đo (ts 1.5s vs 4.7s trên
  stock; mind P95 49.8 vs 57.0ms).

## Decision

- Parity-first: mọi phase có golden fixtures từ live Python + byte-diff; khó lệch nhất
  (note/summary format, json.dumps/repr semantics, legacy codecs) đều được port
  byte-exact thay vì "gần giống".
- Clang/Roslyn/GLiNER/embedding: giữ Python sidecar theo key decision #8 + Plan B —
  contract subprocess, không port ML runtime.
- Dispatch agent song song cho port máy móc (16 analyzers + overlays), tích hợp trung
  tâm qua parity harness làm oracle; fixer agent riêng cho phase 12.

## References

- plan: ./plans/260913-2130-rust-full-migration/plan.md (status: code-complete)
- reports: ./plans/260913-2130-rust-full-migration/reports/ (30+ phase reports)
- commits: e3fcd3c (14B), c9e84d7 (13), 5b8cac6 (12), 4e21731 (canonical_json fix),
  9c73a3e (07), 9c3fbda (05), 2ef33c4, 8fff6ac (04)…
- runbook: ./docs/cutover-runbook.md
