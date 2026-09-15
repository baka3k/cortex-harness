---
title: "Python legacy cleanup — xoá toàn bộ Python không còn cần thiết sau Rust cutover, chốt forced-Python inventory"
status: ready (fast plan — khảo sát 2026-09-15 trên branch feat/change-db sau commit phase-08 analyzer delete 99e9092)
created: 2026-09-15
target: "code-tiny/ (mcp/, tools/, tests/, testtool/, livingdoc/, skills/), doc-tiny/, cortex_harness/, harness/scripts/, scripts/ (mcp-lifecycle, benchmarks, rust_parity, rust_mcp), tests/ (suites Python), installers/ (py helpers), Makefile, .github/workflows, docs/cutover-runbook.md, ReadMe.md, requirements*.txt"
blockedBy:
  - "260915-2027-vector-lane-rust-port"  # phase-03/04/05 chưa xong — Python MCP (unified + mind) vẫn là rollback/baseline; phase-04 còn TẠO THÊM vector_worker.py làm sidecar
blocks: []
relatedPlans:
  - "260913-2130-rust-full-migration"       # umbrella — mục tiêu "xoá dần sau cutover" là chính plan này
  - "260915-analyzer-layer-rust-cutover"    # tiền lệ trực tiếp: delete 1 commit + retired-error + rollback = git revert; dogfood sign-off vẫn mở
  - "260914-2259-dev-make-python-cutover"   # entrypoint dev/make đã binary-only; các py bridge còn sót lại thuộc disposition của plan này
  - "260915-2300-sync-plane-rust-cutover"   # nhánh B của phase-03 (giữ incremental_sync.py) SUPERSEDED: plan đó port embedded resolution + ladybug store + journal replay sang Rust rồi xoá sync closure; nhóm A1 sync-plane chuyển dead-by-plan-đó; `tools/graph/**` di sản (drivers/cli/core — live importers doc-tiny + MCP rollback, red-team C1) quay về disposition của plan này
---

# Python legacy cleanup — xoá hết Python không cần thiết sau cutover Rust

## Overview

Phase-08 của `260915-analyzer-layer-rust-cutover` đã flip default + xoá ~84,780 LOC
analyzer scripts (commit `99e9092`, rollback drill PASS), nhưng **phần lớn Python còn lại
chưa được dọn**: Python MCP servers (code + doc lane), sync delegation target, test suites
của Python đã chết, parity scripts vận hành Python, và một lớp "forced-Python" có chủ đích
(sidecars, clang plane, harness scripts). Khoảng sáng khi khảo sát 2026-09-15:

| Nhóm | LOC ước tính | Trạng thái thực | Bằng chứng |
|---|---|---|---|
| Python MCP runtime (code lane) | ~22k | **chết chờ xoá** — Rust `cortex-mcp` parity-PASS trừ vector lane (đang port) | `CORTEX_MCP_BACKEND` auto-flip, runbook mục 4/5 |
| Python MCP/query (doc lane) | ~3k | **chết chờ xoá** — mind server Rust + `cortex-doc` | `rust/crates/cortex-doc/src/ingest.rs`, mind parity PASS |
| Sync delegation target | `tools/sync/incremental_sync.py` + `tools/common` + `tools/graph` | **CÒN LIVE** — cortex-sync delegate khi graph-target resolution fail hoặc child trả `DELEGATE_SENTINEL` | `rust/crates/cortex-sync/src/orchestrator.rs:2935,276,343` |
| Forced-Python sidecars | `embed_worker.py`, `gliner_sidecar.py`, `vector_worker.py` (phase-04 vector-lane TẠO MỚI) | **LIVE có chủ đích** — rollback `CORTEX_EMBED_BACKEND=python` + GLiNER chưa port | `rust/crates/cortex-embed/src/sidecar.rs`, runbook bảng env |
| Forced-Python planes | cplus clang plane, `csharp/roslyn_worker/`, `harness/scripts/{orchestrator,context_selector}.py` | **LIVE** — decision #8 umbrella + `dev harness` spawn, không có bản Rust | plan umbrella non-goals; `rust/crates/cortex-dev/src/cmds/harness.rs` |
| Parity/golden infra | `scripts/rust_parity/` (72), `scripts/rust_mcp/` record/compare/contract | **giữ đến khi dogfood sign-off** — fixtures JSON = hợp đồng vĩnh viễn | tiền lệ phase-08: header "PY side archived, fixtures = golden" |
| Test suites Python | `tests/` (176 file .py), `code-tiny/tests`, `doc-tiny/tests`, `code-tiny/testtool` | **phần lớn chết** — test runtime Python đã/xoá sắp xoá | pytest loud-skip policy phase-08 |
| Python phụ trợ | `scripts/mcp-lifecycle.py`, `benchmark_*.py`, `audit/smoke`, `cortex_harness/{sync_processes,db_transfer,storage}.py`, `installers/**/*.py`, `code-tiny/{livingdoc,skills,scripts}`, `list_db.py` | **cần disposition từng file** — một số vẫn được Makefile/CI/installer gọi | `Makefile:180-203`, `installers/common/config_manager.py` |

**Nguyên tắc kế thừa từ phase-08 (directive đã chốt):** xoá theo cụm, mỗi cụm 1 commit,
rollback sau xoá = `git revert` (không giữ Python làm rollback file). Mọi backend flag
trỏ vào Python đã xoá phải chuyển thành **loud "retired" error**, cấm silent fallback.

**Ranh giới quan trọng:** plan này là CLEANUP, không port thêm gì. Phần còn live
(sync delegation path, sidecars, clang plane, harness scripts) được **chốt thành
forced-Python inventory** ghi vào runbook — không phải xoá bằng được.

## Scope decisions

| Câu hỏi | Quyết định | Lý do |
|---|---|---|
| Xoá luôn Python MCP dù vector-lane chưa xong? | **Không — blockedBy vector-lane phase-05** | Python unified/mind vẫn là ground-truth parity + rollback của lane đang port; xoá sớm = mất baseline giữa chừng |
| Có port nốt sync delegation / harness scripts để đạt zero-Python? | **Không — out of scope**, ghi forced-Python inventory | clang plane là decision #8 umbrella; harness scripts là công cụ user-project; port thêm = plan mới |
| Parity scripts (`rust_parity`, `rust_mcp` record/compare) | **Giữ trong suốt plan**, archive ở phase cuối sau dogfood sign-off | chúng chạy Python cần module còn sống; fixtures JSON giữ vĩnh viễn |
| Fixtures/golden data (`tests/fixtures`, `scripts/rust_mcp/fixtures`, `scripts/rust_parity` gen output) | **Không xoá** | hợp đồng parity cho mọi phase sau |
| Trình tự xoá | Query-plane trước (độc lập), sync-plane sau (cần smoke chứng minh delegate path chết), phụ trợ cuối | giảm blast radius, mỗi phase tự chứng minh |

## Phases

| # | Phase | Gate chính |
|---|---|---|
| 01 | **Disposition audit** — bảng giữ/xoá/forced từng file .py trong repo, có bằng chứng path:line từ mọi spawn site (Rust, Makefile, CI, installer, docs); smoke `dev sync` + `dev mcp start` để enumerates delegate path sống | `reports/disposition.md` hoàn chỉnh, grep audit script chạy được |
| 02 | **Retire Python query-plane** — xoá `code-tiny/mcp/`, `doc-tiny/mcp_graph_rag.py` + query path; `CORTEX_MCP_BACKEND=python` → retired-error (mirror analyzer phase-08) | gated bởi vector-lane phase-05; cargo build/clippy sạch; MCP smoke 2 server; pytest loud-skip |
| 03 | **Retire sync-plane remnants** — nếu smoke chứng minh không lane nào còn delegate: xoá `incremental_sync.py` + phần `tools/{common,graph}` không được journal-consumer/clang-plane tham chiếu, xoá delegate_to_python path; nếu còn live → ghi forced-Python, bỏ xoá | sync smoke all-binary 3 kỳ liên tiếp, không một dòng "python-plane delegation" |
| 04 | **Cleanup phụ trợ** — xoá test suites Python chết, `scripts/` obsolete (mcp-lifecycle.py đã port, benchmark/audit/smoke), `cortex_harness/` non-reference, installers py (nếu Rust installer không gọi), `code-tiny/{livingdoc,testtool,skills}` theo disposition; prune requirements + Makefile targets + CI workflows | grep audit rỗng ngoài keep-list; `make build`/`dev doctor` xanh |
| 05 | **Final sweep + chốt inventory** — archive parity scripts (header + fixtures giữ), runbook + ReadMe + INSTALLER_GUIDE cập nhật "Python còn lại = forced-Python inventory", venv/uv slimming, bookkeeping 3 plan liên quan, tag cuối | docs nhất quán; `reports/final-sweep.md`; tag `python-cleanup-final` |

Chi tiết từng phase: `phase-01.md` … `phase-05.md`.

## Non-goals

- Không port bất kỳ runtime nào sang Rust (clang plane, harness scripts, sidecars, sync delegation nếu còn live).
- Không xoá fixtures/golden data, C# roslyn worker (không phải Python), installer binary Rust.
- Không đụng default backend flags đã flip ở phase-08 analyzer (retired semantics giữ nguyên).

## Risks

| Rủi ro | Severity | Mitigation |
|---|---|---|
| Xoá nhầm Python còn live (delegate path ít chạy, chỉ trigger khi graph-target resolution fail) | Critical | Phase-01 audit + phase-03 gate: 3 kỳ sync smoke không thấy delegation; nếu không chứng minh được → giữ + ghi inventory |
| Parity scripts chết đột ngột sau delete → mất khả năng verify regression | High | Xoá runtime theo cụm, sau mỗi cụm chạy ngay parity suite còn sống; archive parity scripts chỉ ở phase-05 |
| Test suites xoá làm mất coverage cho Rust (nhiều test thật ra parity test dùng fixture chung) | High | Phase-04 chỉ xoá test **import module Python đã chết**; test dùng fixture JSON giữ |
| Dogfood sign-off umbrella chưa xong khi xoá | Medium | Rollback = git revert đã là chính sách (phase-08 precedent); ghi rõ vào runbook mục rollback |
| requirements prune làm hỏng venv của parity tooling | Medium | Phase-04 prune theo keep-list của phase-01, chạy `make parity-fixtures` xác nhận |

## Exit criteria

- Repo chỉ còn Python thuộc 1 trong 2 nhóm, mỗi file có 1 dòng trong forced-Python inventory:
  (a) runtime live có chủ đích, (b) parity/golden infra được archive có header.
- `grep -rn "\.py"` từ mọi entrypoint Rust/Makefile/CI/installer chỉ trỏ vào keep-list.
- `cargo build --release` + clippy `-D warnings` sạch; sync smoke + MCP smoke xanh không env.
- Runbook/ReadMe mô tả rollback = git revert; không còn flag âm thầm fallback về Python đã xoá.
