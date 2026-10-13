# Phase 02 — Consumers & tests migration sang envelope v2

**Goal:** mọi first-party client/test đọc `content[0].text` (JSON) thay vì
`structuredContent`; toàn bộ test suite liên quan xanh với contract v2.

## Steps

1. `code-tiny/testtool/mcp_client.py` — `call_tool` (:128-160):
   - thứ tự mới: (a) đọc `isError` trước (giữ nguyên rule); (b) khi
     `isError=true` → raise `MCPError` với `error.message` lấy từ JSON
     content (fallback text thô như hiện tại :144-149); (c) khi success →
     `json.loads` từng text part, hợp nhất envelope (nếu nhiều part — hiện
     chỉ có 1); trả `envelope["data"]` kèm giữ `message`/`truncation` cho
     report;
   - nhánh đọc `structuredContent` (:151-152) thành legacy fallback cuối
     (client cũ trỏ server cũ), kèm comment ghi rõ v2.
2. `code-tiny/testtool/mcp_batch_report.py`: đổi extract
   (`_structured` :211, `classify_response` :236) — summary lấy
   `envelope.message`, status từ `envelope.ok`/`isError`, data từ
   `envelope.data`; KHÔNG còn in cả content lẫn structured (rule
   "không lặp" của contract doc). Đặc biệt:
   - THAY rule "content ≤1000 chars" trong `validate_contract`
     (:297-300) — trực tiếp xung đột v2 (content giờ là full JSON) — bằng
     rule mới: content[0] là JSON parse được + envelope hợp lệ + (nếu có)
     size ≤ cap;
   - bump `CONTRACT_VERSION` (:31) `1.0 → 2.0` ĐỒNG BỘ với
     `mcp_contract.py` (convention từ research: 2 constants phải đi cùng
     nhau);
   - `validate_contract` (:265-339) đổi từ kiểm tra structuredContent sang
     kiểm tra envelope v2.
3. `doc-tiny/mcp_graph_rag.py`: verify KHÔNG còn consumer code đọc
   `structuredContent` sau khi phase 01 bỏ 2 producer kwargs (:1392,
   :1405) — red-team F12 xác nhận file này không parse response nào,
   KHÔNG có việc sửa code ở phase 02.
4. Tests migrate (đổi assert từ `result.structured_content` sang parse JSON
   của `result.content`):
   - `tests/test_mcp_testtool_client.py` — client parse mới;
   - `tests/test_mcp_batch_report.py` — report từ envelope v2;
   - `tests/test_semantic_graph_scope_block.py` — scope block nay đọc từ
     `envelope.data.scope` (shape `data` KHÔNG đổi);
   - `tests/test_unified_mcp_input_coercion.py` — các assert trên
     `ToolResult` của `_wrap_dispatch_result`;
   - `tests/test_unified_contract_doc_paths.py` — nếu assert doc paths v1.
   Inventory đầy đủ theo `research/repository-findings.md` (researcher
   digest) — reconcile trước khi start phase. Các file xác nhận KHÔNG ảnh
   hưởng (không đụng): suites JSON, `outputs/*.md` (historical, cấm sửa
   tay), wiki/Readme/HARNESS_WORKFLOW (zero refs), `scripts/*`,
   `test_framework_mcp_search.py`.
5. `_MCP_FUNCTIONS_JSON` catalog: red team đã grep xác nhận KHÔNG chứa
   guidance stale về structuredContent — audit item CLOSED, chỉ re-check
   nếu catalog đổi trước khi implement.

## Test scope (mapping)

| Criterion | Test |
| --- | --- |
| AC4 — client parse JSON từ content (typical) | 1 test existing migrated |
| AC4 — content không phải JSON (legacy/protocol error) → fallback text không crash | 1 test mới (invalid) |
| AC4 — isError raise với message từ envelope | 1 test migrated (boundary) |
| AC4 — batch report summary/status từ envelope | 1 test migrated |
| Scope block shape không đổi qua kênh mới | 1 test migrated |
| Input coercion assertions v2 | migrated tests (không thêm case mới) |

Expected size: **M** (2 test mới + ~5 file migrate; priority migrate
assertions, không nhân bản case).

## Acceptance

- Bộ test tối thiểu (plan.md) xanh toàn bộ.
- `grep -rn "structuredContent\|structured_content" code-tiny/testtool/
  doc-tiny tests/` (loại trừ `code-tiny/testtool/outputs/` — historical,
  cấm sửa tay — red-team F5) chỉ còn hit ở comment/fallback legacy có
  ghi chú.
