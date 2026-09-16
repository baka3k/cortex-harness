---
title: "Presence-gating + parser retirement — formalize selection chính xác đã có, sửa gốc rễ COBOL-junk (copybook rule), thêm per-parser retirement, một canonical parser set cho 3 pass, observability + escape hatches (theo hi-predict verdict CAUTION 2026-09-16 — KHÔNG xây detector heuristic)"
status: planned
created: 2026-09-16
target: "rust/crates/cortex-sync (routing.rs, orchestrator.rs, registry.rs, registry_tests.rs), rust/crates/analyzer-cobol (pipeline.rs, analyzer.rs), rust/crates/cortex-analyzer-framework (cleanup.rs), rust/crates/cortex-graph-writer (retirement queries mới), rust/crates/cortex-dev (cmds/sync.rs), plans/260915-2300-sync-plane-rust-cutover (cross-update)"
blockedBy:
  - "260915-2300-sync-plane-rust-cutover"  # WIP uncommitted cùng crates (graph-writer/topology.rs, message_scan/graph.rs) + phase-02/03 viết lại cùng vùng orchestrator (message-lane gate :3064-3069, journal lane); retirement (phase-04) cần Box<dyn GraphStore> polymorphic từ phase-02 của nó. Phase-01 (fixtures + routing test) có thể authored song song nhưng KHÔNG execute trên working tree chưa commit.
blocks: []
relatedPlans:
  - "260913-2130-rust-full-migration"       # umbrella — plan này thuộc wave sync-plane, chạy sau sync-plane-cutover
  - "260813-2152-code-sync-phase-modes"     # completed — reference semantics full/incremental mode
  - "260909-0854-ignore-folders-config"     # active — chạm walk/ignore, overlap nhỏ ở SKIP_DIRS; coordinate khi đụng walk.rs
  - "260915-2230-python-legacy-cleanup"     # không phụ thuộc trực tiếp; retirement dùng Rust planes nên không bị chặn
research: "plans/260916-0936-presence-gating-parser-retirement/research/prediction-report.md"
---

# Presence-gating + parser retirement — "detect ngôn ngữ" đúng cách

## Overview — hiện trạng đo 2026-09-16 (hi-predict deep, 5 persona, code-verified)

Request gốc: "detect gần đúng các loại ngôn ngữ cần chạy analyze thay vì chạy toàn bộ; sai số thực tế:
quét source Android mà graph có COBOL". Phân tích hi-predict (**verdict CAUTION**) bác bỏ hình dạng
"bộ detect heuristic mới" vì:

1. **Presence-gating chính xác đã tồn tại ở MỌI mode, kể cả full scan**: orchestrator walk toàn cây mỗi
   run (`walk_all_source_files`, orchestrator.rs:1104), full scan đặt `changed_paths = all_source_paths`
   (:1186), và gate `if parser_scan.is_empty() && parser_deleted.is_empty() { continue }` (:1487) chạy
   vô điều kiện — parser không có file nào route về là không bao giờ được spawn. `--parsers auto` =
   *upper bound* (registry.rs:449), không phải "chạy mù tất cả".
2. **Triệu chứng COBOL nằm ở 2 chỗ khác**:
   - **Stray-extension junk**: `.cpy`/`.copy` (copybook COBOL) là branch ĐẦU TIÊN của
     `_select_parser_for_path` (routing.rs:403). Chỉ 1 file rác trùng extension (fixture/vendored/docs)
     → cobol được spawn, parse, ghi node rác vào graph + collection Qdrant riêng.
   - **Không có retirement theo parser**: cleanup graph chỉ per-file (`cleanup_neo4j_for_files`);
     topology cleanup chỉ phủ `topology_owned` (cortex-graph-writer/topology.rs CLEANUP_*); Qdrant
     collection `{project}__{sha1(root)}__{parser}_functions` (registry.rs:492) tồn đọng mãi. COBOL đã
     ghi vào graph từ sync trước không bao giờ được dọn dù parser không chạy lại.
3. **Message lane bypass `parser_filter`** (verified): `enabled_parsers` =
   `PARSER_ITERATION_ORDER ∩ message_enabled_parsers` (orchestrator.rs:3041-3044), KHÔNG giao filter —
   `--parsers java` vẫn cho message lane xử lý 17 parsers (có gate routed-files riêng :3072 nhưng vẫn
   lệch contract).
4. **Observability hụt**: parser bị skip biến mất trước khi vào summary (`continue` :1489 đứng trước
   push :1529) — không ai nhìn thấy "cobol: routed=0, skipped".

**Bất đối xứng lỗi (consensus 5/5 persona)**: false positive = tốn vài phút + node rác nhìn thấy;
false negative = mất dữ liệu graph im lặng với exit 0. Mọi heuristic mới chỉ có thể TRỪ parser so với
cơ chế chính xác hiện có → thêm đúng 1 class lỗi: FN. Vì vậy plan này **cấm** heuristic chấm điểm,
threshold số file, marker scoring mới.

## Scope decisions (từ hi-predict report; user approve chạy hi-plan = chấp nhận reframed scope)

| # | Quyết định | Lý do |
|---|---|---|
| D1 | **Không xây detector heuristic mới.** Formalize + expose presence-gating có sẵn | Gate count>0 chính xác đã chạy mọi mode; detector mới chỉ thêm rủi ro FN |
| D2 | **Copybook routing rule** ở tầng grouping (không phải per-path): `.cpy/.copy` → cobol chỉ khi `all_source_paths` có ≥1 `.cbl/.cob` program | Copybook không program là inert; mọi dự án COBOL thật có program file → class FN ≈ 0. Check ở `all_source_paths` (parent đã walk mỗi run, có ở cả 2 mode) chứ không phải diff subset — incremental đổi 1 `.cpy` mà `.cbl` không đổi vẫn phải route đúng |
| D3 | **Per-parser retirement là mảnh bắt buộc** — không có thì triệu chứng gốc không được sửa (rác cũ tồn đọng + secret đã xoá vẫn searchable trong vector cũ) | Security severity high (hi-predict R2) |
| D4 | Retirement **chỉ chạy ở full-scan/`--reconcile`** + two-strike hysteresis (state file), KHÔNG chạy trên incremental | Chống vòng xoáy flicker-deletion (hi-predict conflict #5) |
| D5 | Một canonical parser set gates cả 3 pass (primary/embedding/message) | Sửa divergence có sẵn; tránh 3 cơ chế gating tự lệch nhau |
| D6 | `auto` giữ nguyên semantics (đã là presence-gated); thêm alias `all` = hành vi "chạy mọi parser có file" theo nghĩa cũ của auto (documentation-level), `auto,+<parser>` force-include | Backward-compat; escape hatch khi user nghi detection |

⚠️ D6 cần chốt lại lúc implement: sau plan này `auto` và `all` trùng hành vi. Khác biệt duy nhất giữ
`all` là **semantic contract cho tương lai** (nếu sau này auto thêm heuristic, `all` vẫn là "không
heuristic"). Ghi decision vào phase-05 report.

## Non-goals (tường minh — cấm scope creep về phía heuristic)

- Bộ detect ngôn ngữ heuristic/scoring (package signals, marker điểm số) ngoài những gì `frameworks.rs`
  đã có.
- Threshold "≥N file mới chạy parser" — mọi threshold >1 tụt ngôn ngữ nhỏ (1 migration `.sql`).
- Disk cache cho detection/presence (in-memory per-run của classifier hiện có là đủ).
- Parse descriptor bằng format parser thật (YAML/Groovy eval) — substring-only như hiện tại.
- Đổi semantics incremental gating (đã đúng).

## Phase map (5 phases)

| Phase | Tên | Deliverable chính | Gate chốt |
|---|---|---|---|
| 01 | Copybook routing rule + zero-program write guard | Demotion `.cpy/.copy`→None khi root không có program file (routing.rs tầng group + orchestrator demote dùng `all_source_paths`); analyzer-cobol suppress graph/Qdrant write khi parse 0 program file | Fixtures: cây chỉ `.cpy` → cobol không spawn/ghi; cây `.cbl`+`.cpy` → cobol chạy đủ; regression routing JP1/Android/VB không đổi; `cargo test -p cortex-sync -p analyzer-cobol` xanh |
| 02 | Observability — skipped-with-evidence + unmatched post-check | Parser skip được ghi vào summary (`status: "not_detected"/"no_changes"`, `routed` counts, reason) thay vì biến mất; dòng `[impact] skipped`; post-check cảnh báo khi >5% walked files route về không parser nào (top extensions) | Golden summary fixture chứa `not_detected` entries; unmatched warning fire trên cây crafted; summary JSON machine-readable |
| 03 | Canonical parser set cho 3 pass + CLI plumbing | Message lane intersect `parser_filter`; embedding pass dùng cùng set; `sync.rs` multi-folder path plumb `--parsers` thay hardcode `auto` (:1038) | `--parsers java` full-run → message lane chỉ java; auto → hành vi không đổi (regression) |
| 04 | Per-parser retirement | Probe node-ownership property (step 0); purge query theo provider qua `Box<dyn GraphStore>`; delete Qdrant collection `code_collection_name(...)`; two-strike state; `retired_parsers` evidence vào summary | Fixture 2 leg: sync-with-cobol → sync-without-cobol (full scan) → nodes + collection biến mất; flicker incremental → không xoá; idempotent; provider matrix neo4j + (sau cutover) ladybug |
| 05 | CLI contract + escape hatches + UX | Alias `all`; `auto,+cobol`; error "Unsupported parser(s)" liệt kê tên hợp lệ; banner `sync code all` per-folder; dry-run detect-only report (`--report-presence` ở cortex-sync, parent gọi khi `--dry-run`) | CLI tests: explicit list byte-identical; `all` ≡ auto hiện tại; dry-run in detected/skipped per folder không spawn children |

## Cross-plan coordination (bắt buộc đọc trước khi execute)

- **260915-2300-sync-plane-rust-cutover (ready, WIP uncommitted)**: phase-02/03 của nó viết lại
  `open_store` (polymorphic store — retirement phase-04 cần), message-lane provider gate (:3064-3069),
  journal lane. Execute plan này **sau khi** cutover commit phase-02; phase-01 fixtures có thể authored
  song song (không đụng working tree của nó).
- **260913-2130-rust-full-migration**: umbrella dogfood gate — không block, chỉ coordinate timing.
- **260909-0854-ignore-folders-config (active)**: nếu nó đổi `walk.rs`/SKIP_DIRS, rebase phase-01 fixture
  paths.

## Acceptance (tổng)

Về phía user: sync 1 source Android có 1 file `.cpy` rác → (1) log/summary thấy cobol `not_detected`,
(2) graph KHÔNG có node COBOL, (3) nếu trước đó đã dính COBOL thì sau 1 full-scan retirement, node +
collection COBOL biến mất. Đủ 3 điều kiện này = mục tiêu request gốc đạt.
