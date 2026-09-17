# Phase 02 — Designer Block Retained + controls[] Plane + Event Wiring Metadata

> Gate ra: M3 (control tree đúng cha/con trên fixture .frm; handler gắn `vb6_event` ≥90%; 0 false-match); quyết định cuối AD-02 (giữ vs fallback strip).

## Tasks

### 2.1 Adapter: giữ designer block

- `materializeDesignerModule()` (`vb6_antlr_adapter.py:47`): đổi hành vi mặc định thành **copy nguyên file** (đổi đuôi .cls, KHÔNG blank phần trước `Attribute VB_Name`) — line numbers giữ nguyên 1:1. Evidence: digest §5 (designer-in-.cls parse `ok:true`, `start_line:6`, `call_line:7`).
- **Worker: bỏ cửa sổ scan 60 dòng** khi tìm `Attribute VB_Name` (`Vb6Worker.java:963-966` + lookup :311) → scan toàn bộ nội dung file (red-team F11 — High). Không sửa thì .frm thật có designer dài mất toàn bộ procedure extraction ("module not registered").
- Env-guard `VB6_ANTLR_STRIP_DESIGNER=1` để quay lại hành vi strip cũ (fallback AD-02) nếu golden line-number fail trên .frm thật. Khi strip active: set `parse_meta.designer_stripped=true` + summary line ghi chú (red-team F13 — controls[] rỗng phải quan sát được, không im lặng).
- Fixture .frm THẬT: mở rộng fixture form hiện có với nested control (`Begin VB.TextBox` trong `Begin VB.Frame`), `BeginProperty`/`EndProperty`, `Tab(n).Control(m)`, **và designer block >60 dòng** (red-team F11 regression guard). Golden test line-number: procedure trong .frm phải giữ `start_line` đúng vị trí file gốc (mở rộng pin contract:101).

### 2.2 Worker: controls[] plane

- Trong `filePayload` (chỉ khi file kind frm/ctl/pag — biết qua `FileEntry`): ctx-walk xuống `controlProperties` từ module ctx (g4:97-126), serialize:
  - `controls[]`: `{name (cp_ControlIdentifier), type (cp_ControlType), parent (name control cha, rỗng nếu root), index (số trong tên `Command1(2)` nếu có), properties: {caption/text/... từ cp_SingleProperty, giới hạn set key hữu ích: Caption, Text, Name, Index, TabIndex}, line}`.
  - Nested: đệ quy theo `cp_NestedProperty`/`controlProperties` con; `Tab(n).Control(m)` mapping giữ dạng raw property (không resolve link — ngoài scope).
- Parse-tree walk dùng `VisualBasic6Parser` ctx trực tiếp (không cần ASG element — control không có ASG model).

### 2.3 Python: event-wiring metadata

- `vb_analyzer_base.py` (VB6 path): hàm `match_event_handlers(controls, functions)`:
  - `VB6_EVENT_SUFFIXES` module-level tuple (Open Questions Q3 — gồm Timer; prefix đặc biệt `Form`, `MDIForm`, `UserControl`; red-team F9) + control đặc biệt `Form` cho `Form_Load` v.v.
  - Match: tên sub `^(?P<ctrl>.+)_(?P<evt>SUFFIX)$` case-insensitive; **longest control-name-first** để xử lý control name chứa `_` (`cmd_OK_Click` → ctrl=`cmd_OK`, evt=`Click`).
  - Anti-false-positive: `evt` PHẢI thuộc suffix set (nên `Command1_Clicked` không match), `ctrl` PHẢI tồn tại trong controls[] của cùng module (hoặc `Form`/`MDIForm`/`UserControl` khi file kind tương ứng); handler trong .bas không bao giờ match (không có controls).
  - Kết quả: đính vào function row: `vb6_event: "<Control>.<Event>"`, `vb6_control_type`, và prepend note/summary: `"Event handler for Command1.Click (Form1)"` khi note đang rỗng. **Field `vb6_event`/`vb6_control_type` phải thêm vào dataclass `FunctionDef` với default rỗng** (red-team F4 — hydration drop unknown keys).
- Qdrant mapping (in-scope, vb_analyzer_base.py:1249-1497): function rows chứa `vb6_event`/`vb6_control_type`; class (form) rows chứa `controls` (danh sách `{name, type}` tóm gọn). Embedding text tự hưởng qua note (research §2).
- **KHÔNG** tạo cạnh/cạnh sự kiện Neo4j, **KHÔNG** node mới (AD-01/AD-06). Graph query "click flow" = semantic search tìm handler theo event text → `trace_flow` từ handler function.

### 2.4 Tests

- Contract: controls[] populated đúng cha/con + properties cho fixture .frm; `vb6_event` trên handler gài; 0 false-match trên các sub thường fixture (ví dụ `Helper_Click_Validate` hoặc sub trùng pattern nhưng control không tồn tại).
- Golden: line-number .frm mở rộng (2.1) xanh cả hai mode strip/keep (keep mặc định).
- Resolver/graph contract KHÔNG đổi ở phase này (vb6_event là payload-level).

## Định nghĩa xong

- [ ] M3: control tree đúng; handler annotation ≥90% planted; 0 false-match
- [ ] AD-02 chốt: keep-designer là default, golden line-number .frm thật xanh; nếu fallback strip → ghi vào plan.md exclusions
- [ ] Qdrant point payload (fake-driver) chứa `vb6_event` + note enriched
- [ ] Không file nào ngoài tools/vb + tests thay đổi (M7 spot-check)
