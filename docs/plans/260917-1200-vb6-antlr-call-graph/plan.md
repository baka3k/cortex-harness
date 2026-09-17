---
title: "VB6 ANTLR Call Graph — ProLeap Java Worker + Resolver + Evidence Plane"
status: complete-with-exclusions
completed: 2026-09-17
commit: 0dbddb2
exclusions: M4 relative-throughput gate reframed to absolute budget (owner review, benchmark-report.md); live-graph MCP smoke (M5 final) left as re-sync runbook step (no Neo4j in dev environment)
created: 2026-09-17
mode: hi-plan --full
scope: "Thay extraction VB6 regex-only bằng engine ANTLR-first (Java worker bọc ProLeap, mirror RoslynVbWorker), thêm project-model resolver (.vbp + Attribute VB_Name + arity), two-tier publication CALLS/POSSIBLE_CALLS, sửa Class-ID collision, fixture-first golden tests + benchmark gate"
blockedBy: []
blocks: []
relatedPlans:
  - 260917-1139-vb6-call-capture-prediction
  - 260910-1400-csharp-roslyn-upgrade
  - 260821-1144-cplus-semantic-call-graph
  - 260807-1329-parser-quality-recovery
sources:
  - code-tiny/tools/vb/vb_common.py
  - code-tiny/tools/vb/vb_analyzer_base.py
  - code-tiny/tools/vb/vb_roslyn_adapter.py
  - code-tiny/tools/vb/roslyn_worker/Program.cs
  - code-tiny/tools/go/go_analyzer.py
  - code-tiny/tools/cplus/cplus_analyzer.py
  - code-tiny/tools/graph/schema/ladybug_schema.py
  - docs/plans/260917-1200-vb6-antlr-call-graph/research-digest.md
---

# VB6 ANTLR Call Graph — ProLeap Java Worker + Resolver + Evidence Plane

## Overview

Nâng pipeline VB6 từ **regex-only silent-degrade** (grammar `tree_sitter_vb6` không tồn tại trên PyPI; cây cú pháp không bao giờ được traverse — xem prediction report `260917-1139`) sang **ANTLR-first** với Java worker bọc [ProLeap vb6-parser](https://github.com/uwol/proleap-vb6-parser), cộng thêm lớp resolution theo project model VB6 và evidence plane hai tầng. Mục tiêu người dùng cuối: **cạnh "hàm A gọi hàm B" xuất hiện đúng trong Neo4j** cho các idioms VB6 điển hình (gọi Sub không ngoặc, `Call x`, nối dòng `_`, cross-module unqualified calls), phần không resolve tĩnh được ghi thành `POSSIBLE_CALLS` thay vì bị drop.

### Motivation (tóm tắt từ prediction report)

| Vấn đề hiện tại | Evidence |
|---|---|
| Tree-sitter không dùng thật, import grammar silently fails | `vb_common.py:466-474, 332-338`; `requirements.txt` thiếu package |
| `_CALL_RE` đòi `(` → miss `DoSomething 1, 2`, `MsgBox "x"`, line continuation | `vb_common.py:374, 544-566` |
| `resolve_calls` match tên tùy ý (`sorted(candidates)[0]`), không arity, không project model | `vb_common.py:1035-1059` |
| Call chưa resolve bị **drop** trước khi ghi graph | `vb_analyzer_base.py:748-754` |
| Class `symbol_id` thiếu `@rel_path` → cross-module ID collision | `vb_common.py:574-575` |
| `relations` luôn rỗng → không có CONTAINS class→method, không có IMPLEMENTS | `vb_common.py` (toàn file) |

### Research corrections (so với prediction report — từ research-digest)

1. **ProLeap KHÔNG có trên Maven Central** (404; author xác nhận issue #18: build bằng `mvn clean install` local). → Worker phải **vendor source pin v3.0.0** vào repo và build bằng Maven (vẫn đối xứng pattern `dotnet build` của RoslynVbWorker). MIT license, target JDK 17 (máy có JDK 26 + Maven 3.9.16).
2. **`.frm`/`.ctl` không tạo ASG module** trong ProLeap (`VbModuleVisitorImpl.visitModule` chỉ nhận `.bas/.cls`; issue #20). Grammar parse được designer block → chiến lược **materialize `.frm/.ctl` thành temp `.cls`** trước khi đưa vào worker (phải chứng minh trong spike). `.dsr` GUID block lỗi parse (issue #21) — skip file, đánh dấu fallback. `#If` conditional compilation cần preprocess (issue #5).

### Architecture

```text
vb_analyzer_base.py (build_call_graph)
    │
    ├── dialect == "vb6" AND engine != "regex"
    │   └── _parse_vb6_with_antlr_batch()          [mirror _parse_vbnet_with_roslyn_batch:430-508]
    │       ├── vb6_antlr_adapter.py                [mirror vb_roslyn_adapter.py]
    │       │   ├── thread-lock build cache → mvn -q package (vendor/proleap pinned)
    │       │   ├── temp JSON manifest {"files":[...], "project": "...vbp"}
    │       │   ├── subprocess: java -jar vb6-antlr-worker.jar --manifest ... --file-timeout-ms N
    │       │   └── stdout: 1 JSON doc, per-file {file_path, ok, payload|error}
    │       │       Vb6AntlrWorker (Java)
    │       │       ├── đọc .vbp → module list (Module=/Class=/Form=/Object=)
    │       │       ├── .frm/.ctl → temp .cls (strip designer block trước Attribute VB_Name)
    │       │       ├── VbParserRunnerImpl.analyzeFiles(toàn bộ files của project)
    │       │       │   → 1 Program cross-module (type/decl/expr multi-pass)
    │       │       ├── ASG walk → FunctionDef/CallEdge payload (module name = Attribute VB_Name)
    │       │       ├── call rows: caller_id, callee_name, callee_id (nếu SubCall.getSub() != null),
    │       │       │  callee_arity, call_line, call_type, resolution_status
    │       │       └── per-file timeout → ok=false, error="timeout after Nms"
    │       └── per-file fallback → parse_vb_file(regex) với fallback_reason     [cascade như roslyn→regex]
    │
    ├── Resolution layer (python, engine-independent — chạy cho MỌI engine)
    │   ├── ModuleRegistry: .vbp + Attribute VB_Name + visibility Public/Private
    │   ├── resolve: exact-qualified → module-local → project-public (unique) → arity match
    │   ├── ambiguous (nhiều đích) / late-bound (As Object) / không tìm thấy
    │   │   → KHÔNG drop: CallEdge.resolution_status ∈ {ambiguous, late_bound, unresolved}
    │   └── ASG-resolved (từ worker) giữ nguyên callee_id (không phải direct_resolved — xem AD-04)
    │
    ├── Publication two-tier
    │   ├── CALLS       : callee_id xác định duy nhất (worker ASG hoặc resolver unique)
    │   ├── POSSIBLE_CALLS : mọi call yếu, site-keyed (site_id = uuid5), props
    │   │                 {line, arity, call_type, resolution_status, resolution_class}
    │   └── schema đã sẵn sàng — ladybug_schema.py:306-307 duyệt CALLS + POSSIBLE_CALLS,
    │       CORE_REL_COLUMNS có sẵn resolution_status/resolution_class/site_id/line → KHÔNG đổi schema
    │
    └── Graph integrity (chung)
        ├── Class symbol_id thêm @rel_path (khớp scheme Function)
        ├── cạnh CONTAINS class→method; cạnh IMPLEMENTS từ Implements/Inherits
        └── cleanup node Class cũ theo file trong incremental cleanup
```

### Architecture Decisions (AD)

| # | Quyết định | Lý do / alternative bị bác |
|---|---|---|
| AD-01 | **D2: Java worker bọc ProLeap**, vendor source pin `3.0.0` (không phải git submodule — repo vừa remove subproject tree-sitter-swift; không phải Maven Central — không có) | D1 (antlr4-python3-runtime) dính GIL với ThreadPoolExecutor hiện tại + phải tự viết lại cả lớp ASG resolution mà ProLeap đã có (calls cross-module, implicit calls) |
| AD-02 | **Whole-program batching:** mọi sync (full + incremental) đưa TOÀN BỘ files của project vào một lần `analyzeFiles` | Nếu chỉ gửi cache-miss files, Program thiếu module → cross-module resolution (chính là bug cần sửa) lại hụt. Java+ANTLR đủ nhanh; payload cache vẫn lọc được file không đổi để khỏi re-embed/re-write |
| AD-03 | **`.frm`/`.ctl`/`.pag` → temp `.cls`** (strip designer block trước `Attribute VB_NAME`), module name lấy từ `Attribute VB_Name` — `.pag` nằm trong `_SOURCE_EXTS["vb6"]` (`vb_analyzer_base.py:71`) nên phải materialize như .frm/.ctl (red-team F7) | ProLeap issue #20: không tạo ASG module cho .frm/.ctl. Grammar parse OK → materialize là đường ít xâm nhập nhất. Phải spike-chứng minh |
| AD-04 | **`resolution_class` chỉ dùng vocab chuẩn** `call_evidence.RESOLUTION_CLASSES` — VB6 dùng `lexical_candidate` (POSSIBLE_CALLS) / `direct_resolved` KHÔNG dùng; chi tiết vb6 nằm ở `resolution_status` (free-text, không bị enforce) (red-team F2) | `enforce_strong_call_row` raise ValueError với vocab lạ (`call_evidence.py:200-202`); `write_relations_typed` không enforce → vocabulary drift giữa 2 lane nếu tự chế giá trị |
| AD-05 | **Regex demote thành fallback per-file**, giữ nguyên hành vi hiện tại làm engine `regex` và fallback cascade | Pattern roslyn→regex đã chứng minh ở `vb_analyzer_base.py:430-485`; xóa regex sẽ phá dialects vba/vbscript dùng chung `parse_vb_file` |
| AD-06 | **Resolver là tầng python engine-independent** đặt cạnh `resolve_calls` hiện tại (thay thế nó cho vb6, guard dialect cho các dialect khác không regress) | 4/5 workstream engine-independent; resolver phải hoạt động cả khi engine=regex fallback |
| AD-07 | **CLI:** `--vb6-parser-engine auto\|antlr\|regex` (mặc định `auto` = antlr nếu java+vendor build OK, else regex + WARNING rõ ràng) | Mirror `--vbnet-parser-engine`; sửa silent-degrade: degrade phải thành WARNING + parse_meta.engine phản ánh thật |
| AD-08 | **Bump `PARSE_CACHE_VERSION` ở phase-03** (merge đầu tiên đổi payload — parse_meta engine fields), KHÔNG đợi phase-05; re-sync runbook kèm theo (red-team F6) | Cache cũ che kết quả mới (prediction report E11); để muộn hơn sẽ có cửa sổ mixed-engine hydration |
| AD-09 | **Incremental edge re-publication:** khi incremental, re-publish MỌI cạnh CALLS/POSSIBLE_CALLS có source HOẶC target thuộc changed set — không chỉ cạnh xuất phát từ file đổi (red-team F1-CRITICAL) | `cleanup_neo4j_for_files` DETACH DELETE node theo `file_path IN changed` (`incremental_cleanup.py:70-84`) làm mất cả cạnh incoming từ caller không đổi; nếu chỉ write payload file đổi thì cạnh đó không được dựng lại → graph suy biến dần mỗi sync |
| AD-10 | **VB6 interface class** (class chỉ là đích `Implements`) được emit thêm như `InterfaceDef` → cạnh IMPLEMENTS (Class→Interface) khớp `_REL_SPECS` (`ladybug_schema.py:374`); fixture phải có `IShip.cls` thật (red-team F5) | Không có Interface node thì IMPLEMENTS bị `_OPTIONAL_EXTERNAL_RELATION_TYPES` bỏ qua im lặng — gate phase-06 không thỏa được |

### Scope Challenge (3 câu — trả lời từ prediction report + owner)

1. **Giải vấn đề gì?** Call graph VB6 thiếu/sai cạnh A→B trong Neo4j/Qdrant. KHÔNG phải rewrite parser cho cả họ VB.
2. **In/Out?** IN: dialect vb6 (extraction engine ANTLR, resolver, evidence plane, Class-ID fix, relations, observability, fixtures/benchmark, cache bump + re-sync runbook, sửa README drift). OUT (defer): vba/vbscript giữ đường hiện tại (không regress), vbnet/roslyn (owner: plan 260910-1400), message_scan (chỉ verify không vỡ contract), schema graph (đã sẵn sàng — không đổi).
3. **Biết khi nào xong?** Acceptance metrics ở bảng dưới — mọi mục đo được bằng fixture/benchmark/graph_mcp.

### Acceptance Metrics (định lượng)

| # | Metric | Gate |
|---|---|---|
| M1 | Fixture golden tests: gọi Sub không ngoặc, `Call x`, line continuation, cross-module unqualified, `MsgBox "x"` | 100% cạnh kỳ vọng xuất hiện trong payload + graph |
| M2 | Recall trên tập **expected-resolvable** callsites của `expected.json` (sample denominator: mọi case kỳ vọng `resolved` — external/late_bound/ambiguous không vào mẫu số) | ≥80%; và 100% call sites có CALLS hoặc POSSIBLE_CALLS (0 drop) (red-team F4 — mẫu số cũ无功 với fixture giàu case đặc biệt) |
| M3 | Worker parse-success trên fixture (kể cả .frm) | ≥95% file ok=true |
| M4 | Per-file amortized parse throughput, đo trên corpus ≥100 files (fixture replicate programmatically), tách riêng JVM startup budget | throughput < 2x regex per-file; JVM startup ≤ 5s (red-team F9 — đo trên 7-file fixture bị chi phối bởi fixed cost) |
| M5 | `graph_mcp.find_callers` / `trace_flow` trên hàm mẫu fixture | Trả về caller kỳ vọng |
| M6 | Sync log mặc định (không --verbose) | In engine thật + resolution rate + số fallback |
| M7 | Test suite hiện có (vb-related + common registry) | Không regress |

### Phase Index

| Phase | Tên | Gate ra |
|---|---|---|
| [01](phase-01-fixture-baseline.md) | Fixture corpus + baseline + golden tests | Baseline metrics ghi nhận (số 0 tham chiếu); expected.json chốt |
| [02](phase-02-worker-spike.md) | Vendor ProLeap + Java worker spike | M3, M4 trên fixture; .frm→temp.cls chứng minh; contract test protocol |
| [03](phase-03-engine-integration.md) | Adapter + engine dispatch + fallback cascade | `--vb6-parser-engine` hoạt động; M1 ở mức payload; M6 |
| [04](phase-04-resolver-evidence.md) | Resolver + two-tier publication + graph integrity | M2; Class-ID/CONTAINS/IMPLEMENTS; M5 |
| [05](phase-05-cache-observability-resync.md) | Cache bump + observability + re-sync runbook | AD-08; M6 hoàn chỉnh; runbook dry-run |
| [06](phase-06-verification.md) | Verification toàn diện + benchmark + docs | M4 benchmark chính thức; M7; README sửa drift; red-team verdict xử lý |

### Risks (từ digest, cập nhật theo)

| Rủi ro | Severity | Mitigation |
|---|---|---|
| ProLeap build-from-source hỏng trên JDK 26 (target 17) | Medium | Spike phase-02 blocker đầu tiên; nếu hỏng → pin JDK 17 qua toolchain Maven (`maven.compiler.release`) hoặc docs yêu cầu JDK 17 |
| `.frm`→temp `.cls` vẫn thiếu ASG (workaround không works) | High | Gate phase-02; fallback: strip designer + parse code-section bằng regex như hiện tại cho .frm (chỉ .frm), phần còn lại vẫn qua ANTLR |
| Cross-module resolution vs parse cache (AD-02 violation) | High | Code review checklist: worker LUÔN nhận toàn bộ files; cache chỉ lọc output |
| `#If` conditional compilation parse error | Medium | Preprocess `#If` bỏ nhánh false (spike xác định); file lỗi → per-file fallback + parse_meta.fallback_reason |
| `.dsr` GUID block | Low | Skip + đánh dấu skipped_files trong parse_meta |
| Worker timeout/Java thiếu trên máy deploy | Medium | auto→regex cascade + WARNING; doctor/docs note (phase-05) |
| Regress vba/vbscript/vbnet do refactor `vb_common` | Medium | Guard dialect trong resolver; test registry hiện có phải xanh |
| Incremental sync cắt cạnh incoming từ file không đổi (AD-09) | **High→đã mitigate bằng design** | Edge re-publication theo source-or-target; phase-06 assert incoming edge |
| `resolution_class` tự chế làm write crash hoặc drift lane (AD-04) | High | Chỉ dùng vocab chuẩn; test ghi edge thật qua `write_possible_calls_with_site` |
| 1 file malformed abort cả batch ProLeap (per-file timeout bất khả thi trong 1 Program) | High | `ignoreSyntaxErrors=true` + workspace timeout + fixture malformed file (phase-02) |
| Placeholder external_symbol Function node tích tụ qua các sync | Medium | Reconciliation: xóa placeholder không còn cạnh nào trỏ tới sau mỗi sync; phase-06 assert non-accumulation (red-team F8) |

### Open Questions (validate — mặc định đã chọn, owner có thể override)

| # | Câu hỏi | Mặc định |
|---|---|---|
| Q1 | Có đưa resolver/evidence cho vba/vbscript ngay không? | Không — chỉ vb6; dialect khác giữ `resolve_calls` cũ (guard) |
| Q2 | Vendor ProLeap ở đâu? | `code-tiny/tools/vb/antlr_worker/vendor/proleap-vb6-parser/` (source pin v3.0.0, không submodule) |
| Q3 | Incremental sync vẫn chạy worker toàn project? | Có (AD-02) — đánh đổi worker cost cho đúng đắn cross-module; đo ở M4 |
| Q4 | Java bắt buộc trên môi trường deploy? | Không bắt buộc — `auto` fallback regex + WARNING; tài liệu hóa trong README + doctor note |
| Q5 | `.frm` designer block strip ở worker (Java) hay adapter (Python)? | Adapter Python (trước khi viết manifest) — giữ worker gọn, tái dùng cho cả regex fallback |
| Q6 | Interface VB6 (class thường làm đích Implements) emit như thế nào trong graph? (F5/AD-10) | Emit song song `InterfaceDef` cho class-chỉ-là-đích-Implements → cạnh IMPLEMENTS khớp schema (Class→Interface) |
| Q7 | Placeholder node cho external/late-bound callee: giữ hay bỏ? (F8) | Giữ + lifecycle reconciliation (xóa khi hết cạnh trỏ tới sau mỗi sync); assert non-accumulation ở phase-06 |

### Cross-Plan Notes

- **260910-1400-csharp-roslyn-upgrade** (in_progress): cùng vùng `tools/vb` nhưng **disjoint files** (họ đụng `vb_roslyn_adapter.py`/`roslyn_worker/`; plan này đụng `vb_common.py`/`vb_analyzer_base.py`/files mới `antlr_worker/`). Điều phối: không đổi chữ ký `parse_vbnet_files_with_roslyn`; đặt CLI flag vb6 theo cùng quy ước `--vb6-*`.
- **260821-1144-cplus-semantic-call-graph**: mượn semantic-call publication pattern (site_id, resolution_class) — không đụng file.
- **260909-0854-ignore-folders-config** (active): không đụng `_SKIP_DIRS`.

### Delegation Receipts

- `[delegate] role=researcher mode=spawn scope="VB6 ANTLR/ProLeap + repo contracts digest" status=done` → `research-digest.md`
- `[delegate] role=reviewer mode=spawn lens=assumptions+failure-modes scope="plan red-team" status=done` → 10 findings (1C/5H/4M), disposition trong `red-team.md`; plan đã cập nhật F1-F10
