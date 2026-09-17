# Phase 02 — Vendor ProLeap + Java Worker Spike (timebox 3 ngày)

> Gate ra: M3 (parse-success ≥95% fixture kể cả .frm), M4-spike (throughput <2x regex), .frm→temp.cls được chứng minh, worker protocol contract test xanh. **Spike fail → dừng, báo owner, không wire engine.**

## Tasks

### 2.1 Vendor ProLeap source

```text
code-tiny/tools/vb/antlr_worker/
├── vendor/proleap-vb6-parser/     # source pin v3.0.0 (không submodule — precedent remove tree-sitter-swift)
│   ├── pom.xml                     # io.github.uwol:proleap-vb6-parser:3.0.0, deps ANTLR 4.7.2 + SLF4J 2.0.9
│   └── src/...                     # giữ nguyên, LICENSE kèm theo (MIT)
├── pom.xml                         # vb6-antlr-worker: deps proleap + gson|jackson
└── src/main/java/.../Vb6Worker.java
```

- Tải source tag v3.0.0 từ GitHub, copy vào vendor, ghi `VENDOR.md` (source URL, commit/tag, ngày, lý do vendoring — Maven Central không có).
- **Blocker đầu tiên:** build trên JDK 26. Nếu `mvn package` vendor fail (target 17 vs JDK 26), đặt `maven.compiler.release=17` và/hoặc cấu hình Maven toolchains; ghi kết quả vào spike report.

### 2.2 Worker `Vb6Worker.java` — hợp đồng mirror `roslyn_worker/Program.cs`

Input/Output (giữ nguyên contract Roslyn để tái dùng maximum pattern Python-side):

- argv: `--manifest <path> --workspace-timeout-ms <n>` — **per-file timeout BẤT KHẢ THI** trong 1 Program multi-pass duy nhất (red-team F3): dùng workspace-level timeout cho cả `analyzeFiles` + `ignoreSyntaxErrors` (cấu hình `VbParserParams` — verify API name trong spike) để 1 file malformed không abort batch; per-file error attribution qua error listener gom theo file
- manifest JSON: `{"root": "...", "project": "path/to.vbp", "files": ["rel/path1.bas", ...]}` — **files = TOÀN BỘ project files** (AD-02)
- stdout: MỘT JSON doc: `{"files": [{"file_path": "...", "ok": true, "payload": {...}} | {"file_path": "...", "ok": false, "error": "..."}], "worker_meta": {...}}`
- payload keys: đúng shape `_is_valid_payload_shape` đòi: `functions, calls, classes, file_def, parse_meta` (+ namespaces/relations/properties/events/interfaces/enums/constants/variables rỗng được)
- call row: `{caller_id, caller_scope, callee_name, callee_id?, callee_arity?, call_line, call_type, resolution_status}` — `callee_id` điền khi `SubCall.getSub()`/`FunctionCall.getFunction()` non-null; `resolution_status ∈ {asg_resolved, external, undefined}`
- per-file timeout: executor + future timeout → `ok=false, error="timeout after Nms"`

### 2.3 Xử lý .vbp + .frm trong worker (hoặc adapter — theo Q5 mặc định adapter)

- **Adapter Python** (`vb6_antlr_adapter.py` — bắt ở phase 03 nhưng spike viết bản nháp): đọc `.vbp` lấy module list; với mỗi `.frm/.ctl`: strip mọi dòng TRƯỚC dòng `Attribute VB_Name` đầu tiên (designer block), viết temp `.cls` vào cache dir, manifest trỏ tới temp file nhưng `file_path` trả về path gốc.
- Worker: `VbParserRunnerImpl.analyzeFiles(các File .bas/.cls — gồm temp .cls)`.
- Module name: ProLeap lấy từ `Attribute VB_Name` (verify trong spike bằng fixture `modMain.bas` có name ≠ filename).
- `#If`: preprocess bỏ nhánh `#Else`/false (nếu spike thấy cần — kiểm tra fixture có `#If` không; thêm 1 case vào corpus nếu quyết làm).

### 2.4 ASG walk → payload

- `Module.getSubs()/getFunctions()/getPropertyGets/Sets/Lets` → FunctionDef (qualified = `ModuleName.ProcName`, symbol_id `{qualified}/{arity}@{relPath}` — khớp scheme regex hiện tại để cache hydrate không vỡ).
- `Procedure.getCalls()` → CallEdge: dùng `Call.getCallType()` (22 enum) map `call_type`; line từ `getCtx().getStart().getLine()`.
- Với `VARIABLE_CALL/UNDEFINED_CALL`: vẫn phát call row (`resolution_status=undefined`) — không drop.

### 2.5 Spike report + contract test

- `docs/plans/260917-1200-vb6-antlr-call-graph/spike-report.md`: M3/M4 số liệu, JDK build outcome, .frm materialize outcome, các issue gặp (link ProLeap issue).
- `tests/test_vb6_antlr_worker_contract.py`: build-if-needed → chạy worker trên fixture corpus → assert schema JSON (keys, types), parse-success, và **ít nhất 1 cạnh A→B cross-module có callee_id** (case `CalcTotal`).

## Định nghĩa xong

- [ ] `mvn package` xanh (vendor + worker) trên JDK 26, build idempotent + locking
- [ ] Worker chạy corpus fixture: ≥95% file ok (trừ `malformed.bas` được đánh dấu ok=false có error), .frm có payload
- [ ] `malformed.bas` KHÔNG làm chìm batch — các file khác vẫn có payload (red-team F3)
- [ ] Throughput đo per-file amortized trên corpus ≥100 files (replicate fixture), JVM startup tách riêng (M4/F9)
- [ ] Cross-module `CalcTotal` có callee_id từ ASG
- [ ] Contract test xanh; spike-report.md có số
- [ ] Throughput ghi nhận (so regex baseline cùng corpus)
