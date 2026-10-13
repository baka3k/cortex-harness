# MCP single-channel JSON envelope v2.0 — 2026-10-13

[inline:log-writer] — session material nhưng toàn bộ decision/impact đã có
trong plan + review artifact; viết trực tiếp thay vì delegate.

## Context

Agent LLM chạy trên client chỉ render `content` không đọc được dữ liệu tool
(v1 để stub `"Success: N items. Read structuredContent.data."` trong content,
data nằm ở `structuredContent.data`) và tự curl thẳng JSON-RPC endpoint
(:8788/:8789). Mirror full JSON vào cả hai kênh bị loại vì duplicate token ở
client forward cả hai (ZCode). User chốt: single channel, mọi tool một shape.

## Change

- `cortex_harness/mcp_contract.py` — contract `2.0`; `result_envelope_json()`
  là điểm serialize duy nhất (envelope 8 field:
  contract/contractVersion/tool/message/ok/data/error/truncation); structural
  cap env `MCP_CONTENT_MAX_CHARS` (default 20k, `0` = tắt) với shave-loop
  chống JSON-escape inflation; error path giữ `data=null`, bound
  message/details (500 chars / 50 items), drop details khi vẫn vượt cap;
  alias `tool_error`→`tool_execution_error`, `validation_error`→
  `invalid_parameters`; `normalize_error` giữ message từ plain-text error
  results (fastmcp convert exception trước khi tới middleware).
- `code-tiny/mcp/tool_error_middleware.py` — `wrap_tool_result` là điểm
  assemble chung; `ToolEnvelopeMiddleware` cho direct-served mode; bỏ dead
  params `example_for`/`backend`.
- `code-tiny/mcp/unified_mcp.py` — `_wrap_dispatch_result` delegate;
  `on_call_tool` try/except: exception từ direct tool giờ ra envelope
  (trước đây thành plain-text ToolError).
- `doc-tiny/mcp_graph_rag.py` `_standard_tool`, `code-tiny/mcp/fastmcp_server.py`,
  java/cplus/android backends — cùng v2.
- `code-tiny/testtool/mcp_client.py` content-first; `mcp_batch_report.py`
  validator v2 (thay rule content ≤1000 chars, reject `structuredContent`).
- `requirements.txt` pin `fastmcp==4.0.4`.
- Docs: `docs/mcp-output-contract.md` rewrite v2 + mục "Lịch sử v1 → v2".

## Impact

- **Breaking** cho client đọc `structuredContent` (wire không còn field này).
  Client render `content` (ZCode, agent LLM) đọc được trọn dữ liệu — mục tiêu
  gốc. Test set 122 passed; live smoke PASS cả 2 server (5 case + message
  preservation). Risk: trung — backend servers direct-served giờ cùng v2 nên
  không còn half-contract; external clients enroll qua `dev mcp integrate`
  cần biết data nằm ở `content[0].text` (đã ghi trong contract doc).

## Decision

Single channel thay vì mirror hai kênh: mirror làm duplicate data trong ZCode
(chính nơi đang hoạt động tốt). Bỏ `structuredContent` chấp nhận mất kênh
machine-readable theo MCP spec — không có consumer thực nào đọc nó ngoài
testtool first-party (research xác nhận). Cap đặt ở server với truncation có
cấu trúc (không byte-cut) để JSON luôn hợp lệ. Thay vì 25 tool edit trong
`fastmcp_server.py`, dùng một middleware envelope tái dụng serializer.

## References

- plan: ./docs/plans/261013-0000-mcp-single-channel-json-contract/plan.md
- research: ./docs/plans/261013-0000-mcp-single-channel-json-contract/research/repository-findings.md
- commit: fdbb9c07
- test set: tests/test_mcp_output_contract.py (15 test v2), 6-file minimal
  suite per docs/mcp-output-contract.md
- cross-refs: docs/plans/260828-1508-multi-instance-fanout-default/plan.md,
  docs/plans/260728-1500-testtool-runall-error-tracing/plan.md
