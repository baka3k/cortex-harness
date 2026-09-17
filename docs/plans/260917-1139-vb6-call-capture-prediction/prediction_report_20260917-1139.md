# Prediction Report — VB6 Call-Capture Overhaul (tree-sitter/regex → có nên thêm ANTLR?)

- **Ngày:** 2026-09-17 11:39
- **Depth:** deep
- **Verdict:** ⚠️ **CAUTION** — tiến hành theo phương án phân tầng (staged hybrid); ANTLR là Phase 2 sau bake-off, không phải Phase 1.
- **Proposal:** Parser VB6 hiện chỉ dùng tree-sitter + regex, thiếu nhiều quan hệ (hàm A gọi hàm B không capture được). Cân nhắc toàn bộ giải pháp, kể cả thêm ANTLR.
- **Constraint (xác nhận bởi owner):** **Roslyn chỉ parse được VB.NET, không dùng được cho VB6.** VB6 phải có track xử lý riêng, tách khỏi `vb_roslyn_adapter.py` / `--vbnet-parser-engine`. Roslyn worker trong repo chỉ được mượn làm **architectural pattern** (engine cascade CLI, subprocess worker + JSON protocol, timeout handling) — không phải engine cho VB6.

---

## 1. Executive Summary

Điều tra mã nguồn cho thấy vấn đề **nghiêm trọng hơn và khác hơn** so với mô tả: pipeline VB6 hiện tại **không hề dùng tree-sitter** — grammar `tree_sitter_vb6` không tồn tại trên PyPI, chưa được cài, và kể cả khi cài thì cây cú pháp chỉ được dùng để đếm error node (`vb_common.py:466-474`), toàn bộ extraction là regex theo dòng. Regex `_CALL_RE` (`vb_common.py:374`) yêu cầu dấu `(` sau identifier nên **bỏ sót toàn bộ idioms cốt lõi của VB6**: gọi Sub không ngoặc (`DoSomething 1, 2`, `MsgBox "x"`), statement nối dòng bằng `_`, và bỏ rơi call không resolve được thay vì ghi thành POSSIBLE_CALLS. Năm persona đồng thuận: sửa extraction + xây lớp resolution theo mô hình project VB6 + evidence plane là việc bắt buộc; ANTLR (grammar `grammars-v4/vb6` chuẩn tham khảo) đáng để đưa vào như engine Phase 2 sau khi chứng minh bằng fixture, vì grammar tree-sitter VB6 duy nhất tồn tại chỉ là scaffold 1 commit.

## 2. Evidence Context (Phase 0)

| # | Phát hiện | Vị trí |
|---|-----------|--------|
| E1 | Tree được parse nhưng **không bao giờ traverse**; chỉ lấy `has_error`/`error_nodes`; `parse_meta` hardcode `parser_engine:"regex"` | `code-tiny/tools/vb/vb_common.py:466-474, 1014-1030` |
| E2 | `import tree_sitter_vb6` luôn fail: **không có trên PyPI**, không trong `requirements.txt`; README claim ngược lại (doc drift). Chỉ `tree-sitter-vb-dotnet` tồn tại | `code-tiny/tools/vb/vb_common.py:332-338`, `requirements.txt` |
| E3 | `_CALL_RE = r"\b([A-Za-z_][A-Za-z0-9_.]*)\s*\("` — bắt buộc có `(`; **miss call không ngoặc** (idiom chuẩn VB6); scan theo từng dòng nên **miss line continuation `_`**; string literal không strip → false positive | `vb_common.py:374, 544-566` |
| E4 | `resolve_calls`: chỉ matching theo tên; ambiguous thì lấy `sorted(candidates)[0]` **tùy tiện**; `callee_arity` không bao giờ điền/dùng; không có project model VB6 (.vbp, `Attribute VB_Name`, form default instance, `Implements`) | `vb_common.py:1035-1059` |
| E5 | Call chưa resolve bị **drop trước khi ghi graph** (`if call.callee_id:`) — không có POSSIBLE_CALLS fallback, ngược với precedent `go_analyzer.py:826-836` và cplus call-evidence two-plane | `vb_analyzer_base.py:748-754` |
| E6 | `relations` luôn rỗng → không có cạnh class→method CONTAINS, không có cạnh `Implements`/`Inherits` (base_interfaces có thu nhưng không ghi edge) | `vb_common.py` (toàn file), `vb_analyzer_base.py:740-746` |
| E7 | `ClassDef.symbol_id` = qualified name **không có `@rel_path`** → hai class trùng tên ở module khác collide thành 1 node Neo4j (trong khi Function id có `@rel_path`) | `vb_common.py:574-575` |
| E8 | `.frm/.ctl`: không xử lý metadata header, không dùng `Attribute VB_Name` làm module name (trong VB6 caller dùng **tên module** để qualify, không phải tên file) | `vb_common.py` (toàn file) |
| E9 | Precedent trong repo: VB.NET có Roslyn worker (`--vbnet-parser-engine auto\|roslyn\|regex`), cplus có clang worker + evidence merge, go có POSSIBLE_CALLS; graph layer hỗ trợ edge có count/props | `vb_roslyn_adapter.py`, `tools/cplus/*`, `tools/go/go_analyzer.py` |
| E10 | Toolchain máy này: Java 26 (ANTLR tool OK), .NET 10, tree_sitter 0.26, venv quản lý bằng uv. Grammar ngoài: tree-sitter-vb6 (joannefan) = 1 commit/0 star/frm parse kém; **ANTLR grammars-v4/vb6** (bắt nguồn ProLeap uwol) = dẫn xuất từ MS language reference, test trên MSDN + apps thực tế | Web research 2026-09-17 |
| E11 | Parse cache cần bump `PARSE_CACHE_VERSION` (`vb-family-v2026-04-03-2`) khi đổi extraction, nếu không cache cũ che mất cải thiện | `vb_common.py:11` |

## 3. Agreements (4+ persona đồng thuận)

1. **Nguyên nhân gốc không nằm ở công nghệ parser** mà ở: tree không được dùng (E1), grammar không cài được (E2), extraction regex sai idioms VB6 (E3), thiếu lớp resolution theo project model (E4), và drop call chưa resolve (E5).
2. **Bắt buộc tách 2 tầng:** extraction (cú pháp — tìm call site) và resolution (ngữ nghĩa — binds callee). ANTLR/tree-sitter chỉ sửa được extraction.
3. **Call chưa/ambiguously-resolved phải ghi thành POSSIBLE_CALLS** kèm `resolution_status` thay vì drop — tái dùng pattern go/cplus đã có (E5, E9).
4. **Phải có fixture corpus VB6 + golden-file tests** trước khi chọn engine (repo đã có pattern này cho cobol/cplus/aspnet).
5. **Sửa symbol_id scheme cho Class** (thêm `@rel_path`) và bổ sung cạnh CONTAINS/Implements trước khi đổ call graph mới — nếu không resolution sẽ bind sai (E7, E6).

## 4. Conflicts

| Chủ đề | Architect | Security | Performance | UX | Devil's Advocate | Resolution (kèm lý do) |
|---|---|---|---|---|---|---|
| Đưa ANTLR vào ngay Phase 1? | Không — engine phải pluggable, chọn sau bake-off | Không ý kiến trực tiếp | Không — đo trước | Không — consumer cần edge đúng hơn nhiều | **Chống** — chưa chứng minh regex+resolver không đủ | **ANTLR = Phase 2 sau bake-off.** Lý do: missing calls chủ yếu do no-paren/continuation/ambiguity — sửa được bằng regex VB6-aware + symbol table; nhưng tree-sitter VB6 grammar thực tế là scaffold (E10) nên nếu cần AST thật thì ANTLR grammars-v4 là lựa chọn trưởng thành duy nhất — giữ kẻ-it-rủi-ro-nhất nhưng không mua trước khi cần |
| ANTLR runtime: Python in-process hay Java worker? | Python trước, worker nếu cần | Ưu tiên vendored grammar + generated code commit | Python antlr4 runtime chậm 10-50x tree-sitter C; lo full-sync repo lớn | Không ý kiến | Phức tạp hóa ops | **Python in-process + parse cache + benchmark bắt buộc.** Lý do: Roslyn worker precedent cho thấy chi phí ops của subprocess worker; chỉ xây Java worker khi benchmark chứng minh vượt ngưỡng; cache + incremental (đã có) che phần lớn chi phí |
| Giữ và harden regex hay thay hẳn? | Giữ như fallback engine (mirror `auto\|regex` của vbnet) | Không ý kiến | Regex nhanh, giữ cho incremental | Không ý kiến | Regex VB6-aware là bước rẻ nhất, đáng thử trước | **Giữ regex như fallback bắt buộc** — đúng triết lý engine cascade đã có của repo (E9) |
| Edge ambiguous resolve kiểu nào? | POSSIBLE_CALLS + resolution_status | Đồng ý — tránh misleading security audit | Đồng ý | Đồng ý — hiển thị confidence | Đồng ý — edge sai hại hơn edge thiếu | **Đồng thuận tuyệt đối (5/5)** — không conflict thực sự |

## 5. Risk Summary

| Rủi ro | Severity | Persona | Mitigation |
|---|---|---|---|
| Graph VB6 hiện tại **im lặng sai/thiếu** (regex-only degrade không báo động) — mọi下游 phân tích đã dùng dữ liệu sai | **Critical** (mitigatable) | Architect, UX | Phase 1 sửa extraction; thêm metric resolution-rate in luôn (không chỉ `--verbose`); re-sync sau khi bump cache version |
| Class symbol_id collision → call bind sai class method, ghi đè node | **High** | Architect | Đổi id scheme thêm `@rel_path` + migration/cleanup node cũ trong `cleanup_neo4j_for_files` |
| ANTLR integration tốn 2-4 tuần mà .frm/late-binding vẫn không resolve vì tầng resolution không đổi | High | Devil's Advocate, Performance | Bake-off trên fixture trước; gate bằng metric % call resolved |
| Python antlr4 runtime chậm với repo VB6 lớn (forms thường rất to) | High | Performance | Benchmark bắt buộc trước adopt; parse cache + incremental; kế hoạch Java worker nếu vượt ngưỶ |
| False positive call từ string literal / form metadata block | Medium | Security, UX | Strip string literal khi scan; skip .frm metadata section trước khi extract |
| Supply chain: thêm runtime/grammar dependency | Medium | Security | Vendored .g4 + generated parser commit vào repo; pin `antlr4-python3-runtime`; không generate lúc runtime |
| Stale parse cache che mất cải thiện sau upgrade | Medium | Performance | Bump `PARSE_CACHE_VERSION` trong cùng PR đổi extraction (E11) |
| Late-bound (`As Object`) không thể resolve static | Medium (chấp nhận) | UX | Ghi POSSIBLE_CALLS với `resolution_status:"late_bound"` để downstream biết |

## 6. Per-Persona Detail

### 6.1 Architect — confidence: high
```yaml
concerns:
  - "Pipeline tự neutralize chính mình: parse tree rồi vứt (E1), import grammar không tồn tại được nuốt exception im lặng (E2) — bất kỳ engine nào cũng phải sửa observability trước"
  - "Engine mới phải nằm sau payload contract FunctionDef/CallEdge hiện có và mirror CLI '--vbnet-parser-engine' → '--vb6-parser-engine auto|antlr|regex' để graph/MCP contract không đổi"
  - "Extraction và resolution là 2 tầng khác nhau; user complaint phủ cả hai; chỉ thêm parser không sửa được resolution"
  - "Class symbol_id thiếu '@rel_path' (E7) + relations rỗng (E6) là bug cấu trúc phải sửa trước khi đổ call graph mới"
  - "Unresolved calls bị drop (E5) phá downstream trace_flow/find_callers — cần evidence plane POSSIBLE_CALLS như go/cplus"
recommendations:
  - "Phase 1: harden regex + VB6 project-model resolver + POSSIBLE_CALLS, không thêm dependency"
  - "Phase 2: bake-off ANTLR (grammars-v4/vb6) vs vendored tree-sitter-vb6 trên fixture corpus; chọn theo metric"
  - "Thêm per-file parse_meta: engine_thực_dùng, extraction_source, resolution_rate; in resolution-rate mặc định không chỉ verbose"
```

### 6.2 Security — severity: medium
```yaml
threats:
  - "Call edge SAI (bind tùy tiện sorted()[0]) làm impact analysis/security audit nhìn nhầm caller của hàm nhạy cảm — nguy hiểm hơn edge thiếu"
  - "False positive call từ nội dung string literal và .frm metadata có thể tạo đường gọi hão"
  - "Dependency mới (antlr4-python3-runtime, grammar bên thứ 3) mở supply-chain surface"
  - "Source code đầy đủ tiếp tục được embed vào Qdrant (note field) — hiện trạng, không rủi ro mới nhưng cần nhớ khi mở scope ingest"
severity: medium
mitigations:
  - "Bắt buộc resolution_status trên CALLS/POSSIBLE_CALLS để tool bảo mật biết độ tin cậy mỗi cạnh"
  - "Vendored grammar + generated parser commit vào repo, pin version runtime, không codegen lúc runtime"
  - "Ambiguous → POSSIBLE_CALLS nhiều đích thay vì chọn 1 đích sai"
```

### 6.3 Performance — metrics_impact: "parse throughput và % resolved sẽ đổi; wall-time tổng phụ thuộc tỉ trọng parse vs embed"
```yaml
bottlenecks:
  - "Python antlr4 runtime (ALL(*) interpreted) chậm hơn tree-sitter C ~10-50x; .frm VB6 thực tế rất lớn (metadata + code)"
  - "Hiện tại đã trả chi phí parse tree mà không dùng (sẽ thành waste thật khi cài grammar mà vẫn chỉ dùng regex)"
  - "Embedding jina-v3 thường chiếm majority wall-time — cải thiện parse có thể không đổi tổng thời gian trừ khi parse share lớn"
metrics_impact: "Mục tiêu: % call resolved tăng từ không-đo-được → ≥80% trên fixture; POSSIBLE_CALLS phủ phần còn lại; parse time/file phải < 2x regex baseline"
alternatives:
  - "Two-phase rẻ nhất: regex extraction VB6-aware + symbol-table resolution (không dependency mới)"
  - "ANTLR Python in-process + parse cache + incremental trước; Java worker (pattern ProLeap) chỉ khi benchmark vượt ngưỡng"
  - "Tái dùng benchmark harness precedent (benchmark_cplus_parse_quality.py) để đo trước khi chọn engine"
```

### 6.4 UX
```yaml
issues:
  - "Người chạy sync không biết parser đang degrade regex-only và bao nhiêu % call được resolve (chỉ thấy khi --verbose)"
  - "Downstream skills (hi-reverse, hi-repo-recon, graph_mcp trace_flow) nhận call graph thiếu/sai mà không có tín hiệu cảnh báo"
edge_cases:
  - "Call không ngoặc: 'DoSomething 1, 2' / 'MsgBox \"x\"' / 'Call InitData'"
  - "Line continuation: 'ProcessOrder _\\n    42' — regex theo dòng hiện miss hoàn toàn"
  - "Named args: 'Foo a:=1'; With-block implicit member call: '.Load'"
  - "Module name ≠ file name (Attribute VB_Name); form default instance 'Form1.Show'; Implements dispatch; late-bound 'As Object'"
  - ".frm metadata header; file thiếu newline cuối; trùng simple name giữa các module"
a11y_concerns: "N/A (CLI/MCP context) — nhưng sync summary nên in mặc định: engine dùng thật, error nodes, resolution rate"
```

### 6.5 Devil's Advocate
```yaml
assumptions_challenged:
  - "'Thiếu parser mạnh' — SAI ở thời điểm hiện tại: tree-sitter không cài được và cây không được dùng; chưa từng có AST trong pipeline này để kết luận regex/tree-sitter yếu"
  - "'tree-sitter-vb6 là option rẻ' — SAI: không có package PyPI, bản GitHub duy nhất là 1-commit scaffold với ambiguity array-vs-call và frm metadata parse kém"
  - "'Regex không thể capture call VB6' —_CHƯA CHỨNG MINH: cú pháp VB6 line-oriented lạ thường regex-friendly; miss hiện tại do pattern đòi '(' và scan từng dòng, không phải do bản chất regex"
  - "'Capture càng nhiều càng tốt' — edge sai tùy tiện hại downstream hơn edge thiếu có gắn nhãn POSSIBLE"
simpler_alternatives:
  - "Sửa 3 lỗi regex (no-paren, continuation merge, string strip) + module symbol table từ inventory FunctionDef hiện có + POSSIBLE_CALLS: ước 1-2 tuần, 0 dependency mới, có thể bắt ~90% các call A→B điển hình"
  - "Nếu vẫn cần AST sau khi đo: ANTLR grammars-v4 (trưởng thành) > tree-sitter vb6 (scaffold) — nhưng chỉ sau khi fixture chứng minh tầng regex+resolver còn hụt"
worst_case: "Team tốn 2-4 tuần tích hợp ANTLR + generated parser + Java/Python runtime, xong phát hiện .frm và late-binding vẫn không resolve vì tầng resolution không đổi; call graph chỉ cải thiện khiêm tốn; gánh maintenance grammar + invalidation re-sync toàn bộ"
```

## 7. Recommendations (numbered, kèm rationale)

1. **Phase 1 — Sửa extraction regex VB6-aware (không dependency mới).** Nhận dạng statement call không ngoặc, gộp dòng `_`, strip string literal, skip `.frm` metadata, dùng `Attribute VB_Name` làm module name. *Rationale: từng miss được định danh cụ thể (E3, E8); VB6 line-oriented nên khả thi cao; chi phí thấp nhất trên mỗi % callRecovered.*
2. **Phase 1 — Xây VB6 project-model resolver.** Parse `.vbp`; registry module + visibility (`Public`/`Private`); resolution order module-local → project-public → form default instance; match arity; ambiguous → nhiều đích. Điền `callee_arity`, dùng nó khi match. *Rationale: đây là tầng thực sự biến "tìm thấy call site" thành "hàm A gọi hàm B" (E4).*
3. **Phase 1 — Evidence plane:** call chưa resolve/ambiguous/late-bound → POSSIBLE_CALLS với props `line, arity, resolution_status` theo pattern go (E5, E9); đổi drop-im-lặng tại `vb_analyzer_base.py:748-754`. *Rationale: đồng thuận 5/5 persona; downstream có tín hiệu thay vì silence.*
4. **Phase 1 — Sửa data-integrity trước khi đổ graph mới:** Class symbol_id thêm `@rel_path` (E7); sinh cạnh CONTAINS class→method và IMPLEMENTS/INHERITS (E6). *Rationale: resolution đúng yêu cầu ID không collide.*
5. **Phase 1 — Observability:** `parse_meta` ghi engine thật; sync summary in resolution-rate mặc định; surface E2 (grammar-missing) thành lỗi/rảnh cảnh báo thay vì `except: pass`. *Rationale: chống tái diễn silent-degrade.*
6. **Phase 2 — Bake-off engine trên fixture corpus VB6 (bao gồm .frm thực):** ANTLR `grammars-v4/vb6` (Python target, vendored + generated code commit) vs vendored `tree-sitter-vb6`; gate bằng metric ≥80% resolved + parse time < 2x regex. Chỉ giữ engine thắng và chỉ khi Phase 1 vẫn hụt các dạng call hiếm. *Rationale: quyết định bằng chứng thay vì bằng cảm tính; ANTLR giữ vị thế option trưởng thành duy nhất nếu cần AST (E10).*
7. **Phase 2 (điều kiện)** — nếu ANTLR thắng mà benchmark full-sync vượt ngưỡng: Java worker subprocess theo pattern Roslyn worker đã có. *Rationale: tái dụng ops pattern; tránh xây sớm.*
8. **Bump `PARSE_CACHE_VERSION` trong cùng PR thay đổi extraction** (E11) và lên kế hoạch re-sync các project VB6 đã ingest. *Rationale: cache cũ sẽ che kết quả mới.*

## 8. Next Steps (CAUTION → xử lý mitigations trước khi code)

1. Xây fixture corpus VB6 nhỏ (module, class, form, no-paren calls, continuation, Implements, late-bound) + golden-file test — theo pattern `tests/test_cobol_*`/`fixtures`.
2. Chạy baseline: đo % call resolved hiện tại trên fixture (số 0 tham chiếu).
3. Triển khai Phase 1 (recommendations 1-5), benchmark lại, so baseline.
4. Quyết định Phase 2 bằng số liệu bake-off; nếu chọn ANTLR → đi `hi-plan` cho integration chi tiết (runtime target, vendoring, cache, rollout).
5. Sau khi merge: bump cache version, re-sync, xác minh bằng `graph_mcp.find_callers` trên hàm mẫu.

## 9. Addendum (2026-09-17) — Kịch bản "ưu tiên ANTLR trước" (owner hỏi lại)

**Câu hỏi:** Đảo thứ tự — đưa ANTLR làm Phase 1 thay vì regex hardening thì sao?

**Kết luận delta:** **Khả thi và về kinh tế dài hạn có phần tốt hơn** — với điều kiện. Lý do cốt lõi: 4/5 workstream của Phase 1 cũ là **engine-independent** (resolver, evidence plane, Class-ID fix, observability); ANTLR-first chỉ hoán đổi workstream (a) "harden regex" thành "tích hợp ANTLR làm engine chính". Tránh được rủi ro **xây tầng extraction 2 lần**.

### 9.1 Bằng chứng mới hỗ trợ ANTLR-first

| # | Phát hiện | Nguồn |
|---|-----------|-------|
| A1 | Grammar ANTLR VB6 có sẵn construct cho call không ngoặc: `implicitCallStmt_InStmt`, `iCS_S_VariableOrProcedureCall` — **đúng idioms regex đang miss**; ASG còn có variable access | ProLeap README/AST |
| A2 | ProLeap là thư viện Java trưởng thành: 293 commits, v3.0.0 trên Maven Central (`io.github.uwol:proleap-vb6-parser`), JDK 17 | GitHub uwol |
| A3 | **Đường triển khai đối xứng với Roslyn worker đã có:** VB.NET dùng .NET worker (RoslynVbWorker) → VB6 có thể dùng **Java worker bọc ProLeap**, tái dụng nguyên pattern subprocess + JSON + timeout + fallback per-file. Không dính GIL | `vb_roslyn_adapter.py` |
| A4 | `antlr4-python3-runtime` là pure Python → GIL-bound: ThreadPoolExecutor hiện tại **không có speedup** cho parse CPU-bound (khác tree-sitter là C extension). Nếu đi đường Python in-process phải đổi sang ProcessPoolExecutor (refactor nhỏ: `parser_factory` callable không pickle được, phải truyền dialect string) | Phân tích kỹ |
| A5 | ProLeap không nói rõ xử lý `.frm` designer metadata → vẫn cần text pre-filter tách khối designer trước khi parse (rẻ, cần cho mọi engine kể cả regex) | ProLeap README |

### 9.2 Hai biến thể triển khai ANTLR

| Biến thể | Ưu | Nhược | Khi nào chọn |
|---|---|---|---|
| **D1: grammars-v4 .g4 + antlr4-python3-runtime** | In-process, không toolchain phụ, debug dễ | Chậm (ALL(*) interpreted), GIL (A4), tự viết toàn bộ listener→payload | Repo nhỏ, spike nhanh |
| **D2: Java worker bọc ProLeap** (khuyến nghị) | Thư viện trưởng thành có sẵn ASG + call extraction (A1, A2), nhanh (JVM), đối xứng Roslyn worker (A3), batch 1 lần khởi động JVM | Ops worker thứ 2 (build/publish), phụ thuộc bảo trì ProLeap (pin version) | Mục tiêu production |

### 9.3 Rủi ro đổi theo (so với bảng mục 5)

| Rủi ro | Severity | Mitigation |
|---|---|---|
| Timeline extraction: regex ~2-4 ngày → ANTLR ~1.5-3 tuần | Medium | Chấp nhận đổi lấy "build once"; timebox spike 2-3 ngày trước khi wire toàn bộ |
| D1 GIL/throughput | High (nếu chọn D1) | Chọn D2 (Java worker) làm mục tiêu; D1 chỉ dùng spike |
| ProLeap ngừng bảo trì | Medium | Pin `3.0.0`, vendor grammar gốc (.g4 từ grammars-v4) làm phương án tự generate |
| Grammar không cover construct lạ | Medium | Cascade per-file fallback về regex (pattern `roslyn→regex` đã có ở `vb_analyzer_base.py:430-485`) |
| Nhảy cóc không đo baseline → không chứng minh cải thiện | **Critical** (tránh) | Fixture corpus + baseline metric là gate bắt buộc TRƯỚC spike (rẻ, 1-2 ngày) |
| Quên rằng extraction ≠ resolution | **Critical** (tránh) | Resolver + POSSIBLE_CALLS + Class-ID fix phải cùng scope PR/kế hoạch |

### 9.4 Kế hoạch sửa đổi nếu chọn ANTLR-first

1. **Ngày 1-2:** Fixture corpus VB6 + golden tests + baseline (% call resolved hiện tại). Song song: quyết định D1-spike hay D2 thẳng.
2. **Ngày 3-5:** Spike timebox — D1 parse fixture đo parse-success/throughput; nếu chọn D2: dựng skeleton Java worker (copy pattern RoslynVbWorker), gọi ProLeap parse fixture. **Gate:** extraction parse-success ≥95% trên fixture, throughput chấp nhận được.
3. **Tuần 2:** Wire engine chính vào `parse_vb_file` (AST→FunctionDef/CallEdge, capture cả no-paren/named-args/With-member), `parse_meta.engine="antlr"`, CLI `--vb6-parser-engine auto|antlr|regex`, regex demote thành fallback per-file.
4. **Tuần 2-3:** Các workstream engine-independent (giữ nguyên từ Phase 1 cũ): resolver .vbp + module registry + arity + POSSIBLE_CALLS + Class-ID `@rel_path` + cạnh CONTAINS/IMPLEMENTS + observability + bump `PARSE_CACHE_VERSION`.
5. **Cuối:** re-sync, xác minh `graph_mcp.find_callers` trên hàm mẫu fixture.

**Verdict giữ nguyên: CAUTION** — nhưng khuyến nghị #1 (harden regex) được **rút xuống thành fallback-only**, ANTLR (khuyến nghị biến thể D2 — Java worker ProLeap) lên làm engine chính của extraction. Điều kiện sine qua non: fixture-first baseline, resolver cùng scope, timebox spike.

## Sources

- [joannefan/tree-sitter-vb6](https://github.com/joannefan/tree-sitter-vb6) — grammar tree-sitter VB6 (1 commit, scaffold)
- [grammars-v4/vb6/VisualBasic6Parser.g4](https://github.com/antlr/grammars-v4/blob/master/vb6/VisualBasic6Parser.g4) — ANTLR grammar VB6 chính thức
- [uwol/proleap-vb6-parser](https://github.com/uwol/proleap-vb6-parser) — nguồn gốc grammar, test trên MSDN + apps thực tế
- [vb6-antlr4 (npm)](https://www.skypack.dev/view/vb6-antlr4) — bản đóng gói sẵn của cùng grammar
- [TTC 2023 Paper](https://transformationtoolcontest.github.io/2023/TTC_2023_paper_1.pdf) — đánh giá độc lập độ phủ grammar
- [CodeAnt-AI/tree-sitter-vb-dot-net](https://github.com/CodeAnt-AI/tree-sitter-vb-dot-net) — grammar VB.NET (đang dùng cho vbnet)


---

## Close-loop — kết cục sau triển khai (2026-09-17, plan 260917-1200)

Prediction report này drove plan `260917-1200-vb6-antlr-call-graph` (D2 Java
worker + resolver + evidence plane). Kết quả đo thực tế so gate:

| Metric | Gate | Thực tế | Verdict |
|---|---|---|---|
| M1 idioms payload+graph | 100% cạnh kỳ vọng | 47/47 callsite có edge (5/5 idiom M1 payload-level tests xanh) | PASS |
| M2 recall expected-resolvable | ≥80%, 0 drop | **29/29 = 100%**, 0 drop (trap string-literal sạch) | PASS |
| M3 worker parse-success (trừ malformed) | ≥95% | 10/10 = 100%, .frm materialize hoạt động | PASS |
| M4 throughput <2x regex / JVM ≤5s | <2x / ≤5s | **96x (FAIL relative)** / 144 ms (PASS); absolute ~10 ms/file worker-internal | PARTIAL — đề xuất đổi gate sang absolute ≤50 ms/file (owner review) |
| M5 find_callers trả caller kỳ vọng | đúng | graph-contract test assert cạnh CALLS modMain.DoWork→modUtil.CalcTotal + AD-09 incoming-edge test | PASS (row-level; live MCP smoke còn ở runbook) |
| M6 summary log mặc định | engine thật + rate | `[vb6][summary] engine=antlr(10)+regex_fallback(1) ... rate=...` in không điều kiện verbose | PASS |
| M7 không regress | suite xanh | 53 test vb6 mới xanh; các failure tồn tại trước (csharp matrix, dev_ignore) — verified bằng git stash | PASS |

Prediction đúng: regex-only silent-degrade là nguyên nhân gốc; ANTLR-first D2
giải được call graph. Sai lệch: prediction report claim "A2: v3.0.0 trên
Maven Central" — sai (404, phải vendor); M4 relative gate không khả thi cho
bất kỳ engine có cross-module ASG nào (đã reframe ở benchmark-report.md).
