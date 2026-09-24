# Phase 02 — Worker planes + hydration + synthetic fixtures

> Gate ra: M4 (redim/static/global trên corpus + fixture) + payload contract pins xanh; jar rebuild sạch theo mtime-stamp path.

## Tasks

### 2.1 Spikes (chặn rủi ro unknown trước khi serialize)

- **S1 Global mapping**: parse fixture `.bas` có `Global x As Integer` → check **`VisibilityEnum.GLOBAL`** cụ thể trong ASG (GLOBAL là visibility token riêng, `g4:824-828`). Nếu worker hiện normalize mất → preprocess rewrite `Global` → **`Public`** (KHÔNG `Public Dim` — `variableStmt` không chấp nhận visibility+DIM, sẽ lỗi parse cả module, `g4:600-601`), 1:1 line, set `parse_meta.global_rewritten=true`.
- **S2 Local Const/Dim**: xác nhận `procedure.getVariables()` chứa local `Const`/`Dim`. Nếu không → ctx-walk `variableStmt`/`constStmt` trong procedure scope.

### 2.2 Planes mới trong `filePayload` (`Vb6Worker.java` — ctx-walk, không pass parse mới)

Mọi plane **phải mang owner `proc`** (symbol name của procedure chứa) — attribution Function→X ở P3/P4 phụ thuộc nó, không line-range join (red-team H4):

- `instantiations[]`: `{proc, name, line, call_type:"NEW"}` — bỏ filter `moduleNames` cho case `New` (`Vb6Worker.java:765-771`: filter chỉ áp receiver-prefix, tách nhánh New trước).
- `with_targets[]`: `{proc, expr_raw, line, block_end_line}` (ctx-walk `withStmt`, g4:620-621) — `block_end_line` cần cho nested With (P3 gắn member theo khoảng block, không theo "đến cuối proc").
- `ui_access[]`: `{proc, receiver_raw, member, access:read|write, via_with:bool, line}` — từ member access rows hiện có, thêm assignment (`.X = v`) và reference; không đụng calls[].
- `redim[]`: `{proc, name, preserve:bool, line}` (ctx-walk `redimStmt`, g4:461-463).
- `variables[]` += `is_static` (nguồn theo S2), `is_global` (module-level + S1), `with_events:bool` (ctx `WithEvents` — regex path bắt rồi vứt ở `vb_common.py:488`, ANTLR path cần flag riêng).
- `functions[]` += `param_types[]`, `return_type` (phục vụ USES_TYPE + COM receiver P3).
- `controls[]` giữ nguyên (260917-1628 phase-02).

### 2.3 Adapter + hydration (red-team H3 — planes mới bị drop nếu chỉ đụng adapter)

- `vb6_antlr_adapter.py`: deserialize planes mới thẳng payload (mirror planes có sẵn).
- **`vb_analyzer_base._hydrate_payload` (:320-337): thêm whitelist entry cho 4 planes mới** (raw-dict passthrough như `controls`, hoặc dataclass như `declares`) — không thì adapter emit gì cũng mất tại hydration, silent.
- Bump `PARSE_CACHE_VERSION` (`vb_common.py:25`) — cache cũ tự invalidate.

### 2.4 Fixtures synthetic (anchor 0-corpus: Global, WithEvents, COM, bang đều 0 occurrence)

- Thêm fixture `.bas`/`.frm`/`.cls` trong `tests/fixtures/` phủ: `Global`, `WithEvents` + raise/handler, `As ADODB.Recordset` + method call, `obj!Field` bang access, `Static` local, `ReDim Preserve`, `With` lồng control + form nav, `Class_Initialize/Terminate`, `Friend` proc, nested `With`.
- Fixture .frm thật mở rộng: pattern `With <form> … .Show` (từ `addbook.frm:529`) copy vào fixture — golden không phụ thuộc corpus ngoài.

### 2.5 Tests + build + pins

- `test_vb6_antlr_worker_contract.py`: pins planes mới (constant đã chuyển assert-equality ở P1); `test_vb6_qdrant_payload.py` cập nhật.
- **Sửa pin đỏ có sẵn**: `test_vb6_engine_dispatch.py:38` đang pin `vb-family-v2026-09-17-4` vs actual `09-18-1` (đỏ trước plan này) — đổi theo version mới trong lần bump này, ghi exclusion "pre-existing red".
- Jar rebuild qua mtime-stamp path; golden hiện có 100% không regress (ctx-walk thuần đọc, line-number không đổi).
- Corpus scan script: đếm `redim[]` rows trên corpus = 26 site (M4); `with_targets[]` có `block_end_line` cho cả nested.

## Định nghĩa xong

- [ ] S1/S2 có kết luận ghi vào plan.md (unknown → resolved)
- [ ] Planes mới sống sót tới analyzer qua hydration (test bridge adapter→analyzer, không chỉ adapter)
- [ ] Corpus: 26 ReDim site captured; static/global/with_events flags đúng fixture synthetic
- [ ] PARSE_CACHE_VERSION bumped; cả 2 pins xanh
