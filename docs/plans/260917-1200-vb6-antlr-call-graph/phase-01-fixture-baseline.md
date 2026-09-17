# Phase 01 — Fixture Corpus + Baseline + Golden Tests

> Gate ra: baseline metrics ghi nhận (số 0 tham chiếu); `expected.json` chốt. **Không đổi production code trong phase này.**

## Mục tiêu

Dụng cụ đo trước khi sửa — mọi claim cải thiện về sau đều so với số của phase này. Theo convention `tests/fixtures/cplus_semantic_calls/` (corpus + `expected.json` golden).

## Tasks

### 1.1 Dựng fixture corpus `tests/fixtures/vb6-application/`

Một "project VB6" tối thiểu nhưng phủ các idioms gây miss (mỗi file có comment đánh dấu case):

```text
tests/fixtures/vb6-application/
├── Sample.vbp                  # Module=/Class=/Form=/Object= lines
├── modMain.bas                 # Attribute VB_Name = "modMain" (khác tên file 1 file để chứng minh module-name semantics)
│                               # Public Sub DoWork() — gọi: no-paren, Call x, MsgBox "x", line continuation `_`,
│                               # cross-module unqualified call tới modUtil.CalcTotal
├── modUtil.bas                 # Public Function CalcTotal(a, b) + Private HelperSub() (chỉ gọi trong module)
├── clsOrder.cls                # Property Get/Let, method ProcessOrder, Implements IShip
├── clsShip.cls                 # Implements IShip → Ship_Order
├── IShip.cls                    # interface class (chỉ stub Sub/Function) — đích Implements THẬT (red-team F5)
├── malformed.bas                # file hỏng cú pháp có chủ đích — chứng minh 1 file không chìm cả batch (red-team F3)
├── frmMain.frm                 # designer block (VERSION 5.00 / Begin VB.Form ... End) + Attribute VB_Name = "frmMain"
│                               # Sub Form_Load gọi modMain.DoWork + Me.Refresh + no-paren call
├── frmAbout.frm                # form thứ 2, có hàm trùng tên với frmMain (TestSameName) → case ambiguous
└── latebas.bas                 # Dim x As Object → x.LateBound 5 (case late_bound)
```

Case matrix (phải phủ hết, ghi vào `expected.json`):

| Case | Ví dụ | Kỳ vọng phân loại |
|---|---|---|
| no-paren sub call | `DoSomething 1, 2` | resolved CALLS |
| no-paren không args | `InitData` | resolved CALLS |
| `Call` statement | `Call InitData` | resolved CALLS |
| builtin không cần resolve | `MsgBox "x"` | POSSIBLE_CALLS `resolution_status=external` |
| line continuation | `ProcessOrder _` ⏎ `42` | resolved CALLS, đúng line |
| cross-module unqualified | `CalcTotal 1, 2` từ modMain | resolved CALLS → modUtil.CalcTotal |
| qualified | `modUtil.CalcTotal(1, 2)` | resolved CALLS |
| module-private | `HelperSub` gọi ngoài modUtil | unresolved → POSSIBLE_CALLS |
| same-name ambiguous | `TestSameName` gọi unqualified từ .bas | ambiguous → POSSIBLE_CALLS multi-target |
| property | `ord.Total = 5` / `x = ord.Total` | CALLS tới Property Let/Get |
| Implements dispatch | `Dim s As IShip: s.Ship_Order` | ambiguous/possible (static); IMPLEMENTS clsShip→IShip edge tồn tại |
| late-bound | `x.LateBound 5` với `As Object` | POSSIBLE_CALLS `resolution_status=late_bound` |
| form default instance | `frmAbout.Show` | resolved → frmAbout.Show (method form) hoặc possible |
| With block | `With ord` ⏎ `.ProcessOrder` | resolved |
| string-literal trap | `"Call Fake(x)"` trong chuỗi | KHÔNG tạo call |

### 1.2 `expected.json` golden schema

Theo pattern `cplus_semantic_calls/expected.json`, mở rộng:

```json
{
  "files": {
    "modMain.bas": {
      "module_name": "modMain",
      "functions": [{"name": "DoWork", "kind": "sub", "arity": 0, "start_line": 5}],
      "callsites": [
        {"caller": "modMain.DoWork", "callee_name": "DoSomething", "line": 7,
         "expect_resolution": "resolved", "expect_callee": "modMain.DoSomething"}
      ]
    }
  }
}
```

### 1.3 Baseline runner `tests/test_vb6_baseline.py`

- Gọi `parse_vb_file(...)` + `resolve_calls(...)` hiện tại (engine regex) trên corpus.
- Đo và IN ra: tổng call sites kỳ vọng (theo expected.json) vs số regex bắt được vs số resolved — **số 0 tham chiếu**.
- Assert mức tối thiểu chỉ để test không rot (không assert chất lượng — chất lượng hiện tại kém là điều đã biết).
- Ghi baseline vào `docs/plans/260917-1200-vb6-antlr-call-graph/baseline.md` (số liệu + ngày).

### 1.4 `benchmark_vb6_parse_quality.py` (skeleton theo `benchmark_cplus_parse_quality.py`)

- Manifest-driven corpus, đo latency percentiles p50/p95, throughput files/s.
- Gate khả năng chạy; số chính thức thu ở phase 06.

## Lưu ý mẫu số M2 (red-team F4)

Corpus giàu case đặc biệt (external/late_bound/ambiguous chiếm tỉ trọng lớn) → M2 tính **recall trên tập expected-resolvable** (các case `expect_resolution=resolved`), KHÔNG phải % trên tổng callsites. Bổ sung ≥6 call thường xuyên resolvable nữa (các cặp modA↔modB gọi nhau đủ loại sub/function/property) để mẫu số ổn định.

## Định nghĩa xong

- [ ] Corpus tồn tại, `.vbp` hợp lệ, ≥15 cases trong matrix
- [ ] `expected.json` chốt (review kỹ — đây là contract cho mọi phase sau)
- [ ] `baseline.md` có số 0 (call sites missed %, resolved %)
- [ ] `tests/test_vb6_baseline.py` xanh trong CI hiện hành
