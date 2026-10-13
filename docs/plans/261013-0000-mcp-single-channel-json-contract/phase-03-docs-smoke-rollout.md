# Phase 03 — Docs rewrite + cross-plan refs + live smoke

**Goal:** chuẩn docs phản ánh v2 (không còn quy tắc "content MUST ngắn"),
cross-plan ghi nhận, live smoke chứng minh envelope identical trên cả hai
server.

## Steps

1. Rewrite `docs/mcp-output-contract.md`:
   - bảng "Phân lớp của response" (:28-33): `content` = ENVELOPE_JSON duy
     nhất; `structuredContent` —removed; `isError`/`_meta` giữ nguyên;
   - 3 ví dụ wire (success có data :43-77, empty :93-116, error
     :123-150) → JSON envelope v2 theo đúng shape plan.md;
   - template code (:206-253): FastMCP `ToolResult` + MCP SDK
     `CallToolResult` đều `content=<envelope json>`, không
     structured_content;
   - section "Quy tắc cho client và report" (:451-461): đọc `isError`
     trước → parse JSON `content[0].text` → `data`/`error`; bỏ rule
     "ưu tiên structuredContent"; THÊM 1 dòng cho client ngoài được enroll
     qua `dev mcp integrate` (Claude Desktop/VS Code/Cursor — red-team
     F7): data nay nằm trong `content[0].text`, không còn
     `structuredContent`;
   - cập nhật khối "Bộ test tối thiểu" (:420-429) khớp bộ test mới của
     plan (thêm batch-report + scope-block — red-team F14);
   - thêm mục "Lịch sử v1 → v2": lý do đảo chính policy (v1 chống
     JSON-string-do-stub; v2 single-channel chủ đích, cap có cấu trúc);
     giữ nguyên bảng error codes và checklists (cập nhật dòng liên quan
     :383-393).
2. Docs phụ: `code-tiny/testtool/mcp-batch-report-guide.md` (:255-354 có
   refs thật) — sửa theo v2. `docs/specs/mcp.md` và `code-tiny/mcp/Readme.md`:
   red team xác nhận ZERO refs structuredContent — chỉ verify + thêm nhắc
   v2 nếu có chỗ tự nhiên (red-team F13, không tạo việc).
3. Cross-plan refs (mỗi plan 1 dòng trong relatedPlans):
   - `docs/plans/260828-1508-multi-instance-fanout-default/plan.md`;
   - `docs/plans/260727-testtool-mcp-coverage-and-ux/plan.md`.
4. Live smoke (checklist "sửa MCP" của contract doc):
   - `.venv/bin/python cortex_harness/dev.py mcp start --force-restart
     --project-dir <project>`; xác nhận process mới load code mới;
   - `.venv/bin/python scripts/mcp-lifecycle.py doctor`;
   - qua testtool (hoặc curl JSON-RPC) gọi cả 8788 và 8789: (1) success
     có data (search_functions), (2) success empty (query không khớp),
     (3) invalid input error, (4) capability error; (5) một query lớn
     chạm cap → truncation block; mỗi case assert: JSON parse được, đủ 8
     field, không `structuredContent`, envelope identical giữa 2 server;
   - đo phân bố kích thước response trước/sau trên bộ query smoke (ghi số
     liệu vào report) — red-team F9: nếu query `content_mode=raw` thường
     vượt 20k → quyết định tune `MCP_CONTENT_MAX_CHARS` dựa trên số liệu,
     không cảm tính;
   - xác nhận ZCode render content JSON đầy đủ không clip ở size 20k
     (red-team F8 — đã thấy render tốt ở size nhỏ trong session planning;
     kiểm chứng lần cuối ở size gần cap);
   - so sánh live output trước/sau về field/type/kích thước (ghi vào
     report).

## Test scope (mapping)

| Criterion | Check |
| --- | --- |
| AC2 — 4 tình huống × 2 server identical envelope | live smoke checklist |
| AC3 — truncation live | case (5) |
| AC5 — docs v2 không còn "content MUST ngắn" | grep docs + review |
| Zero structuredContent trên wire | grep trong smoke output |

Expected size: **M** (smoke manual/doctor, không thêm unit test mới ngoài
migrate đã làm ở phase 02).

## Acceptance

- Live smoke 5/5 case PASS cả hai server, bằng chứng trong report.
- `grep -rn "structuredContent" docs/ code-tiny/mcp/` chỉ còn trong mục
  "Lịch sử v1 → v2" và fallback legacy có ghi chú.
- Two plans cross-referenced.
