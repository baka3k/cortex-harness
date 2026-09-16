# Phase-01 — Copybook routing rule + zero-program write guard

Mục tiêu: chết gốc rễ triệu chứng "source Android dính COBOL" — file `.cpy`/`.copy` rác không còn
kéo cobol analyzer chạy/ghi dữ liệu.

## Changes

1. **Demotion rule ở tầng grouping** (cortex-sync/src/routing.rs — function `group_paths_by_parser`,
   orchestrator.rs:1356 call site):
   - Sau khi group, nếu group `cobol` chứa **chỉ** copybook extensions (`.cpy`/`.copy`) và
     `all_source_paths` (đã có sẵn ở orchestrator — parent walk mỗi run, orchestrator.rs:1104) không
     chứa file nào với `.cbl`/`.cob` → demote các path copybook về `None` (rơi khỏi mọi parser group).
   - **Check chống `all_source_paths`, KHÔNG chống diff subset**: incremental đổi 1 `.cpy` khi các
     `.cbl` im lặng vẫn phải route đúng (D2).
   - Đặt rule trong routing.rs (sở hữu taxonomy parser), không đặt rải rác trong orchestrator.
2. **Zero-program write guard trong analyzer-cobol** (analyzer-cobol/src/pipeline.rs, sau
   `iter_cobol_files` + parse):
   - Nếu 0 program file (`.cbl`/`.cob`) được parse (chỉ copybook): skip toàn bộ write graph/Qdrant,
     summary `processed_program_files: 0` + status rõ ràng. Belt-and-suspenders cho đường chạy trực tiếp
     (manifest fallback, invocation thủ công) — routing rule là lớp chính.
3. **Không đổi**: `_select_parser_for_path` per-path mapping (giữ branch `.cpy/.copy` → cobol — per-path
   không có context root); walk.rs SOURCE_EXTENSIONS (file vẫn hash/inventory); message-scan (cobol
   không nằm trong `message_enabled_parsers`).

## Gates

- Fixture A: cây chỉ có `legacy/x.copy` (không `.cbl/.cob`) → `group_paths_by_parser` trả cobol rỗng;
  full sync không spawn cobol; graph + Qdrant không có artifact COBOL.
- Fixture B: cây có `prog.cbl` + `legacy/x.copy` → cả 2 route cobol, analyzer parse đủ (regression
  chống demote oan).
- Fixture C (incremental): sync lần 1 với `prog.cbl`; sửa `x.copy` → cobol vẫn được route (chứng minh
  check chống full-set chứ không phải diff).
- Regression routing: JP1 `.txt` sniff, Android classifier, VB classifier behavior không đổi
  (registry_tests.rs mở rộng).
- `cargo test -p cortex-sync -p analyzer-cobol` + `cargo clippy -D warnings` xanh.
