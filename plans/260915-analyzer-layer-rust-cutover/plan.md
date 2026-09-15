---
title: "Analyzer layer Rust cutover rev 2 — wire registry, port dart/csharp/topology/message-scan/embedding, flip default + xoá Python analyzer scripts sau dogfood"
status: red-team rev 2 (đã hấp thụ 15 findings — 2 Critical restructure; validate 2026-09-15: 4/4 decision mặc định khuyến nghị do user không phản hồi)
created: 2026-09-15
revised: 2026-09-15 (red-team rev1: agent×4 lenses, verdict FAIL — 2×Critical, 8×High, 5×Medium; tất cả đã xử lý, chi tiết reports/red-team-rev1.md)
target: "rust/crates/cortex-sync (registry + orchestrator + message-scan + embedding pass), rust/crates/analyzer-dart|analyzer-csharp|analyzer-topology (mới), rust/crates/cortex-embed + cortex-analyzer-framework (artifact contract + message-scan util), code-tiny/tools/*_analyzer.py + overlays + project_topology + flutter (xoá ở phase cuối), code-tiny/tools/sync/incremental_sync.py (maps+matrix mirror), .github/workflows (cobol-macos), docs/cutover-runbook.md, ReadMe.md, INSTALLER_GUIDE.md"
blockedBy: []
blocks:
  - "260914-1706-onnx-embedding-spike"  # sync-time embedding chuyển orchestrator-level (phase-06) → MCP golden re-baseline + latency của spike được unblock/open lại theo kết quả
relatedPlans:
  - "260913-2130-rust-full-migration"   # umbrella — P04-P08 analyzers parity-PASS; plan này đóng gate Wave-D chưa chạy + flip default
  - "260914-2259-dev-make-python-cutover"  # kế thừa convention rollback binary-only; seam kế tiếp dưới entrypoint
  - "260714-1603-flutter-analyzer-parser"  # tham chiếu thiết kế dart parser; phase-02 yêu cầu coordinate/pause vì là moving target
  - "260914-1706-onnx-embedding-spike"     # phase-06 phải formally overturn NO-GO (spike reports/phase05-sync-decision.md) bằng component gates
research: "plans/260915-analyzer-layer-rust-cutover/research/analyzer-cutover-digest.md"
redTeam: "plans/260915-analyzer-layer-rust-cutover/reports/red-team-rev1.md"
---

# Analyzer layer Rust cutover (rev 2) — flip default + xoá Python analyzer scripts

## Overview

Hiện trạng đo ngày 2026-09-15 (digest `research/analyzer-cutover-digest.md`, 33 findings có path:line; red-team rev1 verify thêm):

- Binary Rust analyzer đã **build đủ 32 binary** (`rust/target/release/analyzer-*`) và **parity-PASS 29/29 gate** per-analyzer (umbrella P04–P08, reports Sep 14) — nhưng parity này gồm divergence được nuốt bằng thu hẹp fixture (spring dict properties, struts interceptor — umbrella phase-08.md:44-46) → phải thành leg/policy riêng (finding 6).
- **Default chạy hôm nay vẫn là Python cho toàn bộ 37 entry**: `cortex-sync` registry chỉ map 7 parser và cần `CORTEX_RUST_ANALYZER=rust` (`registry.rs:257-290`), trong khi Python orchestrator đã auto-flip 22 parser (`incremental_sync.py:1360-1413`) — hai flip implementation divergent.
- 5 lỗ hổng chặn cutover, đều verified:
  1. **Overlay `extra_args` bị drop** — `orchestrator.rs:1669` `with_script` → 5 overlays crash vì `--framework`/`--dialect` required.
  2. **Vector + message-scan planes chỉ tồn tại ở Python children**: Rust analyzers accept-and-ignore qdrant/embed/message flags (`cortex-analyzer-framework/src/cli.rs:91-111`) — 17 parsers message-enabled; parity phase-09 chạy "(no qdrant)". Flip mà không port 2 plane này = mất capability im lặng (red-team Critical #1, #2).
  3. **Bằng chứng embedding bị red-team bác**: số parity jina int8 chưa từng chạy; spike có report NO-GO đo đạc cho sync-ingest port (uuid5 point-id, redaction regex 3 tầng, `_hash_vector`) — phase embedding phải overturn bằng component gates, không phải citation.
  4. **3 entry chưa có binary Rust**: dart/flutter (1,659 LOC), csharp (entry Python 2,059 LOC bọc Roslyn worker), project_topology (1,835 LOC, mới port nửa writer).
  5. **Windows chết ngầm**: cả 2 implementation không probe `.exe`; Windows default ladybug → mọi run delegate (`cli.rs:31-41`).

Plan này đóng toàn bộ theo strangler-fig chặt hơn rev 1: wire registry (không đổi default), port 3 entry + 2 capability plane (message-scan, embedding), composition gate trên synthetic corpus, dogfood thật, rồi flip default + **xoá** Python analyzer scripts (~84,780 LOC trừ plane forced-Python có chủ đích) trong 1 commit cuối.

## Scope decisions (user 2026-09-15 + validation log)

| Câu hỏi | Quyết định |
|---|---|
| dart/flutter chưa có binary Rust | **Port analyzer-dart mới** (covers mode dart + flutter overlay) — user chốt |
| Thời điểm xoá Python scripts | **Trong cùng plan này**, nhưng là commit cuối gated trên composition gate + ≥1 kỳ dogfood thật + rollback drill (rev 2 theo red-team; validate: user không phản hồi → áp dụng khuyến nghị) |
| "Toàn bộ tầng" gồm gì | **24 primary + 12 overlays + project_topology** — user chốt |
| Embedding (red-team Q1, không phản hồi) | **Port có gate từng component**: formally overturn NO-GO bằng parity gates riêng cho point-id/redaction/hash_vector + wall-time + provenance; FAIL → fallback end-state giữ 7 vector parsers Python |
| Message-scan (red-team Q2, không phản hồi) | **Port native trong plan** (phase-05) — không chấp nhận regression |
| Windows (red-team Q4, không phản hồi) | **Fix .exe probing + Windows delegation smoke** ở phase-07 là gate bắt buộc |

## Giữ lại có chủ đích (không phải analyzer layer — không xoá)

| Python | Lý do giữ |
|---|---|
| `tools/cplus/{clang_parser,semantic_worker,proc_analyzer}.py` (clang plane) | Key decision #8 umbrella; Rust `analyzer-cplus` spawn chúng. **Chỉ xoá `cplus_analyzer.py` (entry)** |
| `tools/csharp/roslyn_worker/` (C# project) | Worker C#; `analyzer-csharp` (Rust) spawn thẳng + port bootstrap build |
| `code-tiny/tools/sync/incremental_sync.py` + `tools/common/` + `tools/graph/` | Delegation target (ladybug / required journal lane); giữ với **maps + flip-matrix mirror patch** (phase-07) |
| `scripts/rust_parity/*` + fixtures | Golden evidence (runbook §5.2) — sau xoá là archived, thêm header note |
| `doc-tiny/*`, embed/query sidecar MCP-side | Ngoài tầng analyzer code-sync |

**Fallback end-state (nếu phase-06 embedding FAIL gate)**: plan dừng trước flip — giữ 7 parser `SHARED_VECTOR_CLI_PARSERS` (dart, go, jp1, perl, rust, shell, swift) trên Python children làm vector plane, flip 15 parser còn lại + topology; message-scan (phase-05) vẫn native. Trạng thái cuối này là acceptable terminal state, ghi vào runbook, không coi là plan failed.

## Phase map (8 phases — rev 2)

| Phase | Scope | Gate chính |
|---|---|---|
| 01 | **Registry wiring + bug fix (KHÔNG đổi default)**: map đủ 22 primary + framework→(binary, extra_args) map (sửa `with_script` bug) + topology entry; flip matrix phase-14 đầy đủ cả .exe probing; **unset giữ nguyên → Python** (không có cửa sổ mất vector/message); artifact embedding contract freeze (thiết kế, chưa implement) | Unit test flip matrix 8 ô + .exe; overlay cmd chứa `--framework`/`--dialect`; `=rust` opt-in smoke: children binary, embedding pass + message lane **vẫn Python children** (opt-in an toàn) |
| 02 | **Port `analyzer-dart`** (mode dart + flutter; `--mode all` quyết tường minh; grammar pin verified/vendored là entry criterion; coordinate plan 260714-1603) | Parity 2 mode + orchestrator leg riêng (dart/flutter) qua sync_orchestrator_parity |
| 03 | **Port `analyzer-csharp`** (spawn thẳng Roslyn worker + **port bootstrap build**; fallback-usage gate đo fallback có được dùng thật; rationale ghi trong plan) | Parity vs Python entry (cùng worker) + fallback-measurement leg + negative test |
| 04 | **Port `analyzer-topology`** (tái dùng `cortex-graph-writer::topology`) | Parity trên stock + orchestrator leg |
| 05 | **Port message-scan native** (tái dùng logic `tools/common/message_scan.py`: graph MessageEndpoint merge + message qdrant lane; ownership của message vectors + qdrant layout gán rõ) | Parity message nodes/rels + message vectors vs Python children baseline trên corpus có message endpoints |
| 06 | **Embedding orchestrator-level qua cortex-embed — component-gated overturn của NO-GO**: (a) point-id uuid5 parity, (b) redaction parity, (c) `_hash_vector` parity, (d) stale-filter fields + vectors_config byte-match, (e) wall-time baseline, (f) provenance pins (HF revision + sha256 in-repo + ensure-ort fail-closed), (g) artifact 0600; FAIL → fallback end-state | Report phase06-vector gates từng component; cross-plan note cho spike re-baseline |
| 07 | **Composition gate + delegation + CI**: synthetic multi-language corpus cover 24 parsers + 12 overlays + topology; quirk legs (spring/struts); detector_evidence quyết trước (fix hoặc mask có lý do); message + vector legs; overlay-binary proof; delegation smokes **macOS + Windows (ladybug)**; maps + flip-matrix mirror vào incremental_sync.py (mọi cell, post-delete semantics); CI workflows update | Graph diff 0 ngoài mask + summary khớp (mask có khai báo); 2 delegation smoke PASS; grep gate: references ngoài rollback script_path = 0 |
| 08 | **Dogfood → flip + DELETE (1 commit cuối)**: ≥1 kỳ dogfood thật (runbook 7 ngày là ops sign-off của umbrella); flip default unset→Rust + retired-error cho mọi value non-Rust & missing-binary ở CẢ cortex-sync và delegation target; xoá scripts; rollback drill (revert trên scratch checkout, re-sync mixed-provenance, converge); bookkeeping 3 plan liên quan | Drill converge; `cargo build` sạch; pytest loud-skip không âm thầm; sync smoke children 100% binary mọi lane |

Phụ thuộc: 01 trước tất cả. 02, 03, 04, 05 độc lập (song song được). 06 cần artifact contract freeze của 01 (không cần 02-05; contract dùng chung qua cortex-analyzer-framework nên dart không build 2 lần). 07 cần 01–06. 08 cần 07 + dogfood.

## Key architectural decisions

1. **Một flip implementation duy nhất, mirror 2 chiều**: `registry.rs` là nguồn chân lý; phase-07 mirror ĐỦ matrix (mọi cell: unset/`=rust`/`=python`/khác × binary-có/thiếu) sang delegation target; post-delete mọi đường non-Rust → loud "retired, rollback = git revert".
2. **Default không đổi đến phase-08**: unset → Python trong suốt 01–07 (không cửa sổ mất vector/message cho default path; dogfood phase-08 đo hệ thống đã đóng băng). Opt-in `=rust` cho phép verify sớm, an toàn vì embedding + message pass vẫn pin Python children đến 05/06.
3. **Embedding + message-scan là capability planes phải port trước khi xoá** — không chấp nhận regression im lặng (red-team Critical). Mỗi plane có parity gate riêng; embedding phải overturn NO-GO của spike bằng component evidence, không citation.
4. **Missing binary không silent-fallback sau flip**: hard error + hint build; trước flip (opt-in) giữ warn-fallback đúng semantics phase-14. `--version` build-commit handshake chống stale binary; embedding artifact thiếu/invalid → hard-error (không tái tạo bug mất vector im lặng).
5. **csharp giữ worker C#, port entry + bootstrap**: rationale — directive "xoá toàn bộ Python analyzer scripts" khiến entry Python không thể tồn tại; pattern chứng minh bởi analyzer-web-overlays. Tree-sitter fallback không port; fallback-usage gate quyết policy user-facing.
6. **cplus clang plane giữ Python subprocess** (decision #8 umbrella) — loại trừ khỏi delete list.
7. **Không đụng đường doc-sync** (`dev sync doc` forced-Python — umbrella phase-14).

## Risks & gates

| Risk | Severity | Gate |
|---|---|---|
| Embedding component gates fail (point-id/redaction/hash_vector/wall-time) | Critical | Phase-06: dừng trước 07/08 → fallback end-state (7 vector parsers giữ Python) — documented, không coi là failed |
| Message-scan parity fail | High | Phase-05: dừng trước flip; fallback = giữ message-enabled parsers Python (mở rộng fallback end-state) |
| Composition fail trên synthetic corpus (quirk springs/struts, detector_evidence, overlay args) | High | Phase-07: quay lại phase tương ứng; không waive |
| Windows delegation smoke fail (.exe, đường dẫn, ladybug) | High | Phase-07 gate bắt buộc trước delete |
| Delegated run chạm script đã xoá (missing-binary cell) | High | Phase-07 matrix mirror + phase-08 retired-error mọi cell |
| tree-sitter-dart grammar không có pin tương thích trên crates.io | Medium | Phase-02 entry criterion: verified pin hoặc vendored — không có thì dừng phase-02 trước khi code |
| Roslyn worker unavailable/fallback được dùng thật trên corpus | Medium | Phase-03 gate: đo + policy; nếu fallback thật sự cần → escalate trước merge |
| Xoá 84k LOC đứt tham chiếu ngoài tầm nhìn (CI workflows, sync_processes, run_migration, skills doc, tests) | Medium | Phase-07 update CI; phase-08 grep audit nêu đích danh + pytest skips phải loud |
| MCP query golden shift sau vector lane đổi backend | Medium | Cross-plan handoff sang spike (đã pending) — phase-06 ghi nhận, không chặn |
| Rollback revert không hội tụ trên mixed-provenance state | Medium | Phase-08 rollback drill là gate |
| Stale binary (pull-without-rebuild) emit artifact sai/old contract | Medium | `--version` handshake + artifact hard-error |

## Verification strategy

- Per-analyzer parity: `scripts/rust_parity/analyzer_parity_*.py` cho dart/csharp/topology + message-scan + vector components.
- Orchestrator leg riêng cho mỗi parser mới port (02→dart/flutter, 03→csharp, 04→topology) TRƯỚC khi merge phase — phase-07 chỉ là aggregate re-run (red-team C6).
- Composition: synthetic multi-language corpus (mới, phase-07) — KHÔNG dùng `realworld_stock_test.py` làm dual-run asset (red-team A2: script không có analyzer dual-run, stock python-only).
- Flip matrix unit test + post-delete retired-error test; Windows delegation smoke.
- Rollback drill cuối plan.

## Red Team Review (rev 1 → rev 2)

Verdict FAIL rev 1: 2×Critical, 8×High, 5×Medium — **toàn bộ 15 findings đã xử lý** (adjudication chi tiết: `reports/red-team-rev1.md`). Thay đổi cấu trúc chính: (1) phase-05 cũ (embedding) tách thành phase-05 message-scan + phase-06 embedding component-gated; (2) default flip dời từ phase-01 về phase-08 sau dogfood; (3) composition gate chuyển sang synthetic corpus + incremental per-parser legs; (4) matrix mirror + retired-error mọi cell; (5) Windows .exe + smoke; (6) CI workflows vào checklist.

## Validation Log (2026-09-15)

| # | Câu hỏi | Quyết định |
|---|---|---|
| 1 | Embedding: overturn NO-GO có gate / carve-out 7 parser / chờ spike | **Port có gate từng component** (khuyến nghị; user không phản hồi → default) |
| 2 | Message-scan: port native / chấp nhận regression | **Port native trong plan** (khuyến nghị; default) |
| 3 | Delete timing: cuối plan sau dogfood / ngay sau gate | **Delete cuối plan sau dogfood + rollback drill** (khuyến nghị; default) |
| 4 | Windows: fix .exe + smoke / out-of-scope | **Fix .exe + Windows delegation smoke là gate** (khuyến nghị; default) |

## Active-plan coordination

- `260913-2130-rust-full-migration`: plan này đóng gate Wave-D chưa check (phase-08.md tail); cập nhật trạng thái umbrella ở phase-08.
- `260914-1706-onnx-embedding-spike`: phase-06 overturn NO-GO bằng component gates; kết quả (pass/fail + wall-time) feed thẳng vào 2 open item của spike (golden re-baseline + latency). Update frontmatter spike (blockedBy) ở phase-06.
- `260714-1603-flutter-analyzer-parser` (in_progress): phase-02 coordinate/pause — parity reference không được mutate giữa chừng; sau phase-08 xoá script, plan đó chuyển reference-only.
- `260914-2259-dev-make-python-cutover`: kế thừa convention binary-only + rollback; seam kế tiếp dưới entrypoint.
