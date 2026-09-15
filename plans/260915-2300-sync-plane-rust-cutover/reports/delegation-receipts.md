# Delegation receipts — sync-plane Rust cutover plan (2026-09-15)

[delegate] role=researcher mode=spawn scope="Python sync-plane internals: delegation triggers, ladybug/embedded drivers, journal consumer contract, storage resolution, deletion blast radius" status=done
→ research/repository-findings.md (9 top findings; 5 porting gaps ranked; 5 open questions — tất cả đã resolve vào rev2)

[delegate] role=red-team mode=spawn scope="Adversarial verify plan rev1 vs code: false premises, missed Python dependencies, gate gaps, ordering/data-safety hazards" status=done
→ reports/red-team-rev1.md — VERDICT FAIL (1C/3H/5M/4L); toàn bộ 13 findings đã hấp thụ vào plan rev2 + phase-01..06 rev2:

| Finding | Xử lý |
|---|---|
| C1 manifest xoá file có live importers (doc-tiny, MCP rollback) | manifest re-scope = true sync closure; `tools/graph/**` chuyển python-legacy-cleanup; caller-check phase-01.3 mở rộng 7 file; gates MCP-boot + doc-sync-smoke phase-06 |
| H1 ladybug graph-name precedence sai + gate mù | phase-02 mirror cli.py:249-256 (arg > project_id > env > hyper_graph); graph-name assert không-masked phase-02/04; `graph-state dump --graph` fail nếu absent |
| H2 premise "embedded falkordb không có Rust" sai (falkor_boot.rs) | Q2 rev2 = spike-first wire qua falkor_boot; fallback fail-closed trung thực (rebuild vs keep-data); bỏ claim impossibility |
| H3 phase-06 thiếu dogfood sign-off gate | precondition gate DELETE = umbrella §2 hoặc waiver ghi rõ; mirror phase-08 |
| M1 message-lane provider gate :3064-3069 | vào cùng refactor trait-object; gate `total_graph_upserted > 0` |
| M2 empty-mode-cplus branch mâu thuẫn | phase-03 xoá cả branch :1214-1221; gate regression tách non-cplus/cplus |
| M3 ordering (vector-lane runbook edits, windows plan active) | phase-01.6 coordination probes; runbook edits sau khi commit; windows plan gate phase-06 |
| M4 grep gate không pass được | allowlist đếm đủ (dev.py, sync_processes, state, scan_ignore); dev.py dead bodies xoá |
| M5 drill thiếu convergence criteria | phase-05: graph-state diff = 0 sau 2 cycles + conservation |
| L1 test count drift | glob, không hardcode |
| L2 "native" không verify được từ summary | stamp `summary.backend = "rust-native"/"python"` phase-02/03 |
| L3 schema-index drift | graph-state dump thêm fingerprint + index set; harness so |
| L4 thiếu pre-delete tag | tag `pre-syncplane-delete` phase-06 |

Cross-plan updates (bidirectional):
- plans/260915-2230-python-legacy-cleanup/plan.md — relatedPlans + supersede note (A1 → dead-by-this-plan; tools/graph di sản quay lại disposition cleanup)
- plans/260913-2130-rust-full-migration/plan.md — relatedPlans + wave sync-plane

Scope challenge (3 câu — mode --full, user không phản hồi realtime → áp khuyến nghị mặc định, ghi plan.md §Scope decisions):
1. Biên xoá = true sync closure (C1 re-scope) — không đụng doc-plane/MCP rollback.
2. Embedded falkordb = spike-first falkor_boot (H2 rev2), fallback fail-closed trung thực.
3. Cutover = mirror phase-08 (hatch flag → flip → dogfood/waiver → tag → delete).
