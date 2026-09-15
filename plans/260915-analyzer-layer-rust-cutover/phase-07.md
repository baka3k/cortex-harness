# Phase 07 — Composition gate + delegation mirror + CI + docs

## Mục tiêu

Đóng Wave-D gate của umbrella bằng composition parity TRÊN SYNTHETIC MULTI-LANGUAGE CORPUS (red-team A2: `realworld_stock_test.py` không có analyzer dual-run, stock python-only — không dùng làm dual-run asset). Chuẩn hoá delegation + CI + docs trước khi flip/delete.

## 1. Composition corpus + harness

- Synthetic corpus mới cover **24 parsers + 12 overlays + topology + message endpoints + vector parsers** (fixture per parser, gồm shapes từng bị fixture-narrow: **spring dict properties, struts interceptor id-index** — red-team A3 thành leg bắt buộc, không waive).
- Mở rộng `sync_orchestrator_parity.py`: legs (1) graph diff mọi parser; (2) **qdrant counts + cosine** (phase-06 gates hoặc fallback state); (3) **message-scan leg** (phase-05); (4) overlay legs gồm 5 overlays từng crash; (5) topology; (6) dart/flutter/csharp legs.
- **detector_evidence quyết trước** (red-team A4): fix approximation (java/.NET module evidence) hoặc thêm vào summary mask với lý do ghi report — không quyết giữa gate.
- Overlay-binary proof leg: log chứng minh overlay children là `analyzer-*` binaries (red-team F4).

## 2. Delegation target mirror (incremental_sync.py — maps + matrix, KHÔNG refactor)

- `_RUST_ANALYZER_BINARIES` + framework map: thêm dart/csharp/topology/overlays → binary Rust.
- **Mirror ĐỦ flip matrix** mọi cell (red-team S3/S4/F10): missing-binary + value-khác post-delete semantics giống cortex-sync; `.exe` probing (Windows delegated path); preload check để maps unreachable khi mode ≠ python.
- Grep gate (định nghĩa lại theo red-team S2): "không có reference `_analyzer.py` ngoài rollback `script_path` fields của maps, và maps unreachable trừ khi mode==python".
- Windows delegation smoke (ladybug path) — **gate bắt buộc** (red-team A8/F2).

## 3. CI + docs

- `.github/workflows/cobol-macos.yml` (và mọi workflow chạy script sẽ xoá): update/remove TRƯỚC delete commit (red-team F7).
- Runbook: :27 auto-flip (đúng cho cả cortex-sync từ phase-08), §5.3 stale claim, Windows prerequisites, csharp dotnet requirement, policy quirks + detector_evidence.
- ReadMe + INSTALLER_GUIDE: analyzer layer = Rust binaries, env semantics mới.

## Gates (tất cả PASS mới sang phase-08)

1. Graph diff 0 ngoài mask (dual-run rust stack vs python stack) trên synthetic corpus.
2. Summary khớp với mask có khai báo; message + vector legs theo baseline 05/06.
3. Delegation smokes macOS (cplus-lane) + Windows (ladybug): children binary Rust, 0 Python analyzer process.
4. CI green trên nhánh plan (trước delete).
5. Report `phase07-composition-parity.md` (đóng gate umbrella Wave-D).

## Exit criteria

- Mọi gate PASS; umbrealla/plan docs cập nhật; sẵn sàng flip + delete.
