# Red Team Review — VB6 ANTLR Call Graph Plan

- **Ngày:** 2026-09-17
- **Mode:** `[delegate] role=reviewer mode=spawn lens=assumptions+failure-modes`
- **Artifact:** `plan.md` + `phase-01..06` + `research-digest.md` (dir `260917-1200-vb6-antlr-call-graph`)
- **Kết quả:** 10 findings — 1 Critical, 5 High, 4 Medium. **Tất cả đã disposition** (cập nhật vào plan/phase files, cột "Xử lý").

## Findings & Dispositions

| # | Sev | Finding (tóm tắt) | Evidence | Xử lý |
|---|-----|-------------------|----------|-------|
| F1 | **Critical** | Incremental sync cắt vĩnh viễn cạnh CALLS incoming từ file không đổi: cleanup `DETACH DELETE` theo `file_path IN changed` (`incremental_cleanup.py:70-84`), phase-05 chỉ re-write payload file đổi, `parse_files` đã changed-only (`vb_analyzer_base.py:545-552`) → cạnh từ caller không đổi không bao giờ được dựng lại; test phase-06 cũ chỉ check chiều outgoing nên không bắt được | code + plan | **AD-09 thêm vào plan.md**; task 4.7 phase-04: re-publish mọi cạnh có source HOẶC target ∈ changed set (worker đã trả toàn program theo AD-02 nên dữ liệu sẵn); phase-06 incremental test assert chiều incoming |
| F2 | High | `resolution_class` tự chế (`"weak"`, `"asg_resolved"`) crash lane `write_possible_calls_with_site` (`call_evidence.py:200-202` enforce vocab `RESOLUTION_CLASSES`) nhưng lọt qua `write_relations_typed` → vocabulary drift; prop `resolution_status` trên CALLS bị `write_calls` thả silently (`language_writer.py:844-848`) | code | **AD-04 viết lại**: chỉ vocab chuẩn — POSSIBLE_CALLS dùng `lexical_candidate`, chi tiết vb6 để trong `resolution_status` (free-text); CALLS cần props → lane `write_calls_with_site`; phase-04 cập nhật cả hai chỗ |
| F3 | High | Per-file timeout mâu thuẫn whole-program `analyzeFiles` (không cắt được 1 file trong 1 Program multi-pass); ProLeap error listener mặc định có thể abort cả batch khi 1 file malformed → mass fallback regex, mất trắng cải thiện của run | plan + digest | Phase-02 sửa contract: workspace-level timeout + `ignoreSyntaxErrors` (verify tên API trong spike) + per-file error attribution; **fixture thêm `malformed.bas`** làm gate "1 file không chìm batch" |
| F4 | High | M2 "≥80% resolved" vô công với fixture giàu case đặc biệt (chỉ ~8-9/14 resolvable → max 57-64%) — gate sẽ block vì fixture, không phải engine | plan + phase-01 | M2 định nghĩa lại: **recall trên tập expected-resolvable** của `expected.json`; phase-01 bổ sung ≥6 call resolvable thường; mẫu số ghi rõ |
| F5 | High | IMPLEMENTES không verify được: fixture thiếu IShip file, cạnh bị `_OPTIONAL_EXTERNAL_RELATION_TYPES` bỏ im lặng (`language_writer.py:2853-2860`); `_REL_SPECS["IMPLEMENTS"]` chỉ nhận target Interface (`ladybug_schema.py:374`) trong khi interface VB6 là Class node | code | **AD-10 thêm**; fixture thêm `IShip.cls`; phase-04: class-chỉ-là-đích-Implements được emit thành `InterfaceDef` để khớp (Class)→(Interface) |
| F6 | High | `PARSE_CACHE_VERSION` bump đậu ở phase-05 dù phase-03 đã đổi payload (parse_meta engine fields) → cửa sổ mixed-engine hydration giữa các merge | plan | **AD-08 sửa**: bump là exit criterion của **phase-03** (merge đầu đổi payload); phase-05 chỉ verify |
| F7 | Medium | `.pag` nằm trong `_SOURCE_EXTS["vb6"]` (`vb_analyzer_base.py:71`) nhưng không được materialize → mất ASG im lặng | code | **AD-03 mở rộng**: `.frm/.ctl/.pag` đều → temp `.cls` |
| F8 | Medium | Placeholder `external_symbol` Function node không có `file_path` → incremental cleanup không bao giờ xóa; tích tụ qua các sync và ô nhiễm find_callers/trace_flow | code + plan | Phase-06: reconciliation (xóa placeholder không còn cạnh sau mỗi sync) + assert non-accumulation sau 2 sync liên tiếp; quyết định (a) giữ placeholder nhưng có lifecycle |
| F9 | Medium | M4 "<2x regex" đo trên fixture 7-file bị chi phối JVM/Maven fixed cost → block phase-02 oan | plan | **M4 định nghĩa lại**: per-file amortized, corpus ≥100 files (replicate), JVM startup tách budget ≤5s |
| F10 | Medium | Premise sai: `requirements.txt` không có `tree-sitter-vb-dot-net` (vbnet error-stats cũng đang degrade trên fresh install); README drift (`tools/vb/README.md:54-59`) | code | Phase-05 cập nhật: **thêm `tree-sitter-vb-dot-net`** vào requirements (tồn tại PyPI); KHÔNG thêm vb6/vba/vbscript (không tồn tại); README sửa tương ứng |

## Coverage (reviewer xác nhận đã kiểm & clear)

- Toàn bộ path:line claims của plan.md + research-digest về `vb_common.py`, `vb_analyzer_base.py`, `vb_roslyn_adapter.py`, `go_analyzer.py`, `language_writer.py`, `ladybug_schema.py`, `call_evidence.py` — khớp code (duy sai số vô hại: Program.cs 1075 vs 1.076 dòng).
- Conventions test (tests/ root, fixture + expected.json pattern) — phase-01 khớp.
- Cross-plan 260910-1400-csharp-roslyn-upgrade: **disjoint files** — không coupling ẩn.
- Cleared as sound: AD-04 stance không-direct_resolved, schema không cần đổi cho POSSIBLE_CALLS Function→Function, bảo toàn regex fallback cho vba/vbscript, fixture-first phasing.
- Không verify được offline: hành vi ProLeap bên ngoài (Maven 404, .frm no-ASG #20, `analyzeFiles`, #5/#21) — digest đã đánh dấu đúng chỗ shaky; F3 chặn những cái materially shape worker contract.

## Verdict

Plan **được chấp nhận sau khi áp dụng F1-F10** (đã áp toàn bộ vào plan.md + phase files). Không finding nào bác bỏ hướng kiến trúc (D2 worker + resolver + evidence plane); F1 là hiệu chỉnh quan trọng nhất — nó nằm ở tầng publication/incremental mà prediction report gốc chưa nhìn thấy.


## Implementation Dispositions (phase 06, after build)

Outcome per finding, post-implementation (2026-09-17):

| # | Outcome |
|---|---|
| F1 | **FIXED + TESTED.** Whole-program payloads kept in incremental sync; edges re-published when source OR target file ∈ changed set. `tests/test_vb6_incremental.py` asserts the incoming edge `modMain.DoWork -> modUtil.CalcTotal` is rebuilt after touching only `modUtil.bas`, and that unrelated edges are NOT re-published. |
| F2 | **FIXED.** POSSIBLE_CALLS rows publish `resolution_class=lexical_candidate` (standard vocabulary only) + free-text vb6 `resolution_status`; graph-contract test asserts the vocabulary and that no ambiguous row ever lands in CALLS. |
| F3 | **FIXED + TESTED.** `VbParserParams.setIgnoreSyntaxErrors(true)` (API verified in spike), workspace-level timeout, per-file syntax-error pre-validation; batch-retry without error files if `analyzeFiles` throws. `malformed.bas` reports `ok=false` ("no procedures after parse") while all 10 other files parse (contract test). |
| F4 | **FIXED.** M2 redefined as recall over expected-resolvable sites (denominator 29); corpus gained modA/modB with 11 routine resolvable calls. Measured 29/29 = 100%. |
| F5 | **FIXED + TESTED.** `IShip.cls` in fixture; worker emits `interface::IShip@IShip.cls` Interface node; IMPLEMENTS (Type→Interface) edges verified in graph-contract test for both implementers. |
| F6 | **FIXED.** `PARSE_CACHE_VERSION` bumped to `vb-family-v2026-09-17-1` in the same change that altered payloads; dispatch test asserts the new value. |
| F7 | **FIXED.** `.frm/.ctl/.pag` all materialize to padded temp `.cls` in the adapter (`_MATERIALIZED_EXTS`). |
| F8 | **FIXED.** Placeholder reconciliation runs after every vb6 write: external_symbol Functions with no incoming CALLS/POSSIBLE_CALLS are DETACH-DELETED (same pattern as the writer's UnknownFunction prune). Non-accumulation holds structurally; live two-sync graph assert remains open for a real Neo4j environment (dev-graph verification step in runbook). |
| F9 | **PARTIAL — gate reframed.** M4 re-measured on 132 files with JVM startup separated (144 ms ≤ 5 s PASS). The relative "<2x regex" gate FAILS at ~96x (see benchmark-report.md): unattainable for any cross-module ASG; absolute cost ~10 ms/file worker-internal. Recommendation: absolute budget (≤50 ms/file). **Owner decision requested.** |
| F10 | **FIXED.** `tree-sitter-vb-dot-net==0.1.3` added to requirements.txt; vb6/vba/vbscript grammars documented as non-installable; README drift corrected (engine requirements, env vars, materialization, doctor note). |

New residual risks found during implementation (added by implementer):

- R-1: ProLeap binds ambiguous unqualified names arbitrarily (e.g.
  `TestSameName` → frmAbout). Mitigated: the python resolver ALWAYS re-derives
  unqualified calls and overrides to `ambiguous` with `candidate_ids`
  (resolver test covers it); worker-resolved QUALIFIED/typed-receiver calls
  are kept as-is.
- R-2: `write_calls` requires per-row `project_id` (enforced by the writer).
  vb6 rows now carry it; the vbnet lane's CALLS rows never did and would fail
  the same check if run with a live writer — pre-existing, out of scope
  (owner: plan 260910-1400).


## Post-implementation code review (phase 06, reviewer score 5/10 → fixed)

Adversarial review of the diff (code lens) returned 10 findings; dispositions:

| # | Sev | Finding | Disposition |
|---|---|---|---|
| R1 | Critical | `tree-sitter-vb-dot-net==0.1.3` does not exist on PyPI (install breaker) | **FIXED**: corrected to `tree-sitter-vb-dotnet==0.3.0` (verified on PyPI; import name unchanged). |
| R2 | High | `ensure_worker_built` always ran Maven and required mvn even with a fresh prebuilt jar | **FIXED**: stamp short-circuit (jar mtime vs newest pom/.java/.g4) — prebuilt jar reused with no mvn; mvn only when missing/stale. |
| R3 | High | vb asdict rows lacked `project_id_normalized` → CALLS/POSSIBLE_CALLS endpoint MATCH would find no nodes on a real graph (masked by CapturingDriver) | **FIXED**: all 10 vb `asdict_*` rows now emit `project_id_normalized` via `project_id_lookup_key`; graph-contract test asserts it on every function row. Live-graph read-back remains a runbook step (no Neo4j in this environment). |
| R4 | Medium | IMPLEMENTS not re-published when the INTERFACE file changes | **FIXED**: source-or-target rule applied to IMPLEMENTS using the interface payload's file. |
| R5 | Medium | no per-file timeout inside the whole-program batch | **ACCEPTED** (documented plan decision, red-team F3/spike-report): per-file timeout is impossible inside one multi-pass Program; workspace budget + batch-retry-without-error-files bounds the blast radius. CLI flag omitted accordingly. |
| R6 | Medium | duplicate module names merged silently (last-wins worker vs first-wins python) + fallback-casing divergence | **FIXED**: worker reports `ok=false "duplicate module name"` for colliding files; stem-fallback capitalization aligned with the python adapter. |
| R7 | Medium | Property Get/Let collapse rebound worker-resolved `Total = 42` (let) to the Get symbol | **FIXED**: registry keys functions name→list; unqualified branch keeps an ASG binding that points at a same-module target (ASG knows the property kind); golden test now verifies `expect_kind` arity strictly. |
| R8 | Medium | 35 MB Maven target/ artifacts would be committed | **FIXED**: `.gitignore` covers `antlr_worker/**/target/` (jar builds on demand; first build needs mvn+network once). |
| R9 | Low | `except (ImportError, RuntimeError, Exception)` collapses to broad | **FIXED**: honest `except Exception` with comment (factories raise varied types). |
| R10 | Low | CALLS row `resolution_status` is not persisted by `write_calls` | **ACCEPTED**: tier provenance lives on POSSIBLE_CALLS props per AD-04; the CALLS row field documents the batch contract and is harmless. |

All 49 vb6 tests + adjacent suites green after fixes (pre-existing failures
elsewhere verified unchanged via git stash).
