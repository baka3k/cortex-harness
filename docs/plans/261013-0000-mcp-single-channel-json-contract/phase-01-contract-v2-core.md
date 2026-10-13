# Phase 01 — Contract core v2.0: envelope serializer + cap + bỏ structuredContent

**Goal:** mọi tool response của CẢ HAI server (graph 8788 qua
`_wrap_dispatch_result`, mind 8789 qua `_standard_tool`) xuất ra đúng một
TextContent chứa ENVELOPE_JSON (shape trong plan.md), không còn
`structuredContent`, kèm structural size cap.

## Steps

1. `cortex_harness/mcp_contract.py`:
   - `MCP_CONTRACT_VERSION` → `"2.0"` (hằng tại :19x — giữ `MCP_CONTRACT_NAME`
     nguyên).
   - `result_summary` (:233-259): giữ nguyên logic đếm, chỉ đổi text sinh ra
     thành `"Success: N results."` / `"Success."` — bỏ đuôi
     `"Read structuredContent.data."`. Hàm này thành source của field
     `message`.
   - Thêm hàm `result_envelope_json(tool: str, *, ok: bool, data, error,
     message: str) -> str`:
     - dựng dict `{contract, contractVersion, tool, message, ok, data,
       error, truncation: null}` rồi `json.dumps(..., ensure_ascii=False)`;
     - serializer robustness (red-team F4): payload có thể chứa kiểu non-
       native (datetime/set/Path) — v1 trước đây được
       `_serialize_to_jsonable` (pydantic) cứu; v2 PHẢI có fallback tương
       đương (try `json.dumps`, bắt `TypeError` → serialize lại với
       `default=str`) để không biến tool cũ thành crash;
     - áp cap: đọc `MCP_CONTENT_MAX_CHARS` (default `20000`, `0` = tắt) một
       lần ở module level;
     - nếu `len(json) > cap`: dựng lại với
       `data = {"truncated": true, "preview": <min(cap - overhead, len)
       ký tự đầu của data JSON>, "note": "<hint thu hẹp query>"}` và
       `truncation = {truncated: true, reason: "content_cap_exceeded",
       cap_chars: N, hint: "<hint>"}`. Overhead tính từ template envelope
       ĐÃ DỰNG ĐỦ (có block truncation + note), clamp
       `max(0, cap - overhead)`; `cap < overhead` → `preview: ""` (red-team
       F2). Kết quả LUÔN là JSON hợp lệ;
     - error path (`ok=false`): `data=null`; `error` CŨNG chịu cap — list
       trong `error.details` (`violations` :113-114, `missing_relationships`
       :122-138) cắt ở 50 phần tử + `details_truncated: true` (red-team
       F3);
     - payload string thô (unwrap fallback :1009-1013 trả `raw_text`) →
       wrap `data = {"text": <string>}` (red-team F11).
   - `normalize_success` / `normalize_error` / `result_meta`: giữ nguyên
     output shape — chúng là building block.
2. `code-tiny/mcp/unified_mcp.py` — `_wrap_dispatch_result` (:985-1037):
   - nhánh error (:1019-1027): `envelope = normalize_error(payload)`;
     `content = result_envelope_json(tool_name, ok=False, data=None,
     error=envelope["error"], message=envelope["error"]["message"])`;
     `ToolResult(content=..., meta=meta, is_error=True)` — KHÔNG truyền
     `structured_content`.
   - nhánh success (:1029-1037): giữ logic strip `ok` khỏi payload;
     `content = result_envelope_json(tool_name, ok=True, data=envelope
     ["data"], error=None, message=result_summary(payload, ok=True))`;
     `ToolResult(content=..., meta=meta, is_error=False)` — KHÔNG truyền
     `structured_content`.
   - Lưu ý: FastMCP `ToolResult` hiện đang nhận `content` dạng string và
     tự bọc thành TextContent — giữ đúng pattern này, không tự dựng list
     content.
   - **RED-TEAM F1 (High)** — exception từ direct tool đang bypass envelope:
     `_ProxyMiddleware.on_call_tool` không có try/except quanh
     `await call_next(context)` (:1054) nên `ValueError` của tool direct
     (ví dụ `tool_list_parsers` :1587) bị FastMCP đổi thành ToolError →
     plain-text isError tại protocol layer (`server.py:1567-1577`), KHÔNG
     qua `_wrap_dispatch_result` → wire không envelope, vi phạm AC1. Fix:
     bọc `call_next` trong try/except, exception → `normalize_error(exc)`
     → envelope v2 (`is_error=True`). (Mind server đã an toàn: decorator
     catch-all.)
3. `doc-tiny/mcp_graph_rag.py` — `_standard_tool` (:1371-1423), 5 tool
   mind server build `mcp.types.CallToolResult` trực tiếp:
   - success + error branch (:1385-1389) chuyển sang `result_envelope_json`
     chung; `CallToolResult(content=[TextContent(type="text",
     text=<envelope_json>)], isError=..., _meta=result_meta(tool))` —
     KHÔNG set structuredContent;
   - giữ per-tool `max_passage_chars` cuts (:1542, :1682) nguyên vẹn —
     chúng cắt TRƯỚC khi vào envelope.
4. `code-tiny/mcp/tool_error_middleware.py` (:108-112 — exception path,
   chỉ install trên standalone `fastmcp_server.py:2772`): cho output đi
   qua shared serializer (hiện path này còn thiếu `_meta` — nhân tiện
   chuẩn hóa). `fastmcp_server.py` standalone (~25 tool trả raw dict,
   default port 8788, dev.py KHÔNG launch — red-team F6: sửa per-tool tốn
   phí và success path vẫn tự sinh structuredContent qua auto-generate
   `base.py:422-426`): conform bằng MỘT result-wrapping middleware tái dụng
   shared serializer (không sửa 25 tool), kèm 1 wire test; nếu audit xác
   nhận không còn entry point sử dụng → ghi đề xuất retire vào report
   (quyết định retire thuộc follow-up plan).
5. Pin `fastmcp==4.0.4` trong `requirements.txt:17` (red-team F10 — hành
   vi `to_mcp_result`/`exclude_none` chưa pin là nền của suppression).
6. **Verification FastMCP** — ĐÃ VERIFY trên fastmcp 4.0.4
   (`.venv/.../fastmcp/tools/base.py:171-201` + researcher repro):
   `structured_content=None` → wire KHÔNG hề có field `structuredContent`
   (outgoing dump dùng `exclude_none`, `mcp/shared/peer.py:57`); auto-
   generate structuredContent cho dict return (`base.py:422-426`) bị bypass
   vì middleware luôn wrap — middleware là load-bearing. Việc còn lại: test
   gọi `_wrap_dispatch_result(...).to_mcp_result()` + `_standard_tool`
   assert wire KHÔNG có `structuredContent` và `isError` round-trip đúng.
   Lưu ý: `resultType: "complete"` luôn xuất hiện ở wire (mcp_types 2.2.0)
   — ghi nhận, không phải lỗi.

## Test scope (mapping theo test-budget.md — tier Always: public API contract)

Mapped criteria → test (rewrite `tests/test_mcp_output_contract.py`):

| Criterion | Test |
| --- | --- |
| AC1 — envelope v2 đủ 8 field, JSON parse được + wire KHÔNG có field `structuredContent` (exclude_none) | 1 param test trên `to_mcp_result()` (gộp 2 mapping cũ — red-team F15) |
| Quy tắc 4 — empty là success, collection rỗng đúng kiểu | 1 test |
| Quy tắc 5 — error path: ok=false + error 4 field + isError + message = error.message | 1 param test (2 case: invalid input, capability error) |
| F1 (High) — direct tool raise → vẫn envelope v2 + isError=true | 1 test (tool raise ValueError qua `on_call_tool`) |
| AC3 — truncation: cap nhỏ (monkeypatch) → valid JSON + truncation block + preview | 1 param test (3 case: thường, `cap < overhead` → preview "", env `0` = tắt) |
| F4 — payload non-native type (datetime/set) không crash | 1 test serializer robustness |
| F11 — payload string thô → `data = {"text": ...}` | gộp vào test strip `ok:true` (:1029) |
| `message` không chứa chuỗi `"structuredContent"` | 1 test |
| `_standard_tool` (mind) wire không structuredContent + isError round-trip | 1 test (dùng FakeMcp theo convention `register_tools`) |
| Standalone middleware (F6) — wire không structuredContent | 1 wire test |

Expected size: **M** (~9-10 test functions, table-driven nơi được).

## Acceptance

- `pytest tests/test_mcp_output_contract.py` xanh.
- `_wrap_dispatch_result` không còn tham chiếu `structured_content=`.
- Không file nào khác trong repo import symbol mới chưa có.
