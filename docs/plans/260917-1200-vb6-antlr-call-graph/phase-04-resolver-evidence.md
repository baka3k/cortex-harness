# Phase 04 — Resolver + Two-tier Publication + Graph Integrity

> Gate ra: M2 (≥80% CALLS resolved trên fixture; 0 call site bị drop); Class-ID/CONTAINS/IMPLEMENTS vào graph; M5 (`graph_mcp.find_callers` trả caller kỳ vọng).

## Tasks

### 4.1 ModuleRegistry (python, engine-independent)

`code-tiny/tools/vb/vb6_resolver.py` (file mới, không nhét vào vb_common):

```python
@dataclass
class ModuleInfo:
    module_name: str          # Attribute VB_Name (authoritative), fallback: tên file không ext
    file_path: str            # rel path
    kind: str                 # bas | cls | frm | ctl
    functions: Dict[str, FunctionDef]   # key: lowercase name
    implements_map: Dict[str, List[str]]  # interface -> implementing procedures

class VB6ModuleRegistry:
    @classmethod
    def from_payloads(cls, payloads, vbp_meta) -> "VB6ModuleRegistry": ...
```

- Nguồn module_name: payload FunctionDef cần thêm field `module_name` (extraction điền: ANTLR từ ProLeap; regex path đọc `Attribute VB_Name` — thêm regex 1 dòng `_VB_NAME_RE` trong `parse_vb_file`, rẻ, dùng cho mọi engine).
- `.vbp` parse: `Module=name;file`, `Class=`, `Form=`, `Object=`/`Reference=` (COM refs → ghi worker_meta/parse_meta, không resolve).

### 4.2 Resolver mới (thay `resolve_calls` cho vb6)

`resolve_vb6_calls(registry, calls) -> None` — resolution order (case-insensitive, R5 digest):

1. **Exact qualified** `module.proc` / `proc` với caller_scope match module-local (kể cả Private).
2. **Module-local** (Private + Public cùng module).
3. **Project-public** (.bas Public; .cls/.frm Public qua qualified access; form default instance `frmX.Method` → method của form module).
4. **Arity filter** khi nhiều candidate (dùng `callee_arity` — ANTLR điền; regex path bỏ qua).
5. Kết quả:
   - 1 đích → `callee_id` + `resolution_status ∈ {asg_resolved, name_resolved}` (giữ asg_resolved từ worker nếu có).
   - >1 đích → KHÔNG chọn tùy tiện: `resolution_status="ambiguous"`,ghi `candidate_ids` vào CallEdge.
   - 0 đích + callee_name là builtin/COM (`MsgBox`, `Print`, ...) → `resolution_status="external"`.
   - receiver là biến `As Object`/`As Variant` (tham chiếu registry variables) → `resolution_status="late_bound"`.
- **Đi họ**: `resolve_calls` cũ vẫn dùng cho vbnet/vba/vbscript (guard: `if dialect == "vb6": resolve_vb6_calls(...) else: resolve_calls(...)`).

### 4.3 CallEdge mở rộng

`vb_common.CallEdge` thêm fields (default giữ compat hydrate cache): `call_type: str = ""`, `resolution_status: str = ""`, `candidate_ids: List[str] = field(default_factory=list)`, `site_column: int = 0` (ANTLR có). Bump `PARSE_CACHE_VERSION` (phase 05 cùng PR).

### 4.4 Two-tier publication trong `vb_analyzer_base.py`

Thay block drop `vb_analyzer_base.py:748-754`:

```python
for call in payload["calls"]:
    if call.callee_id and call.resolution_status != "ambiguous":
        calls_rows.append({caller_id, callee_id, call_type, resolution_status})
    else:
        possible_rows.append({
            # site-keyed theo cplus: callsite_site_id pattern; "properties" chỉ chứa cột schema duyệt
            "site_id": callsite_site_id(caller, callee_name, file, line, call_type),
            "properties": {"line": call.call_line, "arity": call.callee_arity,
                            "call_type": call.call_type,
                            "resolution_status": call.resolution_status,   # free-text vb6: asg_resolved/name_resolved/ambiguous/late_bound/external/unresolved
                            "resolution_class": "lexical_candidate",      # CHỈ vocab chuẩn call_evidence.RESOLUTION_CLASSES (F2)
                            "semantic_provider": "vb6_antlr_worker" if engine == "antlr" else "vb6_regex",
                            "candidates": json.dumps(candidate_ids)}})
```

- **Ambiguous multi-target**: 1 POSSIBLE_CALLS edge mỗi candidate (n đích) + cùng site_id — downstream tính được tập ứng viên.
- **External/late_bound/unresolved không có Function đích**: schema POSSIBLE_CALLS là Function→Function → các case này CẦN đích; chọn: (a) tạo node placeholder `Function {name, kind:"external_symbol"}` per project, target các edge туда — quyết định (a), ghi rõ trong code comment; worker_meta đếm số external symbol.
- Ghi qua `code_writer.write_relations_typed` (props arbitrary OK theo digest R3) hoặc `write_possible_calls_with_site` nếu tái dùng được của cplus — chọn API ít friction nhất theo code thực tế lúc implement.

### 4.5 Graph integrity

- **Class-ID**: `vb_common.py:574-575` (+ block close-unbalanced ~688): `class_id = f"{qualified}@{rel_path}"`. Cập nhật mọi nơi tạo ClassDef (2 chỗ) + asdict_class. Kiểm tra `cleanup_neo4j_for_files` xoá theo file hoạt động với Class mới (id giờ chứa path).
- **CONTAINS class→method**: khi build relations_rows, với mỗi Function có `class_name` và payload có class tương ứng → relation `(class_id)-[:CONTAINS]->(fn.symbol_id)`.
- **IMPLEMENTS**: registry implements_map → relation `(cls)-[:IMPLEMENTS]->(iface.symbol_id)` khi interface tồn tại trong payload. **VB6 interface là class thường** → extraction/registry emit class-chỉ-là-đích-Implements thành `InterfaceDef` (AD-10) để cạnh khớp `(Class)→(Interface)` của `_REL_SPECS`; fixture có `IShip.cls` (F5). `Inherits` tương tự cho class.

### 4.6 Tests

- `tests/test_vb6_resolver.py`: từng bước resolution order, arity filter, ambiguous multi-target, late_bound, external.
- `tests/test_vb6_graph_contract.py` (theo pattern cobol_graph_contract): chạy analyzer với graph fake/in-memory (hoặc testcontainers theo hiện hành) → assert CALLS + POSSIBLE_CALLS + CONTAINS + IMPLEMENTS tồn tại đúng cho các case fixture.
- M5 verification tay: sau khi ingest fixture vào dev graph, `find_callers("modUtil.CalcTotal")` phải chứa `modMain.DoWork`.

### 4.7 Incremental edge re-publication (AD-09 — red-team F1 Critical)

Trong `build_call_graph`, khi `incremental=True`:

- Node/write/embed vẫn lọc theo `changed_set` (như hiện tại).
- **Cạnh**: sau resolver, tập re-publish = mọi CallEdge có `caller.file_path ∈ changed_set` **HOẶC** `callee.file_path ∈ changed_set` (cả hai chiều). Vì worker luôn trả payload toàn project (AD-02), dữ liệu nguồn đủ sẵn — chỉ là vấn đề lọc đúng chiều.
- Không làm điều này → mỗi sync incremental DETACH DELETE node file đổi (`incremental_cleanup.py:70-84`) làm mất cạnh từ caller không đổi và không bao giờ dựng lại.

## Định nghĩa xong

- [ ] M2 đo trên corpus: ≥80% resolved, 0 drop (script đo trong test)
- [ ] `resolve_calls` cũ nguyên vẹn cho 3 dialect kia (test registry xanh)
- [ ] Class-ID mới + cleanup incremental không để node mồ côi
- [ ] POSSIBLE_CALLS có site_id/resolution_status; không có direct_resolved từ vb6
- [ ] M5 find_callers trả kết quả đúng
- [ ] Incremental: cạnh incoming từ file KHÔNG đổi được dựng lại sau khi file đích re-sync (test F1)
